"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.

DO NOT INVENT NEW NON-STANDARD ENDPOINTS ON THIS SERVICE. NOT ONE, EVER,
UNDER ANY CIRCUMSTANCE, without the workspace-scoped ACL implications being
explicitly signed off first.

This service's job is OData: `$metadata`, `$filter`, `$select`, `$top`,
`$skip`, `$count`, `$apply` - the protocol, applied to workspace-scoped
collections, gated by the read grant on the path. A bespoke `$name` route
that is not part of that protocol is a byte-for-byte re-implementation of
"what an entitlement means here," made by whoever wrote that one route,
usually while solving a real and sympathetic problem - and every one of them
found here so far (`$inbound-edges`, `$lineage`, `$consumers`, all added and
removed the same week, 2026-09) answered its question by walking outside the
workspace named in the path and reporting on objects in OTHER workspaces,
elided by name but not by existence. That is not a bug in any one of them;
it is what "give me an answer that isn't scoped to one workspace" costs by
construction, and it is a design decision this codebase does not get to make
route-by-route. If a feature needs a cross-workspace read, that is a product
and security decision made above this file, not a `$` prefix invented in it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../opteryx-core")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../opteryx-catalog"))
)
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../opteryx-access"))
)

import base64
import datetime as dt
import decimal
import hashlib
import json
import time
import traceback
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from typing import List
from typing import Optional

import opteryx
import opteryx_access
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response

# Some opteryx releases may not export OpteryxConnector (e.g. in lightweight installs).
# Provide a minimal stand-in so imports (and docs generation) still work.
try:
    from opteryx.connectors import OpteryxConnector
except ImportError:  # pragma: no cover
    OpteryxConnector = type("OpteryxConnector", (), {})

from opteryx.connectors import set_workspace_resolver
from opteryx.connectors import set_workspace_settings_resolver
from opteryx_catalog import OpteryxCatalog

from app.catalog_resolver import resolve_workspace
from app.catalog_resolver import resolve_workspace_settings
from opteryx_shared_services.logging import get_logger

from app.auth.deps import require_authentication
from app.core import _get_firestore_client
from app.odata.content_negotiation import MEDIA_JSON
from app.odata.content_negotiation import MEDIA_XML
from app.odata.content_negotiation import require_acceptable
from opteryx_shared_services.config import get_config
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.plan_optimizer import ODataLogicalPlanOptimizer
from app.odata.version_selector import label_to_version_kwargs
from app.odata.version_selector import split_dataset_label
from app.odata.column_stats import ipv4_address
from app.odata.column_stats import ipv4_enclosing_cidr
from app.odata.column_stats import is_ipv4_source
from app.odata.column_stats import usable_bound
from app.odata.edm_types import EDM_UNKNOWN
from app.odata.edm_types import ORIGINAL_TYPE_TERM
from app.odata.edm_types import RESOURCE_TERM
from app.odata.edm_types import edm_property
from opteryx_catalog.catalog.dataset import _stored_type_display
from app.odata.virtual_datasets import TASK_DEFINITION_COLUMNS
from app.odata.virtual_datasets import TASK_DESCRIPTION
from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS
from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_TABLES
from app.odata.virtual_datasets import is_virtual_information_schema
from app.odata.xml_safety import sanitize_xml_tree
from app.routes.v4.errors import describe_type_mismatch
from app.routes.v4.metadata_cache import clear_describe_cache
from app.routes.v4.metadata_cache import clear_metadata_cache
from app.routes.v4.metadata_cache import describe_cache_get
from app.routes.v4.metadata_cache import describe_cache_put
from app.routes.v4.metadata_cache import metadata_cache_get
from app.routes.v4.metadata_cache import metadata_cache_put
from app.routes.v4.metadata_cache import xml_response

logger = get_logger()

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

# Closes every storage-read 503: what the caller can and cannot do about it.
_STORAGE_READ_ADVICE = (
    "This is not caused by the query and rewriting it will not help. The failure "
    "has been logged; retry shortly, and report it if it persists."
)


# Resolved per use, not at import. A module-level constant is evaluated once
# at startup and then frozen for the life of the process, so config changes
# would never reach it without a redeploy.
def _firestore_database() -> str:
    return get_config("FIRESTORE_DATABASE")


def _bucket_name() -> str:
    return get_config("GCS_BUCKET")



# --- external-catalog stubs -------------------------------------------------
# A workspace bound to a customer's own Iceberg catalog has no real dataset
# documents. control.opteryx projects that catalog's listing into Firestore as
# STUB documents marked `external-catalog: true`, carrying an inline `schema`
# and a `statistics` block (see opteryx-catalog's stub_projection module).
#
# The service-wide `/$metadata` reads those fields already, because it builds
# its document straight from the catalog documents. THIS endpoint does not: it
# goes through `OpteryxCatalog.load_dataset(...)` and asks the loaded dataset
# for its schema, which resolves through `current-schema-id` and a `schemas`
# subcollection. A stub has neither, so every bound workspace's tables listed
# fine and then 404ed here with "dataset schema unavailable in catalog" - the
# tree showed them, the column view had nothing.
#
# The stub's schema is a thin facade, deliberately not versioned: the external
# catalog owns schema evolution, and a projection of one version is all there
# is to hold. So it is read inline here rather than given a `schemas`
# subcollection just to satisfy this code path.


def _external_catalog_stub(workstream: str, collection: str, dataset: str) -> dict | None:
    """The stub document for this dataset, or None if it is not one.

    One document read, and the same document `load_dataset` would read a moment
    later on the native path - so a real dataset pays for one small duplicate
    get, which is cheap beside the schema document and the GCS `describe()`
    that path goes on to fetch. Checking first is what keeps the stub branch
    from doing that pointless work on every request for a bound workspace,
    where EVERY dataset is a stub.

    Any failure returns None: an unreadable document is not evidence of a stub,
    and the native path's own error handling is better at explaining it.
    """
    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return None
        document = (
            db.collection(workstream)
            .document(collection)
            .collection("datasets")
            .document(dataset)
            .get()
        )
        if not document.exists:
            return None
        data = document.to_dict() or {}
    except Exception:
        return None
    return data if data.get("external-catalog") else None

def _merge_column_statistics(col_metadata: dict, description) -> None:
    """Fold a `describe()` result into the per-column metadata the renderer reads.

    Shared by the native path and the external-catalog stub path. It was inline
    on the native path only, which is how a bound workspace came to advertise
    its columns with no statistics at all while the native one showed them.
    """
    if not description or not isinstance(description, dict):
        return
    for col_name, stats in description.items():
        if col_name not in col_metadata or not isinstance(stats, dict):
            continue
        meta = col_metadata[col_name]
        meta.update(stats)
        # A column the manifest computed no bounds for carries the int64
        # sentinel, not a null. Newer catalog wheels drop it in `describe()`
        # itself; this stays for the ones that don't, at the single point stats
        # enter, so nothing downstream mistakes -9223372036854775808 for a real
        # bound.
        meta["min"] = usable_bound(meta.get("min"))
        meta["max"] = usable_bound(meta.get("max"))


def _stub_column_statistics(workstream: str, collection: str, dataset: str, stub: dict):
    """`describe()` for an external-catalog stub, or None if it has no manifest.

    This branch used to annotate no statistics at all, on the reasoning that
    "the projection deliberately does not read the external catalog's
    manifests, so there are no bounds to annotate". That stopped being true
    when control.opteryx's workspace refresh began WRITING one - a Parquet
    manifest in the workspace's own GCS metadata/ prefix, holding the row
    counts, null counts, sizes and bounds it collected from the source (for
    PostgreSQL, `pg_stats` plus an optional ANALYZE) - and recording its path
    on the stub document as `manifest-list`. The bounds exist; nothing read
    them, so a bound workspace's columns showed their types and nothing else.

    Gated on that pointer, so a stub written before the manifest existed, or
    one whose table could not be read at refresh time, costs exactly what it
    cost before: no catalog handle, no GCS fetch. Only a stub that has
    statistics pays to fetch them.

    Best effort, like every other enrichment on this endpoint: a manifest that
    cannot be read costs the document its statistics, never its columns.
    """
    if not stub.get("manifest-list"):
        return None
    try:
        workspace = OpteryxCatalog(
            workstream,
            firestore_project=GCP_PROJECT_ID,
            firestore_database=_firestore_database(),
            gcs_bucket=_bucket_name(),
        )
        return workspace.load_dataset(f"{collection}.{dataset}").describe()
    except Exception as exc:
        logger.warning(
            f"Could not read projected statistics for {workstream}.{collection}.{dataset}: {exc}"
        )
        return None


def _fork_relationship(workstream: str, collection: str, dataset: str) -> dict | None:
    """What this dataset was forked from, and how far it has diverged.

    `None` for a dataset nobody cloned, which is nearly all of them - the
    absence is the common case and the caller emits no annotation for it.

    TWO READS, and only when the first says there is something to say. The
    dataset document carries the fork block; the UPSTREAM's document carries
    the sequence number the "behind" figure is measured against, and that second
    read is skipped entirely for a dataset that is not a fork.

    REVISIONS ARE UPPER BOUNDS. A sequence number advances on every commit,
    maintenance included, so a difference of 3 may be three inserts or two
    compactions and a statistics refresh. Anything rendering these must say "at
    most"; zero is exact, and is the answer that matters.

    Never fatal, like every enrichment on this document: a catalog too old to
    carry the block, or a read that fails, costs the annotation and nothing
    else.
    """
    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return None
        doc = (
            db.collection(workstream)
            .document(collection)
            .collection("datasets")
            .document(dataset)
            .get()
        )
        data = (doc.to_dict() or {}) if getattr(doc, "exists", False) else {}
        fork = data.get("fork")
        if not isinstance(fork, dict):
            return None
        source = fork.get("source") or {}
        target = fork.get("target") or {}
        upstream = source.get("dataset")
        if not upstream:
            return None

        # This side's divergence needs nothing but the two numbers already in
        # hand: the head's sequence against the anchor the last sync wrote.
        current = data.get("current-snapshot-id")
        ahead = 0
        if current is not None:
            snap = (
                db.collection(workstream)
                .document(collection)
                .collection("datasets")
                .document(dataset)
                .collection("snapshots")
                .document(str(current))
                .get()
            )
            snap_data = (snap.to_dict() or {}) if getattr(snap, "exists", False) else {}
            ahead = max(
                0,
                int(snap_data.get("sequence-number") or 0)
                - int(target.get("sequence-number") or 0),
            )

        behind = 0
        parts = str(upstream).split(".")
        if len(parts) == 3:
            up_doc = (
                db.collection(parts[0])
                .document(parts[1])
                .collection("datasets")
                .document(parts[2])
                .get()
            )
            up_data = (up_doc.to_dict() or {}) if getattr(up_doc, "exists", False) else {}
            up_current = up_data.get("current-snapshot-id")
            if up_current is not None:
                up_snap = (
                    db.collection(parts[0])
                    .document(parts[1])
                    .collection("datasets")
                    .document(parts[2])
                    .collection("snapshots")
                    .document(str(up_current))
                    .get()
                )
                up_snap_data = (
                    (up_snap.to_dict() or {}) if getattr(up_snap, "exists", False) else {}
                )
                behind = max(
                    0,
                    int(up_snap_data.get("sequence-number") or 0)
                    - int(source.get("sequence-number") or 0),
                )
    except Exception as exc:  # noqa: BLE001 - catalog boundary
        logger.warning(f"Failed to read fork for {workstream}.{collection}.{dataset}: {exc}")
        return None

    return {
        "upstream": str(upstream),
        "base_snapshot": source.get("snapshot-id"),
        "revisions_behind": behind,
        "revisions_ahead": ahead,
        "last_sync_ms": target.get("last-sync-ms"),
        "forked_by": fork.get("forked-by"),
    }


def _standing_sources(workstream: str, collection: str, dataset: str) -> tuple[list[str], bool]:
    """The dataset document's standing source list (PROVENANCE_DESIGN.md S2.2)
    and whether it is complete, read straight from the document the way
    `_declared_relationships` reads its subcollection.

    Never fatal, like every enrichment on the metadata document: a catalog too
    old to write the field, a dataset that is really a view or a task, or a
    read that fails, costs the list and nothing else. All of those come back as
    an incomplete empty list, which is the catalog's own reading of a document
    without the field - a list nobody maintained may be missing anything.
    """
    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return [], False
        doc = (
            db.collection(workstream)
            .document(collection)
            .collection("datasets")
            .document(dataset)
            .get()
        )
        data = (doc.to_dict() or {}) if getattr(doc, "exists", False) else {}
    except Exception as exc:  # noqa: BLE001 - catalog boundary
        logger.warning(f"Failed to read sources for {workstream}.{collection}.{dataset}: {exc}")
        return [], False
    names = [str(name) for name in data.get("sources") or []]
    return names, bool(data.get("sources-complete", False))


def _policy_fingerprint(access_policies_list) -> str:
    """Stable digest of the caller's policy set, for the metadata cache key.

    The document now carries a per-source `Visible` flag, and the RECEIPT's
    sources are not known until after the cache is consulted -- so unlike the
    standing list, whose verdicts ARE in the key by name, there is nothing
    finer than the policy set to key those flags on. Two callers with matching
    policies see the same flags everywhere and may share an entry; anyone else
    gets their own.

    Digested rather than carried whole so the key stays small and hashable
    whatever shape the policy documents take.
    """
    try:
        encoded = json.dumps(access_policies_list or [], sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - a policy document is not schema-enforced
        encoded = repr(access_policies_list)
    return hashlib.blake2b(encoded.encode("utf-8"), digest_size=16).hexdigest()


def _source_is_readable(name: str, identity: str | None, access_policies_list) -> bool:
    """Whether the caller may read a dataset named in provenance.

    Provenance NAMES every dataset it cites -- a name is a citation, not access
    -- but a reader still needs to know which of those names they can follow.
    This is the flag that says so, emitted as `Visible` beside the name on
    Custom.Sources, Custom.ReadSources and Custom.Consumers.

    Answered by `_can_read_relation`, the same ladder the route applies to the
    dataset it was ASKED about -- so a dataset in `public`, or in the caller's
    own `personal` workspace, is readable here for the reason it is readable
    there. Matching only the policy patterns called both of those a dead end.
    No catalog round trip either way: the ladder is local.

    A name that is not three parts cannot be resolved to a relation, so it is
    reported unreadable rather than assumed either way -- a grant that does not
    demonstrably cover a name must not be claimed to.
    """
    parts = str(name or "").split(".")
    if len(parts) < 3:
        return False
    return _can_read_relation(parts[0], parts[1], ".".join(parts[2:]), identity, access_policies_list)


def _policy_fingerprint(access_policies_list) -> str:
    """Stable digest of the caller's policy set, for the metadata cache key.

    The document carries per-name `Visible` flags. The standing list's names
    are known before the cache is consulted and their verdicts go in the key
    by name; the RECEIPT's and the CONSUMERS' are not, so there is nothing
    finer than the policy set to key those on. Two callers with matching
    policies see the same flags everywhere and may share an entry.
    """
    try:
        encoded = json.dumps(access_policies_list or [], sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - a policy document is not schema-enforced
        encoded = repr(access_policies_list)
    return hashlib.blake2b(encoded.encode("utf-8"), digest_size=16).hexdigest()


# The 64 the catalog caps a consumer list at (impact.MAX_SOURCES). Named here
# so the annotation's prose and the query agree.
_CONSUMER_CAP = 64


def _consumers_of(dataset_fq: str, snapshot_id, source_type: str) -> dict | None:
    """Who READ this version of this dataset -- the downstream half.

    The only question the dataset's own documents cannot answer: `sources` and
    `read-sources` both look upwards. `impact.find_readers` answers it from the
    `read-source-keys` index on every snapshot in the catalog, narrowed to the
    version this document describes, which is what makes it a TAINT answer --
    not "who uses this table" but "who consumed the bad version".

    Answers CROSS WORKSPACE. A consumer in a workspace the caller cannot read
    is named like any other, with `Visible` false beside it; see impact.py's
    module docstring for why an impact answer that will not say who is
    affected does not answer the question.

    Never fatal, like every enrichment on this document: no snapshot, a kind
    that cannot have consumers, or a failed query costs the annotation and
    nothing else -- and it is OMITTED rather than sent empty, because "nobody
    read this version" and "we could not look" are different facts and only
    the first is something a client may act on.
    """
    if snapshot_id is None or source_type not in TABLE_LIKE_SOURCES:
        return None
    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return None
        from opteryx_catalog.impact import find_readers

        return find_readers(db, dataset_fq, snapshot_id=int(snapshot_id), limit=_CONSUMER_CAP)
    except Exception as exc:  # noqa: BLE001 - catalog boundary
        logger.warning(f"Failed to read consumers for {dataset_fq}: {exc}")
        return None


def _declared_relationships(
    workstream: str,
    collection: str,
    dataset: str,
    identity: str | None,
    access_policies_list,
) -> list[dict]:
    """Relationships declared ON this dataset, filtered to what the caller may see.

    A relationship records that a column holds values corresponding to a column
    of another table. NOTHING ENFORCES IT: a write that breaks it succeeds, and
    no query plan consults it. It is here so a client can see how two tables fit
    together.

    Read straight from the dataset's own `relationships` subcollection. Unlike
    the service-wide document's collection-group query this needs no index and
    no filter -- the dataset is already known, which is the whole benefit of
    storing these under the dataset they are declared on.

    BOTH ENDS MUST BE READABLE. The row names the far table's collection,
    dataset and column, so a caller who can read only this side would learn the
    shape of data they hold no grant on. The far end is always in this same
    workspace (a relationship cannot cross one), so the same ladder the caller's
    own access used decides it.

    Never fatal: a workspace with no relationships and an older catalog with no
    such subcollection are the same thing to a client, and neither is a reason
    to fail an otherwise-complete metadata document.
    """
    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return []
        documents = (
            db.collection(workstream)
            .document(collection)
            .collection("datasets")
            .document(dataset)
            .collection("relationships")
            .stream()
        )
        rows = [doc.to_dict() or {} for doc in documents]
    except Exception as exc:
        logger.warning(f"Failed to read relationships for {workstream}.{collection}.{dataset}: {exc}")
        return []

    visible = []
    for row in rows:
        # Only what a person asserted and that still stands. An inferred
        # proposal or a broken relationship is not something to show as fact.
        if row.get("kind") != "maps":
            continue
        if row.get("origin") != "asserted":
            continue
        if row.get("status") != "active":
            continue

        far_collection = row.get("references-collection")
        far_dataset = row.get("references-dataset")
        if not far_collection or not far_dataset:
            continue
        if not _can_read_relation(
            workstream, far_collection, far_dataset, identity, access_policies_list
        ):
            continue
        visible.append(row)
    return visible


def _inbound_relationships(
    workstream: str,
    collection: str,
    dataset: str,
    identity: str | None,
    access_policies_list,
) -> list[dict]:
    """Relationships declared elsewhere that point AT this dataset.

    The design stores one row per relationship, under the dataset that declares
    it, and answers the reverse question by searching rather than by keeping a
    mirrored copy -- a copy holds nothing the original does not and can only
    drift out of step with it. This is that search.

    A collection group query, so it costs one indexed read rather than a walk
    over every dataset in the workspace. Unlike the forward direction, which is
    a direct read of this dataset's own subcollection, this one REQUIRES a
    collection-group index on `relationships` over (references-workspace,
    references-collection, references-dataset).

    THE BACKTICKS ARE LOAD-BEARING. An unquoted Firestore property path must
    match `[a-zA-Z_][a-zA-Z_0-9]*`, and these field names are hyphenated, so a
    bare path is rejected outright.

    THE WORKSPACE FILTER IS LOAD-BEARING TOO. A collection group query crosses
    every workspace in the database; without it this would return other tenants'
    relationships. It is sound because a relationship cannot cross a workspace,
    so the far end's workspace is always this one.

    Visibility runs the other way round from the forward direction: the row
    names the dataset that DECLARED it, so that is the one the caller must be
    able to read.
    """
    from google.cloud.firestore_v1.base_query import FieldFilter

    try:
        db = _get_firestore_client(database=_firestore_database())
        if db is None:
            return []
        query = (
            db.collection_group("relationships")
            .where(filter=FieldFilter("`references-workspace`", "==", workstream))
            .where(filter=FieldFilter("`references-collection`", "==", collection))
            .where(filter=FieldFilter("`references-dataset`", "==", dataset))
        )
        rows = [doc.to_dict() or {} for doc in query.stream()]
    except Exception as exc:
        # Never fatal, for the same reason the forward direction is not: an
        # absent index or an older catalog means "no inbound relationships",
        # which is not a reason to fail an otherwise-complete document. Logged
        # at warning with the exception, which carries Firestore's index link.
        logger.warning(
            f"Failed to read inbound relationships for "
            f"{workstream}.{collection}.{dataset}: {exc}"
        )
        return []

    visible = []
    for row in rows:
        if row.get("kind") != "maps":
            continue
        if row.get("origin") != "asserted":
            continue
        if row.get("status") != "active":
            continue

        near_collection = row.get("collection")
        near_dataset = row.get("dataset")
        if not near_collection or not near_dataset:
            continue
        if not _can_read_relation(
            workstream, near_collection, near_dataset, identity, access_policies_list
        ):
            continue
        visible.append(row)
    return visible


def _can_read_relation(
    workstream: str,
    collection: str,
    dataset: str,
    identity: str | None,
    access_policies_list,
) -> bool:
    """Whether the caller may read one relation, by the same ladder the
    per-dataset metadata route uses for the dataset it was asked about."""
    if workstream == "personal":
        return collection == identity
    if workstream == "public":
        return True
    from app.auth.permissions import read_grant_for_relation

    return (
        read_grant_for_relation(
            access_policies_list, f"{workstream}.{collection}.{dataset}"
        )
        is not None
    )


def _default_top() -> int:
    return int(get_config("ODATA_DEFAULT_TOP", "100"))


def _max_top() -> int:
    return int(get_config("ODATA_MAX_TOP", "25000"))


# The two values below feed OpenAPI descriptions built inside route decorators,
# which Python evaluates once at import -- there is no per-request hook to hang
# them off. They are therefore snapshots, and a config change reaches the docs
# only on redeploy. Request handling calls `_default_top()`/`_max_top()` and
# does pick up changes; only the prose is frozen.
_DOC_DEFAULT_TOP = _default_top()
_DOC_MAX_TOP = _max_top()

# A materialized view is a real dataset -- it has a schema, snapshots and a sort
# order like any table -- that the catalog tags with `dataset-type`
# (MATERIALIZED_VIEW_TYPE in opteryx_catalog.opteryx_catalog, surfaced on a
# loaded dataset as `ds.metadata.dataset_type`). It is surfaced under its own
# source label, but must follow the *Table* code path everywhere else. The value
# is duplicated rather than imported so this module stays importable without the
# catalog package (several tests load it against stubs).
MATERIALIZED_VIEW_DATASET_TYPE = "materialized_view"
MATERIALIZED_VIEW_SOURCE = "MaterializedView"
# Source labels backed by a physical table: real schema, snapshots, sort orders.
EXTERNAL_TABLE_SOURCE = "ExternalTable"
# An external table is table-like for every annotation below: it has a row
# count, a last-modified time and possibly a sort order. Leaving it out of this
# tuple would silently drop all three from its metadata document.
TABLE_LIKE_SOURCES = ("Table", EXTERNAL_TABLE_SOURCE, MATERIALIZED_VIEW_SOURCE)

# A task: a statement the platform runs on its own. Deliberately NOT in
# TABLE_LIKE_SOURCES -- it is the one catalog object with no storage behind it,
# so it has no snapshot, no row count, no sort order and no tags, and every
# annotation derived from those must stay off its document rather than be
# reported as zero. It is in the same `workspace.collection.<object>` namespace
# as the rest, so this endpoint has to answer for its name; what it answers
# with is the fixed single-row definition schema (TASK_DEFINITION_COLUMNS).
TASK_SOURCE = "Task"


def _task_record(workspace, collection: str, dataset: str) -> dict | None:
    """A task's registration record, or None if this name is not a task.

    Called only after `load_dataset` AND `load_view` have both missed, so a
    table, a view and a materialized view never pay for it: the read happens
    exactly where the alternative was a 404, and it is the read that turns that
    404 into a real document.

    Returns None for "not a task" and for an older catalog wheel with no
    `get_task()` -- both mean the same thing to the caller (fall through to the
    404 it was already going to raise), and neither is worth a log line. Any
    other failure is logged: a task that exists but cannot be read is a
    different problem from one that does not exist, and silently reporting the
    second for the first is what makes a catalog bug look like a typo.
    """
    getter = getattr(workspace, "get_task", None)
    if getter is None:
        return None
    try:
        return getter(f"{collection}.{dataset}")
    except Exception as exc:  # noqa: BLE001 - catalog boundary
        if type(exc).__name__ != "TaskNotFound":
            logger.warning(
                "task metadata unavailable for %s.%s: %s: %s",
                collection,
                dataset,
                type(exc).__name__,
                exc,
            )
        return None


def _as_tag_int(value) -> int | None:
    """A tag's snapshot id as an int, or None if it is not one.

    Catalog documents are not schema-enforced, and a snapshot id that arrived
    as a string would otherwise render as `Int="1785..."` in one document and
    `Int="None"` in another. Anything uncoercible drops the PropertyValue
    rather than the whole tag: the name is still worth sending.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _loaded_dataset_source_type(ds) -> str:
    """"Table" or "MaterializedView" for an already-loaded catalog dataset.

    Reads `ds.metadata.dataset_type`, which the catalog populates from the
    dataset document's `dataset-type` field -- no extra round trip. Defensive
    because older opteryx-catalog wheels have no such attribute.
    """
    try:
        if getattr(getattr(ds, "metadata", None), "dataset_type", None) == (
            MATERIALIZED_VIEW_DATASET_TYPE
        ):
            return MATERIALIZED_VIEW_SOURCE
    except Exception:
        pass
    return "Table"


def register_default_connector() -> None:
    """(Re-)register the default connector with freshly resolved configuration.

    `opteryx.connectors.set_default_connector` just rebinds a module-level
    dict (see opteryx-core `opteryx/connectors/__init__.py`), so calling it
    again is cheap and idempotent -- and it is the only way the connector's
    own `firestore_database`/`gcs_bucket` can ever pick up a config change:
    the registration captures those values by value, so a registration done
    once at import stays frozen for the life of the process.

    Called at import (below) to preserve the import-time side effect other
    modules rely on -- `app.routes.clickbench` builds opteryx sessions
    without registering a connector of its own -- and again from
    `execute_odata_query` so every query re-resolves.
    """
    opteryx.set_default_connector(
        OpteryxConnector,
        catalog=OpteryxCatalog,
        firestore_project=GCP_PROJECT_ID,
        firestore_database=_firestore_database(),
        gcs_bucket=_bucket_name(),
    )


register_default_connector()

# Slot 2 of opteryx-core's resolution chain, consulted BEFORE the default
# registered above. A workspace bound to a customer's own catalog (Iceberg, a
# PostgreSQL server) resolves through here; one with no binding falls through to
# that default exactly as it always did.
#
# This service already ADVERTISES bound workspaces - `$metadata` and the service
# document are built from the stub dataset documents a catalog refresh projects
# - so without this it listed a bound workspace's tables with their columns and
# then served nothing when a client read the entity set, because every workspace
# resolved to the native catalog whatever its binding said.
#
# Installed once at import, like jobs' and the worker's: the resolver captures
# no configuration, re-reading it per call, so there is nothing here to go
# stale. See app/catalog_resolver.py, and keep the three in step.
set_workspace_resolver(resolve_workspace)

# The settings axis - which catalog holds a workspace's properties and
# lifecycle - is ALWAYS the native one, whatever the data is bound to.
# opteryx-core refuses a data resolver installed without a settings resolver,
# so these two go together.
set_workspace_settings_resolver(resolve_workspace_settings)

# opteryx-core permits everything on its own -- access control belongs to a
# deployment, not to the engine. This is the deployment, so it installs the
# capability that reads the `access_policies` each session is handed.
#
# At import, ONCE, and deliberately not inside `register_default_connector`:
# that runs again per query, and a capability may not be installed after a
# permission check has been answered. Unguarded on purpose -- a service that
# cannot enforce policy must fail to start rather than serve permissively.
#
# Built over a policy STORE, not bare: bare `capability()` can only answer
# about the asking session, and refuses GRANT/REVOKE/SHOW GRANTS ON and
# principal checks (ALTER MATERIALIZED VIEW ... OWNER TO) with
# PolicyStoreRequiredError. The store resolves its Firestore client through a
# factory on every access -- the project/database come from configuration that
# can change under a running process, and `_get_firestore_client` is
# lru_cached, so nothing is frozen here and the per-access cost is a dict
# lookup. Policies live at `{workspace}/$policies/access` in the CATALOG
# database, hence `_firestore_database()`, never the default database.
from opteryx_access.adapters.firestore import FirestorePolicyStore

opteryx.register_permissions_capability(
    opteryx_access.capability(
        FirestorePolicyStore(
            db_factory=lambda: _get_firestore_client(database=_firestore_database())
        )
    )
)


class _MaterializedResult:
    """Minimal pyarrow.Table-alike (column_names/num_rows/to_pydict()) built from
    Draken Morsels without a pyarrow dependency.

    opteryx-core dropped pyarrow from its reader/planner (commit 6094635a) and
    ``Morsel.to_arrow()``/``Vector.to_arrow()`` raise ImportError when pyarrow
    isn't installed — which it deliberately no longer is here. ``Vector.to_pylist()``
    has no such dependency, so results are assembled from that instead.
    """

    __slots__ = ("column_names", "num_rows", "_columns")

    def __init__(self, column_names, columns, num_rows):
        self.column_names = column_names
        self.num_rows = num_rows
        self._columns = columns  # name -> list of values, same order as column_names

    def to_pydict(self):
        return dict(self._columns)


def _materialize_logical_plan_result(
    session, logical_plan, visibility_filters=None
) -> _MaterializedResult:
    """Execute a logical plan on a session and materialize the result as a table-alike.

    ``Session.execute_logical_plan`` returns a ``(results_generator, ResultType)`` tuple
    rather than a materialized table (opteryx-core commit 6094635a). TABULAR results
    are streamed as Draken Morsels; concatenate them into a single in-memory result so
    the rest of this module can keep working with the table-shaped API it expects.
    """
    from opteryx.constants import ResultType

    results_generator, result_type = session.execute_logical_plan(
        logical_plan, connection=session, visibility_filters=visibility_filters
    )

    if result_type != ResultType.TABULAR:
        # NON_TABULAR results (e.g. SET/EXPLAIN) have no rows to return here.
        return _MaterializedResult([], {}, 0)

    column_names = None
    columns = {}
    num_rows = 0
    for morsel in results_generator:
        # Morsels straight off the logical-plan executor are Cxx-backed; materialize()
        # is the sanctioned point where they become PyObject-backed for callers outside
        # the engine (mirrors what Session.execute_to_morsels does for its SQL-text path).
        morsel.materialize()
        names = [n.decode("utf-8") if isinstance(n, bytes) else n for n in morsel.column_names]
        if column_names is None:
            column_names = names
            columns = {name: [] for name in names}
        for name in names:
            columns[name].extend(morsel.column(name).to_pylist())
        num_rows += morsel.num_rows

    return _MaterializedResult(column_names or [], columns, num_rows)


def _execute_and_serialize_rows(
    session, logical_plan, top: int, fetch_top: int, visibility_filters=None
):
    """Execute a logical plan and serialize its rows straight from Draken
    Morsels to JSON bytes via rugo's native ``write_jsonl``, skipping Python
    object materialization (``morsel.column(name).to_pylist()`` +
    ``_sanitize_value_for_odata`` per cell) entirely for the row payload.

    KNOWN GAP (tracked separately, in rugo): ``write_jsonl`` currently emits
    bare ``NaN``/``Infinity``/``-Infinity`` tokens for non-finite floats,
    which are not valid JSON and will break any strict JSON parser (incl.
    every browser's JSON.parse). This function assumes that's fixed
    rugo-side to null those out, matching what ``_sanitize_value_for_odata``
    already does for the Python-materialization path. Do not call this for
    columns that can contain non-finite floats until that lands.

    Returns (value_bytes, num_rows, col_names, has_more_rows):
      - value_bytes: raw bytes of a JSON array (``[{...},{...}]``), ready to
        be spliced directly into a response body -- never re-parsed.
      - num_rows: rows actually returned (post-truncation).
      - col_names: column names, for logging/consistency with the other paths.
      - has_more_rows: True if `fetch_top` rows came back and were trimmed to `top`.
    """
    from opteryx.constants import ResultType
    from rugo.jsonl import write_jsonl

    results_generator, result_type = session.execute_logical_plan(
        logical_plan, connection=session, visibility_filters=visibility_filters
    )

    if result_type != ResultType.TABULAR:
        return b"[]", 0, [], False

    col_names = []
    jsonl_parts = []
    total_rows = 0
    for morsel in results_generator:
        # No morsel.materialize() here: write_jsonl reads the Cxx-backed buffers
        # directly and produces byte-identical output, so materializing first
        # would only buy a full PyObject-backed copy of every result set.
        if not col_names:
            col_names = [n.decode("utf-8") if isinstance(n, bytes) else n for n in morsel.column_names]
        if morsel.num_rows:
            jsonl_parts.append(write_jsonl(morsel))
            total_rows += morsel.num_rows

    if not jsonl_parts:
        return b"[]", 0, col_names, False

    # Every row write_jsonl emits is `\n`-terminated, including the last one,
    # so morsels concatenate safely with no separator needed. Avoid
    # `split(b"\n")` here -- it would allocate a list of up to `top` (25k)
    # byte-slice objects on every request just to drop a trailing row that,
    # by construction (fetch_top == top + 1), is at most one row. Trim with
    # `rfind` instead and turn the remaining newlines into commas in a single
    # pass.
    jsonl = b"".join(jsonl_parts)
    has_more_rows = fetch_top > top and total_rows > top
    if has_more_rows:
        end = len(jsonl) - 1  # position of the trailing '\n' of the last row
        for _ in range(total_rows - top):
            end = jsonl.rfind(b"\n", 0, end)
        jsonl = jsonl[: end + 1]
        num_rows = top
    else:
        num_rows = total_rows

    value_bytes = b"[" + jsonl[:-1].replace(b"\n", b",") + b"]"
    return value_bytes, num_rows, col_names, has_more_rows


def _sanitize_value_for_odata(value: Any) -> Any:
    """
    Aggressively normalize values for OData/JSON compatibility.

    OData clients are strict about type matching. We prioritize compatibility
    over precision, converting everything to basic JSON-compatible primitives.

    Returns: int, float, str, bool, or None only
    """
    if value is None:
        return None

    # Boolean - check first since bool is subclass of int in Python
    if isinstance(value, bool):
        return value

    # Strings: return as-is, no heuristic mutation. Checked early (ahead of
    # numeric/datetime) since text columns dominate most result sets.
    if isinstance(value, str):
        return value

    # Numeric types -> int or float
    if isinstance(value, int):
        return value

    if isinstance(value, (float, decimal.Decimal)):
        # Convert Decimal to float, handle NaN/Infinity
        f = float(value)
        # OData doesn't handle NaN/Infinity well, convert to None
        if not (f == f):  # NaN check (NaN != NaN)
            return None
        if f == float("inf") or f == float("-inf"):
            return None
        return f

    # Datetime types -> ISO 8601 string with timezone (OData requires timezone)
    if isinstance(value, dt.datetime):
        # If naive datetime (no timezone), assume UTC
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        else:
            return value.isoformat()

    if isinstance(value, dt.date):
        # Date -> convert to datetime at midnight UTC
        return value.isoformat() + "T00:00:00Z"

    if isinstance(value, dt.time):
        # Time alone -> convert to today's date with that time in UTC
        return f"1970-01-01T{value.isoformat()}Z"

    # Binary data -> None (OData clients struggle with binary in JSON)
    if isinstance(value, (bytes, bytearray)):
        # Return base64-encoded string so binary columns can be represented
        try:
            return base64.b64encode(bytes(value)).decode("ascii")
        except Exception:
            return None

    # Lists/arrays -> return JSON array of sanitized inner values
    # Emit real arrays instead of stringifying to support Collection types
    if isinstance(value, (list, tuple)):
        return [_sanitize_value_for_odata(v) for v in value]

    # Dicts/structs (JSONB/STRUCT) -> encode as base64 JSON so we can expose as Edm.Binary
    if isinstance(value, dict):
        try:
            b = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            return base64.b64encode(b).decode("ascii")
        except Exception:
            return str(value)

    # Everything else -> convert to string as fallback
    # This handles UUIDs, enums, custom objects, etc.
    return str(value)


def _execute_count_query(
    make_session,
    table_name: str,
    filter_expr: Optional[str],
    apply_expr: Optional[str],
    compute_expr: Optional[str] = None,
    visibility_filters=None,
    version: Optional[int] = None,
    version_tag: Optional[str] = None,
) -> int:
    """Run a COUNT(*) plan over the same filters as the row query, returning the total.

    Takes a session factory rather than a session because this runs concurrently
    with the row query (see execute_odata_query) and a session carries per-execution
    state, so the two must not share one.

    version/version_tag must match the row query's -- otherwise $count=true
    against a pinned snapshot could report a total that doesn't match the rows
    returned alongside it. compute_expr must match too -- if $filter references
    a $compute alias, the count plan needs the same alias defined to resolve it.
    """
    count_builder = ODataLogicalPlanBuilder(table_name, version=version, version_tag=version_tag)
    count_plan = count_builder.build(
        filter_expr=filter_expr,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
        apply_expr=apply_expr,
        compute_expr=compute_expr,
        count_only=True,
    )
    count_table = _materialize_logical_plan_result(
        make_session(), count_plan, visibility_filters=visibility_filters
    )
    # Strict validation: count query MUST return a 1x1 numeric result. Fail fast with clear error otherwise.
    if not count_table:
        raise ValueError(f"Count query returned no table object for {table_name}")
    data = count_table.to_pydict()
    if not data:
        raise ValueError(f"Count query returned empty data for {table_name}")
    first_col = next(iter(data))
    vals = data.get(first_col)
    if vals is None or len(vals) == 0:
        raise ValueError(f"Count query returned no values for column '{first_col}' for {table_name}")
    try:
        return int(vals[0])
    except Exception as e:
        raise ValueError(
            f"Count query returned non-integer value: {vals[0]} (col={first_col}) for {table_name}"
        ) from e


def execute_odata_query(
    table_name: str,
    filter_expr: Optional[str] = None,
    orderby_expr: Optional[str] = None,
    select_cols: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    apply_expr: Optional[str] = None,
    compute_expr: Optional[str] = None,
    count_requested: bool = False,
    identity: Optional[str] = None,
    access_policies: Optional[List[dict]] = None,
    billing_account: Optional[str] = None,
    visibility_filters: Optional[dict] = None,
    entitlements: Optional[List[str]] = None,
    version: Optional[int] = None,
    version_tag: Optional[str] = None,
) -> dict:
    """Execute an OData query and return results.

    This function orchestrates the core OData query execution pipeline:
    1. Build logical plan from OData parameters (parsing + plan construction)
    2. Optimize the plan based on constraints (e.g., top=0 with count)
    3. Execute via Opteryx, handling optimization strategy

    Args:
        table_name: Full table name (e.g., "workstream.collection.dataset")
        filter_expr: OData $filter expression
        orderby_expr: OData $orderby expression
        select_cols: OData $select columns
        top: $top limit (1-`_max_top()`, default: `_default_top()`)
        skip: $skip offset
        count_requested: Whether to include total count
        apply_expr: OData $apply expression
        compute_expr: OData $compute expression (rejected together with
            apply_expr -- see ODataLogicalPlanBuilder.build)
        version: Explicit snapshot id from a `@{label}` version selector, or None
        version_tag: Tag name (including "current") from a `@{label}` version
            selector, or None

    Returns:
        dict with keys:
            - value_bytes: raw bytes of a JSON array (`[{...},{...}]`) of the result
              rows, pre-serialized -- splice directly into a response body, don't re-parse
            - row_count: number of rows in value_bytes
            - col_names: list of column names
            - total_count: total count (if requested), None otherwise
            - has_more_rows: bool indicating if result was truncated (more rows exist)
            - took_ms: execution time in milliseconds

    Raises:
        ValueError: If OData query is invalid
        Exception: If execution fails
    """
    import time

    start_time = time.time()

    # Step 1: Set defaults
    original_top = top
    if top is None:
        top = _default_top()

    # Step 2: Build logical plan from OData parameters
    # This parses each OData expression ($filter, $orderby, etc.) and builds
    # a structured representation of the query
    # When top is set, fetch top+1 internally to detect truncation
    fetch_top = top + 1 if top > 0 else top

    plan_builder = ODataLogicalPlanBuilder(table_name, version=version, version_tag=version_tag)
    try:
        logical_plan = plan_builder.build(
            filter_expr=filter_expr,
            select_cols=select_cols,
            orderby_expr=orderby_expr,
            top=fetch_top,
            skip=skip,
            apply_expr=apply_expr,
            compute_expr=compute_expr,
        )
    except ValueError as e:
        logger.error(
            f"Failed to build logical plan for table {table_name}: filter={filter_expr}, select={select_cols}, orderby={orderby_expr}, error={e}"
        )
        raise
    except Exception as e:
        logger.error(f"Unexpected error building logical plan for table {table_name}: error={e}")
        raise

    # Build a session carrying the caller's identity/access policies so that
    # Opteryx's permission checks are evaluated against the actual caller,
    # rather than falling back to an empty (deny-all) execution context.
    # billing_account attributes the usage this query generates to the caller's
    # account; when None, Opteryx falls back to its default (house) account.
    def make_session():
        # Re-resolve the platform config and re-register before every session,
        # so a config change reaches the connector without a redeploy.
        register_default_connector()
        return opteryx.session(
            user=identity,
            # An empty list, NEVER None. `access_policies=None` means "owner on
            # everything" to opteryx (see Session.__init__), which is the exact
            # opposite of the deny-all fallback described above -- a caller whose
            # policies came to nothing would be handed the whole catalog. Reachable
            # for an anonymous caller, whose claims carry no policies and for whom
            # `access_policies_for_query` appends none. jobs.opteryx guards the same
            # way, and pins it with a test.
            access_policies=access_policies or [],
            # The caller's entitlement names, bare. The engine's permission
            # capability resolves what they confer (`automation_admin::public`
            # is AUTOMATE on `public.*`), which is how an entitled caller sees
            # the triggers and tasks in a workspace no policy can grant. Without
            # them the same query answers empty here and full through jobs.
            entitlements=entitlements or [],
            billing_account=billing_account,
        )

    session = make_session()

    # Step 3: Optimize the plan based on query constraints
    # This makes strategic decisions about query execution (e.g., schema+count split)
    plan_optimizer = ODataLogicalPlanOptimizer()
    optimized_plan, strategy_info = plan_optimizer.optimize_with_constraints(
        logical_plan, top, count_requested
    )

    # Step 4: Execute based on optimization strategy
    strategy = strategy_info.get("strategy")

    # The count query shares no data dependency with the row (or schema) query, so
    # start it on its own thread now and collect it at the end: the request then
    # costs max(rows, count) instead of the sum of the two.
    count_pool = None
    count_future = None
    if count_requested:
        count_pool = ThreadPoolExecutor(max_workers=1)
        count_future = count_pool.submit(
            _execute_count_query,
            make_session,
            table_name,
            filter_expr,
            apply_expr,
            compute_expr=compute_expr,
            visibility_filters=visibility_filters,
            version=version,
            version_tag=version_tag,
        )

    try:
        if strategy == "schema_count_split":
            # Special optimization: top=0 with count_requested
            # Report the result's shape and its count, but fetch no data rows
            value_bytes = b"[]"
            row_count = 0
            col_names = []
            has_more_rows = False  # No data rows returned, so never truncated

            explicit_select = plan_builder.expression_parser.parse_select(select_cols or "")
            if explicit_select and explicit_select != ["*"]:
                # An explicit $select fixes the response columns exactly, in the
                # order the caller wrote them -- that is what Exit is given (see
                # ODataLogicalPlanBuilder._add_exit_node). So the answer is already
                # in hand and needs no query at all.
                col_names = explicit_select
            else:
                # Otherwise the columns depend on the data (or on $apply's output,
                # which the catalog cannot describe), so ask for a single row purely
                # to learn the shape. This runs while the count query is in flight,
                # so it costs nothing beyond the count it hides behind.
                try:
                    schema_builder = ODataLogicalPlanBuilder(
                        table_name, version=version, version_tag=version_tag
                    )
                    schema_plan = schema_builder.build(
                        filter_expr=filter_expr,
                        select_cols=select_cols,
                        orderby_expr=None,
                        top=1,
                        skip=None,
                        apply_expr=apply_expr,
                        compute_expr=compute_expr,
                    )
                    schema_table = _materialize_logical_plan_result(
                        session, schema_plan, visibility_filters=visibility_filters
                    )
                    col_names = schema_table.column_names or []
                except Exception as exc:
                    logger.error(f"Error getting schema for table {table_name}: {exc}")
                    raise
        else:
            # Standard path: execute the optimized logical plan, serializing rows
            # directly from Morsels to JSON bytes (see _execute_and_serialize_rows)
            # instead of materializing Python dicts.
            value_bytes = b"[]"
            col_names = []
            has_more_rows = False
            row_count = 0

            try:
                value_bytes, row_count, col_names, has_more_rows = _execute_and_serialize_rows(
                    session, optimized_plan, top, fetch_top, visibility_filters=visibility_filters
                )
            except Exception as exc:
                logger.error(
                    f"Error executing logical plan for table {table_name} with orderby={orderby_expr}, filter={filter_expr}: {exc}"
                )
                raise

        # Collected after the row/schema query so that when both fail, the row
        # query's error is the one that surfaces -- as it did when these ran serially.
        total_count = None
        if count_future is not None:
            try:
                total_count = count_future.result()
            except Exception as exc:
                tb = traceback.format_exc()
                logger.error(f"Error executing count(*) query for table {table_name}: {exc}\n{tb}")
                raise
    finally:
        if count_pool is not None:
            count_pool.shutdown(wait=True)

    elapsed_ms = (time.time() - start_time) * 1000

    return {
        "value_bytes": value_bytes,
        "row_count": row_count,
        "col_names": col_names,
        "total_count": total_count,
        "has_more_rows": has_more_rows,
        "took_ms": elapsed_ms,
    }




router = APIRouter(
    prefix="/api/v4",
    tags=["OData v4"],
    dependencies=[Depends(require_authentication)],
)


@router.get(
    "/{workstream}/{collection}/{dataset}",
    summary="Query dataset rows",
    description=(
        "Retrieve data from a dataset with OData v4 query parameters ($filter, $select, $orderby, "
        "$top, $skip, $apply, $compute, $count). Returns paginated results with total count and "
        "nextLink for server-driven paging. "
        "The dataset segment may carry a `@{label}` version selector: `dataset@current` (the "
        "default, also what a bare `dataset` means), `dataset@previous` (the most recent version "
        "of the data before this one — maintenance snapshots such as compaction that changed no "
        "rows are skipped), `dataset@{tag}` (a named snapshot), or `dataset@{snapshot_id}` (a "
        "specific snapshot id). `@current` and `@previous` are resolved fresh on every page, so "
        "paging through them is not snapshot-isolated against concurrent writes; `@{tag}` and "
        "`@{snapshot_id}` are immutable and page consistently."
    ),
    responses={
        200: {"description": "Query succeeded; returns rows and pagination metadata"},
        400: {"description": f"Invalid query: malformed $filter, unsupported $top value (not 0-{_DOC_MAX_TOP}), negative $skip, $skip without $orderby, invalid $count value, invalid $apply expression, invalid $compute expression, $compute combined with $apply, OData-MaxVersion below 4.0, or a malformed @{{label}} version selector"},
        401: {"description": "Missing or invalid authentication (no bearer token or basic auth)"},
        403: {"description": "Forbidden: authenticated but no permission for dataset"},
        404: {"description": "Dataset not found, or the @{label} version selector names a tag/snapshot/previous version that does not exist"},
        406: {"description": "Accept header does not admit application/json, the only format this route returns"},
        501: {"description": "Unsupported query feature: $search or $expand not implemented"},
    },
    dependencies=[Depends(require_acceptable(MEDIA_JSON))],
)
def read_dataset(
    request: Request,
    response: Response,
    workstream: str,
    collection: str,
    dataset: str,
    filter: Optional[str] = Query(
        None,
        alias="$filter",
        description=(
            "OData $filter expression for row filtering. Operators: eq (equal), ne (not equal), "
            "lt/le/gt/ge (comparison), and/or/not (logical), contains/startswith/endswith (string), "
            "in_subnet (IPv4 CIDR containment). Case-sensitive. in_subnet(ip_column, 'cidr') is an "
            "Opteryx extension for IPv4-typed columns, e.g. in_subnet(src_addr, '192.168.4.0/24'); "
            "the PostgreSQL <<= operator is not valid OData syntax. "
            "Example: vendor eq 'Oracle' and price gt 100. "
            "Date/datetime literals must be unquoted per the OData v4 spec, e.g. "
            "shipped_date gt 2024-01-01 — a quoted date is compared as a string and raises a "
            "type-mismatch error. "
            "Type conversion: cast(field, Edm.Type) converts a value before comparing it, e.g. "
            "cast(code, Edm.Int32) gt 10 on a number stored as text. Supported targets: "
            "Edm.String, Edm.Boolean, Edm.Byte, Edm.SByte, Edm.Int16, Edm.Int32, Edm.Int64, "
            "Edm.Single, Edm.Double, Edm.Decimal, Edm.Date, Edm.DateTimeOffset; a value that does "
            "not convert becomes null rather than failing the query. Other targets, "
            "the one-argument cast(Edm.Type) form and isof() are rejected. "
            "Date and time functions: now() (the query's wall clock, evaluated once per query so "
            "every row sees the same instant), year(), month(), day(), hour(), minute(), second() "
            "(each returns the named component of a date or timestamp as an integer, e.g. "
            "year(shipped_date) eq 2024), and date() (narrows a timestamp to its date part, e.g. "
            "date(created_at) eq 2024-01-01). The OData functions time(), mindatetime() and "
            "maxdatetime() are not implemented and are rejected with a message naming what to "
            "write instead. "
            "Rolling windows: combine now() with an ISO 8601 duration literal using add or sub, "
            "e.g. published_at ge now() sub duration'P30D' for the last 30 days. The duration "
            "syntax is duration'PnYnMnDTnHnMnS', optionally signed, e.g. duration'P1Y', "
            "duration'P18M', duration'PT12H', duration'-P7D'. Year and month durations are "
            "calendar-aware — duration'P1Y' means one calendar year and duration'P1M' one "
            "calendar month, so their length depends on the date they are applied to — while "
            "day, hour, minute and second durations are fixed spans (duration'P30D' is always "
            "exactly 30 × 24 hours)."
        ),
        # The description above is written for clients that read the spec and
        # cannot follow a link (LLMs, BI tools). The docs site renders this
        # instead and points at the guide that walks through the same syntax.
        json_schema_extra={
            "x-docs-summary": (
                "Row filter, e.g. `vendor eq 'Oracle' and price gt 100`. Comparison "
                "(`eq`, `ne`, `lt`, `le`, `gt`, `ge`), logical (`and`, `or`, `not`) and string "
                "(`contains`, `startswith`, `endswith`) operators, plus `cast()`, date "
                "functions, `now()` with durations for rolling windows, and the Opteryx "
                "extension `in_subnet(src_addr, '192.168.4.0/24')` for IPv4 columns. "
                "Case-sensitive; date literals are unquoted (`shipped_date gt 2024-01-01`). "
                "See [Querying via OData](/docs/guides/querying-via-odata#querying-a-dataset) "
                "for the full syntax."
            )
        },
    ),
    top: Optional[int] = Query(
        None,
        alias="$top",
        description=f"Limit result rows (0-{_DOC_MAX_TOP}, default {_DOC_DEFAULT_TOP}). Value 0 with $count=true returns count only. Returns @odata.nextLink if result is truncated."
    ),
    skip: Optional[int] = Query(
        None,
        alias="$skip",
        description="Skip N rows for pagination (server-driven). Requires $orderby: without a deterministic row order, paging duplicates some rows and drops others. Example: &$orderby=id asc&$skip=100 to fetch rows 101+. Combine with $top for paging."
    ),
    orderby: Optional[str] = Query(
        None,
        alias="$orderby",
        description="Sort by column(s): 'col1 asc, col2 desc'. Default ascending. Example: &$orderby=created_date desc"
    ),
    count: Optional[str] = Query(
        None,
        alias="$count",
        description="Include total row count in response: 'true' or 'false' (default false). Use with $top=0 to get count only."
    ),
    select: Optional[str] = Query(
        None,
        alias="$select",
        description="Select specific columns: 'col1,col2,col3' or '*' for all (default all). Reduces payload size."
    ),
    search: Optional[str] = Query(
        None,
        alias="$search",
        description="Full-text search (not implemented; returns 501)"
    ),
    compute: Optional[str] = Query(
        None,
        alias="$compute",
        description=(
            "Computed properties: comma-separated '<expression> as <name>' clauses, e.g. "
            "'price mul quantity as TotalValue'. The expression uses the same operators and "
            "functions as $filter (add/sub/mul/div/mod, contains(), etc.), just producing a "
            "value instead of a boolean. The new name is then usable in $select, $orderby, "
            "and $filter on the same request, e.g. "
            "$compute=price mul quantity as TotalValue&$filter=TotalValue gt 100. "
            "Not supported combined with $apply in this version (400)."
        ),
        json_schema_extra={
            "x-docs-summary": (
                "Computed columns, as comma-separated `<expression> as <name>` clauses, e.g. "
                "`price mul quantity as TotalValue`. Expressions use the same operators and "
                "functions as `$filter`, and the new name can be used in `$select`, `$orderby` "
                "and `$filter` on the same request. Can't be combined with `$apply`."
            )
        },
    ),
    apply: Optional[str] = Query(
        None,
        alias="$apply",
        description=(
            "Data aggregation: groupby((col), aggregate(amount with sum as Total, $count as Count)). "
            "Aggregates are written as '$count as Alias' or 'col with <method> as Alias', where "
            "<method> is one of sum, average, min, max -- that list is exhaustive, and function-call "
            "forms such as sum(amount) are not accepted. Transformations chain with '/', e.g. "
            "filter(x gt 1)/groupby((col), aggregate($count as Count)); a groupby with no aggregate "
            "deduplicates, so a distinct count is groupby((a,b))/groupby((a), aggregate($count as N))."
        ),
        json_schema_extra={
            "x-docs-summary": (
                "Server-side aggregation, e.g. "
                "`groupby((vendor), aggregate(amount with sum as Total, $count as Count))`. "
                "Aggregates are `$count as Alias` or `col with <method> as Alias`, where "
                "`<method>` is `sum`, `average`, `min` or `max`. Chain steps with `/`, e.g. "
                "`filter(price gt 1)/groupby((vendor), aggregate($count as N))`. See "
                "[Aggregating with `$apply`](/docs/guides/querying-via-odata#aggregating-with-apply)."
            )
        },
    ),
) -> Any:
    """Read rows from a specified dataset.

    Query OData datasets with standard OData v4 query parameters. Returns rows in
    JSON with @odata.context, value array, and optional @odata.count / @odata.nextLink.

    Request body:
    - Specify filters, selection, ordering, aggregation as OData query parameters

    Response:
    - value: array of row objects
    - @odata.context: metadata reference (e.g., /api/v4/$metadata#workstream_collection_dataset)
    - @odata.count (if $count=true): total matching rows
    - @odata.nextLink (if result truncated): URL for next page

    Common examples:
    - GET /ws/coll/data?$filter=status eq 'active' - filter rows
    - GET /ws/coll/data?$select=name,price&$top=50 - select columns, limit 50
    - GET /ws/coll/data?$orderby=created desc&$skip=100 - sort and paginate
    - GET /ws/coll/data?$apply=groupby((vendor),aggregate(amount with sum as Total))
    - GET /ws/coll/data?$count=true&$top=0 - get row count only
    - GET /ws/coll/data?$compute=price mul quantity as TotalValue&$filter=TotalValue gt 100

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    try:
        dataset_name, label = split_dataset_label(dataset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    version_kwargs = label_to_version_kwargs(label)

    table_name = f"{workstream}.{collection}.{dataset_name}"

    # Parse the raw query string once, here: it is needed for duplicate detection,
    # for the $expand rejection below, and again for the @odata.nextLink further
    # down. keep_blank_values is on because nextLink has to round-trip a blank
    # parameter the caller sent rather than silently dropping it.
    from urllib.parse import parse_qs

    query_params = parse_qs(request.url.query, keep_blank_values=True)

    # Reject unsupported query options
    if search and search.strip():
        raise HTTPException(
            status_code=501,
            detail="$search is not supported by this service"
        )

    # A blank "$expand=" asks for nothing and is allowed through; only an actual
    # navigation property is a 501.
    if any(query_params.get("$expand", [])):
        raise HTTPException(
            status_code=501,
            detail=f"$expand (navigation property expansion) is not supported by this service"
        )

    # Parameters for which duplicates are disallowed
    duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip", "$compute"]
    duplicates = [p for p in duplicate_sensitive if len(query_params.get(p, [])) > 1]
    if duplicates:
        raise HTTPException(
            status_code=400, detail=f"Multiple parameters not allowed: {', '.join(duplicates)}"
        )

    if top is None:
        top = _default_top()
    else:
        top = int(top)  # Will raise ValueError if not a valid integer

    # Validate $top range (allow 0 unconditionally per OData spec)
    max_top = _max_top()
    if not (0 <= top <= max_top):
        raise HTTPException(
            status_code=400,
            detail=f"$top must be between 0 and {max_top}",
        )

    # Validate $skip. A negative offset is not a valid OData value, and the
    # engine clamps it to zero rather than erroring -- silently answering a
    # different query than the caller asked for.
    if skip is not None:
        skip = int(skip)  # Will raise ValueError if not a valid integer
        if skip < 0:
            raise HTTPException(
                status_code=400,
                detail="$skip must be zero or greater",
            )

    # $skip is only well-defined over a deterministic row order, and this
    # service cannot provide one on its own: a dataset's files are scanned in
    # parallel, so morsel arrival order -- and therefore which rows a given
    # OFFSET lands on -- varies between executions of the same query. Paging
    # without $orderby returns some rows on more than one page and never
    # returns others: walking a 5000-row dataset in five pages of 1000
    # measures 5000 rows delivered but as few as 2999 of them distinct, and
    # the shortfall differs run to run. Reject it rather than serve a result
    # that looks complete and is not.
    if skip and not (orderby and orderby.strip()):
        raise HTTPException(
            status_code=400,
            detail=(
                "$skip requires $orderby: paging is only well-defined over a deterministic "
                "row order. Add $orderby on a column (or columns) that uniquely identify a row."
            ),
        )

    # Parse count parameter (only accept "true" or "false" per OData spec)
    want_count = False
    if count is not None:
        if isinstance(count, str):
            count_lower = count.lower()
            if count_lower in ("true", "false"):
                want_count = count_lower == "true"
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid $count value '{count}'; must be 'true' or 'false'"
                )
        else:
            want_count = bool(count)

    # Determine the caller's identity/access policies from their JWT claims so
    # Opteryx's permission checks are evaluated against the actual caller.
    from app.auth.permissions import (
        access_policies_for_query,
        entitlements_from_claims,
        is_anonymous_exempt,
        visibility_filters_from_claims,
    )

    claims = getattr(request.state, "claims", None) or {}
    identity = claims.get("identity")
    access_policies = access_policies_for_query(claims)
    entitlements = sorted(entitlements_from_claims(claims))
    billing_account = claims.get("billing_account")
    # Row-level scoping. Always supplied: it denies rather than allows when the caller's
    # billing account is unknown, and Opteryx leaves unlisted relations unfiltered.
    visibility_filters = visibility_filters_from_claims(claims)

    if identity is None and not is_anonymous_exempt(workstream, collection):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid credentials",
        )

    # Execute the OData query using the core function
    try:
        result = execute_odata_query(
            table_name=table_name,
            filter_expr=filter,
            orderby_expr=orderby,
            select_cols=select,
            top=top,
            skip=skip,
            apply_expr=apply,
            compute_expr=compute,
            count_requested=want_count,
            identity=identity,
            access_policies=access_policies,
            billing_account=billing_account,
            visibility_filters=visibility_filters,
            entitlements=entitlements,
            **version_kwargs,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except opteryx.exceptions.PermissionsError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except opteryx.exceptions.DatasetNotFoundError as e:
        # Match the $metadata endpoint: an unknown dataset is a 404, not a 400,
        # even though DatasetNotFoundError is (surprisingly) a subclass of SqlError.
        raise HTTPException(status_code=404, detail="dataset not found in catalog") from e
    except (opteryx.exceptions.SqlError, opteryx.exceptions.IncompatibleTypesError) as e:
        # "The query as given is invalid" — client-fixable (bad/uncast filter
        # literal, unknown column, unsupported syntax, etc.), not a server fault.
        # IncompatibleTypesError is listed separately because it doesn't actually
        # inherit from SqlError despite opteryx-core's own docs implying it does.
        #
        # Type mismatches go through describe_type_mismatch because opteryx's
        # remediation for them is SQL's `CAST(column AS type)`; the OData
        # spelling is `cast(column, Edm.Type)`. The commonest cause out here is
        # a quoted date/number, and the message says that instead.
        if isinstance(
            e, (opteryx.exceptions.IncompatibleTypesError, opteryx.exceptions.IncorrectTypeError)
        ):
            raise HTTPException(status_code=400, detail=f"Invalid query: {describe_type_mismatch(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    # The three DatasetReadError responses below are all 503: the dataset is
    # registered and the query is well-formed, so neither 404 nor 400 describes
    # it, and nothing the caller rewrites will help. They differ in saying WHICH
    # read failed, so nobody diagnoses the metadata when a data file failed.
    #
    # The engine's message embeds the storage URI it failed on, which names the
    # bucket and its internal layout, so it is logged and never returned.
    except opteryx.exceptions.DatasetMetadataReadError as e:
        logger.error(f"Dataset metadata unreadable for {table_name}: {e}")
        raise HTTPException(
            status_code=503,
            detail=(
                "dataset could not be read: its description (schema, snapshot or "
                f"manifest) could not be read from storage. {_STORAGE_READ_ADVICE}"
            ),
        ) from e
    except opteryx.exceptions.DatasetDataReadError as e:
        logger.error(f"Dataset data file unreadable for {table_name}: {e}")
        raise HTTPException(
            status_code=503,
            detail=(
                "dataset could not be read: a data file this query needed could not "
                f"be read from storage. {_STORAGE_READ_ADVICE}"
            ),
        ) from e
    except opteryx.exceptions.DatasetReadError as e:
        # Opteryx-core raises this single exception type for two very different
        # situations: a genuine storage failure (manifest unreadable), and a
        # `@{label}` version selector that named a tag/snapshot/previous version
        # that doesn't exist (see OpteryxTable._resolve_snapshot upstream). The
        # two need different status codes, and there's no distinct exception
        # type to catch, so this is keyed off the resolver's own stable message
        # templates ("No tag ...", "No snapshot ...", "No previous version for
        # ...", "... which could not be read"). A version-selector opteryx
        # exception subclass upstream would let this stop matching message text.
        msg = str(e)
        if (
            msg.startswith("No tag ")
            or msg.startswith("No snapshot ")
            or msg.startswith("No previous version for ")
            or "which could not be read" in msg
        ):
            raise HTTPException(status_code=404, detail=msg) from e

        # A read failure the engine did not attribute to the metadata or to a data
        # file (filesystem, connector and READ_* paths still raise the base class).
        # Say only what is known: something about the dataset could not be read.
        logger.error(f"Dataset unreadable for {table_name}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"dataset could not be read from storage. {_STORAGE_READ_ADVICE}",
        ) from e
    except Exception as e:
        logger.error(f"Error executing OData query for {table_name}: {e}")
        raise

    value_bytes = result["value_bytes"] or b"[]"
    row_count = result["row_count"]
    col_names = result["col_names"] or []
    total_count = result["total_count"]
    has_more_rows = result.get("has_more_rows", False)

    # All paging (LIMIT/OFFSET) should have been pushed down to SQL when
    # possible; no Python-side slicing is necessary.

    # Build the response body directly as bytes -- value_bytes is already a
    # complete, valid JSON array (see _execute_and_serialize_rows), so it's
    # spliced straight in rather than round-tripped through a dict and a
    # JSON encoder. json.dumps() is only used below for the two small,
    # dynamic strings (context/nextLink), to get correct escaping without
    # re-serializing the row payload.
    #
    # Per OData spec, the top-level resource set can only have:
    # @odata.context, value, @odata.count, @odata.nextLink
    # Use underscore form for context URI to match EntitySet name in metadata (CSDL SimpleIdentifier compliance)
    full_resource_fragment = f"{workstream}_{collection}_{dataset}"
    context_value = f"/api/v4/$metadata#{full_resource_fragment}"

    body_parts = [
        b'{"@odata.context":',
        json.dumps(context_value).encode("utf-8"),
        b',"value":',
        value_bytes,
    ]

    if total_count is not None:
        body_parts.append(b',"@odata.count":' + str(int(total_count)).encode("ascii"))

    # Build @odata.nextLink if result was truncated.
    #
    # This is emitted even when the caller gave no $orderby, in which case
    # following it earns the "$skip requires $orderby" 400 raised above. That
    # is deliberate: the alternative -- withholding the link -- makes a
    # truncated page indistinguishable from the end of the data, which is the
    # silent undercount this rule exists to eliminate. A 400 naming the missing
    # parameter is the louder, more fixable failure.
    if has_more_rows:
        from urllib.parse import urlencode

        # Calculate new skip value
        current_skip = int(skip or 0)
        new_skip = current_skip + top

        # Update or add $skip parameter
        query_params["$skip"] = [str(new_skip)]

        # Reconstruct query string, preserving parameter order and handling multi-valued params
        query_items = []
        for key, values in query_params.items():
            for value in values:
                query_items.append((key, value))

        # Build the nextLink URL (relative URL in the OData spec)
        next_query = urlencode(query_items)
        next_link = f"/api/v4/{workstream}/{collection}/{dataset}?{next_query}"
        body_parts.append(b',"@odata.nextLink":' + json.dumps(next_link).encode("utf-8"))

    body_parts.append(b"}")

    return Response(
        content=b"".join(body_parts),
        media_type="application/json",
        headers={"OData-Version": "4.0"},
    )


def _materialized_view_runs_as(catalog, mv: dict, qualified: str | None) -> str | None:
    """The identity a view's unattended refresh runs as, read from its triggers.

    One per source, and kept agreeing by the catalog - `ALTER MATERIALIZED
    VIEW ... OWNER TO` repoints all of them in one batch - so ONE distinct
    identity is the expected answer and is what this emits. Two is a view
    somebody split by hand with `ALTER TRIGGER ... OWNER TO`; naming either
    would be a guess, so nothing is emitted and `information_schema.triggers`
    is where the split is visible.

    TRANSITIONAL: a view registered before the identity moved onto its
    triggers still carries `runs-as` on its own record until the catalog's
    backfill (`scripts/backfill_refresh_trigger_identity.py`) retires it. It
    is honoured only where the triggers hold nothing, and goes with the field.

    Best effort throughout: an older catalog wheel has no `list_triggers`, and
    a source that cannot be listed must not break the metadata document.
    """
    candidates: set[str] = set()
    for source in mv.get("source-tables") or []:
        try:
            triggers = catalog.list_triggers(source)
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.debug(f"refresh triggers on {source} unreadable for {qualified}: {exc}")
            continue
        for trigger in triggers or []:
            if trigger.get("kind") != "materialized_view_refresh":
                continue
            target = trigger.get("target-view")
            if qualified and target and target != qualified:
                continue
            if trigger.get("runs-as"):
                candidates.add(str(trigger["runs-as"]))
    if not candidates and mv.get("runs-as"):
        return str(mv["runs-as"])
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _resolve_labeled_snapshot(workspace, collection: str, dataset: str, ds, label: str):
    """Resolve a `@{label}` version selector against an already-loaded,
    already permission-checked dataset object.

    Mirrors the three label kinds opteryx-core's OpteryxTable._resolve_snapshot
    resolves for `VERSION AS OF` (opteryx/connectors/opteryx_connector.py), so
    $metadata and row queries agree on what a label means. Called only after
    the caller's permission to view this dataset's metadata has already been
    checked -- resolving a label is not itself a permission decision.
    """
    lowered = label.lower()

    if lowered == "current":
        return ds.snapshot()

    if lowered == "previous":
        previous_user_snapshot = getattr(ds, "previous_user_snapshot", None)
        if previous_user_snapshot is None:
            raise HTTPException(
                status_code=400,
                detail=f"{collection}.{dataset} does not support @previous",
            )
        snap = previous_user_snapshot()
        if snap is None:
            raise HTTPException(
                status_code=404, detail=f"no previous version for {collection}.{dataset}"
            )
        return snap

    if label.isdigit():
        snap = ds.snapshot(int(label))
        if snap is None:
            raise HTTPException(
                status_code=404,
                detail=f"snapshot {label} not found for {collection}.{dataset}",
            )
        return snap

    from opteryx_catalog.exceptions import TagNotFound

    try:
        snapshot_id = workspace.resolve_tag(f"{collection}.{dataset}", label)
    except TagNotFound:
        raise HTTPException(
            status_code=404, detail=f"tag {label} not found for {collection}.{dataset}"
        )
    snap = ds.snapshot(snapshot_id)
    if snap is None:
        raise HTTPException(
            status_code=404,
            detail=f"tag {label} names a snapshot that could not be read",
        )
    return snap


@router.get(
    "/{workstream}/{collection}/{dataset}/$metadata",
    summary="Per-dataset OData EDMX metadata",
    description=(
        "Returns OData $metadata (EDMX) for a single dataset, including column types and "
        "nullability, plus custom annotations carrying column statistics "
        "(Custom.Statistics.Min/Max, Min/MaxPrefix for text columns, DistinctValueCount, "
        "NullCount, Distribution, and CIDR for "
        "IPv4 columns), the source type name (Custom.OriginalType, Custom.SourceType), the "
        "caller's access (Custom.Role, Custom.Policy), dataset and column descriptions "
        "(Custom.Description, Custom.LLMDescribed), latest-snapshot metadata "
        "(Custom.Snapshot.Id/TotalRecords/TotalDataSize/CommitMessage/Author/ProducedBy, the "
        "last naming what made the commit as `kind:name` -- the segment after the colon is a "
        "catalog object for `task` and `view`, and a channel for `upload`), physical sort "
        "order (Custom.SortOrder.Column/Direction), snapshot tags (Custom.Tags.Count and "
        "Custom.Tags, a Collection of Records with Name, SnapshotId and CreatedBy), vector "
        "indexes (Custom.VectorIndexes, a Collection of Records with Name, Column, Method "
        "and Build), "
        "the dataset this one was FORKED from, if any (Custom.Fork, a Record with Upstream, "
        "RevisionsBehind, RevisionsAhead, BaseSnapshot, LastSyncMs and ForkedBy -- the two "
        "revision figures are UPPER BOUNDS, since a sequence number advances on every commit "
        "including maintenance, so render them as \"at most N\"; zero is exact, and the "
        "annotation is absent entirely for a dataset nobody cloned), the "
        "standing source list the current content was built from (Custom.Sources.Complete "
        "and Custom.Sources, a Collection of Records with Dataset and Visible), the commit "
        "receipt of the snapshot described (Custom.ReadSources.Reported, "
        "Custom.ReadSources.Truncated and Custom.ReadSources, a Collection of Records "
        "with Dataset, SnapshotId, ResolvedBy and Visible), and who READ that version "
        "(Custom.Consumers.Truncated and Custom.Consumers, a Collection of Records with "
        "Dataset, Visible, SnapshotId - the consumer's own version - Commits and ProducedBy; "
        "omitted entirely, flag included, when the lookup could not run, so an empty "
        "Collection beside the flag means nothing read it). Both name every source, including "
        "datasets the caller holds no grant on - a name is not readable data, and "
        "provenance that cannot be followed is not provenance - and Visible says which "
        "of those names the caller may actually read, so a client can decline to offer "
        "a link that would 403 rather than withholding the citation. Also "
        "materialized-view state (Custom.MaterializedView.*). Annotations are omitted where "
        "they do not apply to the dataset kind or are unavailable. "
        "The dataset segment may carry a `@{label}` version selector -- `dataset@current`, "
        "`dataset@previous`, `dataset@{tag}`, or `dataset@{snapshot_id}` -- and the "
        "Custom.Snapshot.* and Custom.ReadSources.* annotations describe that version "
        "rather than the current one. Custom.Tags and Custom.Sources do NOT: a tag is a "
        "pin on the dataset and the source list is a property of its current content, so "
        "both are the same on every version's document."
    ),
    # The description above is written for clients that read the spec; the docs
    # site renders this instead. It is the only human-facing reference for these
    # annotations, so it keeps every one, as a table rather than one sentence.
    openapi_extra={
        "x-docs-summary": "\n".join([
            "Returns the OData `$metadata` (EDMX) document for one dataset: column types and "
            "nullability, plus the `Custom.*` annotations below. An annotation is left out "
            "where it doesn't apply to the dataset's kind or isn't available.",
            "",
            "| Annotation | What it carries |",
            "| --- | --- |",
            "| `Custom.Statistics.*` | Per-column `Min`/`Max` (`MinPrefix`/`MaxPrefix` for text), "
            "`DistinctValueCount`, `NullCount`, `Distribution`, and `CIDR` for IPv4 columns |",
            "| `Custom.OriginalType`, `Custom.SourceType` | The column's type in its source |",
            "| `Custom.Role`, `Custom.Policy` | Your access to the dataset |",
            "| `Custom.Description`, `Custom.LLMDescribed` | Dataset and column descriptions |",
            "| `Custom.Snapshot.*` | `Id`, `TotalRecords`, `TotalDataSize`, `CommitMessage`, "
            "`Author`, and `ProducedBy` - what made the commit, as `kind:name` |",
            "| `Custom.SortOrder.*` | Physical sort `Column` and `Direction` |",
            "| `Custom.Tags`, `Custom.Tags.Count` | Snapshot tags: `Name`, `SnapshotId`, `CreatedBy` |",
            "| `Custom.VectorIndexes` | Vector indexes: `Name`, `Column`, `Method`, `Build` |",
            "| `Custom.Fork` | The dataset this one was forked from: `Upstream`, "
            "`RevisionsBehind`, `RevisionsAhead`, `BaseSnapshot`, `LastSyncMs`, `ForkedBy`. "
            "The revision counts are upper bounds (zero is exact). Absent if it isn't a fork |",
            "| `Custom.Sources`, `Custom.Sources.Complete` | The datasets the current content "
            "was built from: `Dataset`, `Visible` |",
            "| `Custom.ReadSources.*` | What this snapshot's commit read: `Dataset`, "
            "`SnapshotId`, `ResolvedBy`, `Visible`, plus `Reported` and `Truncated` flags |",
            "| `Custom.Consumers.*` | Who read this version: `Dataset`, `SnapshotId`, "
            "`Commits`, `ProducedBy`, `Visible`, plus a `Truncated` flag. Absent if the "
            "lookup couldn't run; an empty list means nothing read it |",
            "| `Custom.MaterializedView.*` | Materialized-view state |",
            "",
            "Source and consumer lists name every dataset, including ones you can't read; "
            "`Visible` says which you can.",
            "",
            "Add a `@{label}` version selector (`dataset@previous`, `dataset@{tag}`, "
            "`dataset@{snapshot_id}`) to describe another version. `Custom.Snapshot.*` and "
            "`Custom.ReadSources.*` follow the label; `Custom.Tags` and `Custom.Sources` "
            "always describe the dataset as it is now.",
        ])
    },
    responses={
        200: {"description": "EDMX metadata document returned as XML"},
        400: {"description": "Malformed @{label} version selector, or the dataset kind does not support snapshot versioning"},
        401: {"description": "Missing or invalid authentication"},
        403: {"description": "Forbidden: no permission to view dataset metadata"},
        404: {"description": "Dataset not found in catalog, or the @{label} version selector names a tag/snapshot/previous version that does not exist"},
        406: {"description": "Accept header does not admit application/xml, the only format this route returns"},
    },
    dependencies=[Depends(require_acceptable(MEDIA_XML))],
)
def dataset_metadata(
    request: Request,
    response: Response,
    workstream: str,
    collection: str,
    dataset: str,
) -> Any:
    """Return per-dataset OData $metadata (EDMX) with enriched column statistics.

    The EDMX response includes:
    - Schema definition with EntityType describing columns, types, and nullability
    - EntitySet declaration for this dataset
    - Custom annotations for statistics:
      * Custom.Statistics.Min/Max: min/max column values (if available)
      * Custom.Statistics.MinPrefix/MaxPrefix: the leading characters of a TEXT
        column's bounds. Text has no Min/Max because its manifest bound is an
        ordinal key over the value's first bytes, which recovers a prefix and not
        the value -- and a prefix is not an upper bound.
      * Custom.Statistics.DistinctValueCount: cardinality (exact or estimated)
      * Custom.Statistics.Distribution: histogram of value distribution
      * Custom.OriginalType: source database type (for mapped types like DECIMAL→Edm.Double)
      * Custom.Snapshot.TotalRecords: row count at last snapshot (table only)
      * Custom.Snapshot.Author: author of the last snapshot commit (table only)
      * Custom.Snapshot.ProducedBy: what made the commit, as `kind:name` --
        `task:<workspace.collection.name>`, `view:<workspace.collection.name>`
        or `upload:<channel>`. The segment after the colon is a catalog object
        for `task` and `view`, and a CHANNEL (`api`, `web`, `gcs-logs`) for
        `upload`, so anything resolving it must branch on the kind first.
        Omitted when the commit was a statement run by hand, which is a state
        and not a missing record. Named in full for every caller, as
        Custom.Sources is.
      * Custom.SortOrder.Column/Custom.SortOrder.Direction: the column the
        underlying storage is physically sorted by, if any (table only)
      * Custom.VectorIndexes: a Collection of Records, one per vector index
        defined on the dataset, omitted when there are none. Each Record
        carries Name, Column, Method and Build. Coverage of the head snapshot
        is not included; SHOW INDEXES FROM answers that.
      * Custom.Tags.Count: how many snapshot tags the dataset holds, 0 included.
        Present only for catalog-backed table-like datasets -- its absence means
        the dataset cannot hold a tag, not that it holds none.
      * Custom.Tags: a Collection of Records, one per tag, omitted when there
        are none. Each Record carries Name, SnapshotId (the pinned snapshot,
        whose id is also its millisecond timestamp) and CreatedBy.
      * Custom.Sources.Complete: whether the standing source list below may be
        missing a name (the 64 cap, or a commit with no receipt). Present for
        the same kinds as Custom.Tags.Count, and false for a dataset from
        before receipts existed.
      * Custom.Sources: a Collection of Records, one per dataset the current
        content was built from, most recent first, omitted when the list is
        empty. Each Record carries Dataset. Every source is named, including
        one in a workspace the caller holds no grant on -- the name is a
        citation, and reading the dataset behind it still needs a grant.
      * Custom.ReadSources.Reported: whether the commit that produced the
        snapshot this document describes reported what it read. False means
        nothing is on record; it is not "read nothing", which is an empty
        receipt with Reported true.
      * Custom.ReadSources.Truncated: whether the receipt hit its 256-entry
        cap. Emitted only when Reported is true.
      * Custom.ReadSources: a Collection of Records, one per (dataset,
        snapshot) that commit read, omitted when the receipt is empty. Each
        Record carries Dataset, SnapshotId and ResolvedBy (how the version was
        chosen: current, version, previous, tag or date). Named in full for
        every caller, as Custom.Sources is.

    Also includes access-control annotations:
    - Custom.Role: user's assigned role (reader, writer, admin, owner)
    - Custom.Policy: policy that granted access

    `<workspace>/information_schema/{tables,columns,views,triggers,tasks,schemata,column_relationships,grants}` are virtual,
    non-catalog datasets (source "Virtual") with a fixed schema (see
    app.odata.virtual_datasets). Each is offered in a workspace the caller can
    otherwise reach when they hold a role there that confers the action the engine
    gates its rows on: the shape tables to any role, `triggers`, `tasks` and
    `grants` to owners (see app.auth.permissions.virtual_information_schema_role).
    Which rows are in it is decided downstream, at query time.

    Content-Type: application/xml (EDMX)

    Example: GET /api/v4/workspace/collection/dataset/$metadata

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    # Split off a `@{label}` version selector, if present. Every use of `dataset`
    # below this point -- catalog identifiers, entity names, permission checks --
    # is the true dataset name; the label is resolved separately, once the
    # dataset object is loaded (see _resolve_labeled_snapshot below).
    try:
        dataset, label = split_dataset_label(dataset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    table_name = f"{workstream}.{collection}.{dataset}"

    # Authorization: determine whether the authenticated user may view this dataset's metadata
    # Get claims provided by the authentication dependency
    claims = getattr(request.state, "claims", {}) or {}
    identity = claims.get("identity")

    from app.auth.permissions import is_anonymous_exempt

    if identity is None and not is_anonymous_exempt(workstream, collection):
        raise HTTPException(status_code=401, detail="Missing or invalid credentials")

    from app.auth.permissions import entitlements_from_claims
    from app.auth.permissions import get_access_policies_from_claims
    from app.auth.permissions import read_grant_for_relation
    from app.auth.permissions import virtual_information_schema_role

    # Default: no access
    allowed = False
    assigned_role = None
    assigned_policy = None

    # Whether this request is for one of the virtual, non-catalog information_schema
    # datasets rather than a real one.
    is_virtual_info_schema = is_virtual_information_schema(collection, dataset)

    access_policies_list = get_access_policies_from_claims(claims)
    entitlements_list = sorted(entitlements_from_claims(claims))

    # The virtual information_schema datasets have no catalog entry for a policy to
    # name, so they are not granted per relation. One is offered in a workstream
    # when the caller holds a role there conferring the action the engine gates its
    # rows on - `personal` and `public` included, which the helper answers the way
    # the engine answers implicit grants. Decided first because it is the whole
    # decision for these datasets: the branches below are for catalog objects.
    if is_virtual_info_schema:
        role = virtual_information_schema_role(
            access_policies_list, workstream, dataset, entitlements=entitlements_list
        )
        if role is None:
            raise HTTPException(status_code=403, detail="forbidden: not allowed to view metadata")
        allowed = True
        assigned_role = role
        assigned_policy = "virtual-information-schema"

    # Personal workspace handling
    if not allowed and workstream == "personal":
        # the collection is the identity for personal workspaces
        if collection == identity:
            allowed = True
            assigned_role = "owner"
            assigned_policy = "always-allow-personal"
        else:
            # deny access to other users' personal workspaces
            raise HTTPException(status_code=403, detail="forbidden: not allowed to view metadata")

    # Public workspace -> read-only
    if not allowed and workstream == "public":
        from app.auth.permissions import PUBLIC_READ_POLICY

        allowed = True
        assigned_role = "reader"
        assigned_policy = PUBLIC_READ_POLICY

    # Policy-granted access
    if not allowed:
        # The caller needs a read grant on THIS relation. Matching on the pattern's
        # workstream alone would let a grant on one dataset disclose every dataset
        # beside it in the same workspace.
        grant = read_grant_for_relation(access_policies_list, table_name)
        if grant is not None:
            allowed = True
            assigned_role, assigned_policy = grant

    if not allowed:
        raise HTTPException(status_code=403, detail="forbidden: not allowed to view metadata")

    # Everything the document's content depends on is now known, so a cached
    # rendering can be served without touching the catalog at all.
    #
    # NO ETag on this document (decision 2026-09-09). The service-wide $metadata
    # is polled -- Power BI refetches it on every refresh -- and a validator
    # earns its keep there. This one is opened a handful of times a session from
    # the Studio UI, so revalidation saved little, and tying the document's
    # identity to a hash of its bytes constrains what may go IN it: anything
    # that changes when a DIFFERENT dataset changes (a downstream consumer list,
    # say) would churn the validator for reasons that have nothing to do with
    # the version being described. See metadata_cache.xml_response.
    # Relationships are resolved BEFORE the cache is consulted because they are
    # part of the key. Two callers can hold the same role on THIS dataset and
    # differ in what they may read on the far side of a relationship, and a key
    # of (table, role, policy) alone would hand one of them the other's
    # document -- the same shape of leak the client-side $metadata cache had.
    # Naming the visible relationships (not the caller) keeps callers who see
    # the same thing sharing an entry, which is most of them.
    relationships = (
        []
        if is_virtual_info_schema
        else _declared_relationships(
            workstream, collection, dataset, identity, access_policies_list
        )
    )
    inbound_relationships = (
        []
        if is_virtual_info_schema
        else _inbound_relationships(
            workstream, collection, dataset, identity, access_policies_list
        )
    )
    relationship_key = tuple(
        sorted(str(row.get("name") or "") for row in relationships)
    ) + tuple(
        sorted(
            f"{row.get('collection')}.{row.get('dataset')}.{row.get('name')}"
            for row in inbound_relationships
        )
    )
    # The standing source list (PROVENANCE_DESIGN.md S2.2) is read here, before
    # the cache, because it is part of the key: the document is rebuilt when
    # the list changes rather than serving a provenance record the catalog has
    # already moved past. It costs nothing to key on -- the read happens on
    # every request either way, since `load_dataset` reads the same document
    # again further down.
    #
    # The NAMES do not vary by caller -- provenance names its sources whether
    # or not the caller can read them (see `Custom.Sources` below) -- but the
    # `Visible` flag beside each one does, so the readability verdict goes in
    # the key too. Keying on the verdict rather than on the caller keeps every
    # caller who can follow the same subset of sources on one entry, which is
    # the shape `relationship_key` above uses for the same reason.
    #
    # The RECEIPT's names are not known until the snapshot is loaded further
    # down, and they can name datasets the standing list does not (an older
    # version read something the current content no longer carries). Its flags
    # therefore cannot be keyed on what is known here, which is why
    # `policy_fingerprint` is in the key as well: it is the only thing that
    # bounds what the receipt's flags can say.
    #
    # The `@{label}` selector IS in the key: `dataset@{old snapshot}` and the
    # bare name are different documents -- different receipt, different
    # Custom.Snapshot.* -- under one dataset name.
    source_names, sources_complete = (
        ([], False)
        if is_virtual_info_schema
        else _standing_sources(workstream, collection, dataset)
    )
    sources = [str(name) for name in source_names]
    source_readable = {
        name: _source_is_readable(name, identity, access_policies_list) for name in sources
    }
    # Beside the standing sources, and read from the same document. A fork's
    # divergence moves when either side commits, so it goes in the cache key
    # too - otherwise "at most 3 revisions behind" would keep being served
    # after a resync made it zero.
    fork = None if is_virtual_info_schema else _fork_relationship(workstream, collection, dataset)
    sources_key = (
        tuple((name, source_readable[name]) for name in sources),
        sources_complete,
        tuple(sorted(fork.items())) if fork else None,
    )
    cache_key = (
        table_name,
        label,
        assigned_role,
        assigned_policy,
        is_virtual_info_schema,
        relationship_key,
        sources_key,
        _policy_fingerprint(access_policies_list),
    )
    cached = metadata_cache_get(cache_key)
    if cached is not None:
        # The stored validator is unused here; the cache is shared with the
        # service-wide document, which still carries one.
        _, cached_xml, cached_headers = cached
        return xml_response(cached_xml, cached_headers)

    # Try to get enriched metadata from catalog
    col_names = []
    col_types = []
    col_metadata = {}  # Maps col_name -> {type, min, max, cardinality, description}
    col_id_to_name = {}  # Maps catalog column id -> name, for resolving sort_orders
    # Whether a materialized view has actually been built yet. False between
    # CREATE MATERIALIZED VIEW and the first refresh, when the catalog record
    # exists but there is no schema and no snapshot behind it.
    mv_materialized = False
    # The loaded catalog object, for the branches that have one. Initialised
    # here because the renderer below reads `ds.metadata` for the description
    # annotations inside a try/except that swallows anything - so a branch
    # that never binds it produced a NameError which silently dropped every
    # annotation after that point, sort order included.
    ds = None
    # A task's registration record, for the one branch that has one. Initialised
    # beside `ds` and for exactly the same reason: the annotation block below
    # reads it, and a branch that never bound it would raise NameError there.
    task = None
    # Snapshot tags held by this dataset, newest catalog wheels only. Empty for
    # anything that cannot hold one (a view, a virtual table, an external
    # projection) as well as for a table that simply has none -- the
    # Custom.Tags.Count annotation below is emitted only for the kinds that
    # can, so a client can still tell those two apart.
    tags = []
    # Vector index definitions on this dataset (opteryx-core
    # VECTOR_INDEX_DESIGN.md), read beside the tags and gated the same way.
    vector_indexes = []
    # The commit receipt of the snapshot this document describes -- see the
    # renderer below. None for everything that has no snapshot to carry one: a
    # view, a virtual table, an external projection, and a materialized view
    # that has never been refreshed.
    receipt = None

    # A projection of a customer's own catalog, not a dataset this catalog
    # owns. Checked before the catalog path because that path cannot answer
    # for one: see `_external_catalog_stub`.
    stub = (
        None
        if is_virtual_info_schema
        else _external_catalog_stub(workstream, collection, dataset)
    )

    if is_virtual_info_schema:
        # No catalog entry backs this dataset -- its schema is fixed.
        source_type = "Virtual"
        response.headers["X-Dataset-Source"] = source_type
        virtual_columns = VIRTUAL_INFORMATION_SCHEMA_TABLES[dataset]
        col_names = [col.name for col in virtual_columns]
        col_metadata = {
            col.name: {
                "type": col.type,
                "facets": col.facets,
                "source_type": col.source_type,
            }
            for col in virtual_columns
        }
        sort_order_column = None
        sort_order_ascending = True
        last_ts_ms = None
        snapshot_info = {}
    elif stub is not None:
        # Everything below comes off the one document already read. No catalog
        # handle, no GCS fetch, no external round trip - the projection exists
        # precisely so this endpoint does not need any of them.
        source_type = EXTERNAL_TABLE_SOURCE
        response.headers["X-Dataset-Source"] = source_type

        for column in stub.get("schema") or []:
            if not isinstance(column, dict) or not column.get("name"):
                continue
            col_name = column["name"]
            col_names.append(col_name)
            field_id = column.get("id")
            if field_id is not None:
                col_id_to_name[field_id] = col_name
            # `_stored_type_display` is the catalog's own renderer for a
            # persisted column, so a projected column reports the same EDM type
            # a native one with the same stored spelling would.
            col = edm_property(
                col_name,
                _stored_type_display(column),
                column.get("element-type") or column.get("element_type"),
            )
            col_types.append(col.type)
            col_metadata[col_name] = {
                "type": col.type,
                "facets": col.facets,
                "source_type": col.source_type,
                "nullable": bool(column.get("nullable", True)),
            }

        if not col_names:
            # A stub whose table could not be opened when the list was last
            # refreshed carries a name and nothing else. That is a listing that
            # needs refreshing, not a dataset that does not exist - and 404 is
            # the wrong thing to tell a client whose tree just showed it.
            raise HTTPException(
                status_code=404,
                detail="this dataset comes from an external catalog and its columns have "
                "not been projected yet; refresh the workspace's dataset list",
            )

        # Per-column statistics, when the workspace refresh wrote a manifest
        # for this table. See `_stub_column_statistics` for why this is not the
        # free-of-charge branch it reads like.
        _merge_column_statistics(
            col_metadata, _stub_column_statistics(workstream, collection, dataset, stub)
        )

        sort_order_column = None
        sort_order_ascending = True
        try:
            from opteryx_catalog.catalog.sort_order import normalize_sort_order

            normalized = normalize_sort_order(stub.get("sort-orders"))
            if normalized:
                sort_order_ascending = normalized.get("ascending", True)
                target_name = normalized.get("name")
                target_fid = normalized.get("field_id")
                if target_fid is not None:
                    sort_order_column = col_id_to_name.get(target_fid)
                if sort_order_column is None and target_name in col_names:
                    sort_order_column = target_name
        except Exception:
            sort_order_column = None

        last_ts_ms = stub.get("timestamp-ms")
        if last_ts_ms:
            response.headers["X-Dataset-LastModified"] = str(last_ts_ms)

        snapshot_info = {}
        statistics = stub.get("statistics") or {}
        for key, field in (
            ("row-count", "total_records"),
            ("total-file-size-bytes", "total_data_size"),
        ):
            value = statistics.get(key)
            if value is not None:
                try:
                    snapshot_info[field] = int(value)
                except (TypeError, ValueError):
                    pass
    else:
        try:
            workspace = OpteryxCatalog(
                workstream,
                firestore_project=GCP_PROJECT_ID,
                firestore_database=_firestore_database(),
                gcs_bucket=_bucket_name(),
            )

            # Try to load as a dataset first, then as a view, then as a task.
            ds = None
            task = None
            source_type = "Table"
            try:
                ds = workspace.load_dataset(f"{collection}.{dataset}")
                # A materialized view is a dataset whose metadata carries a
                # `dataset_type`; it is physically a table (schema, snapshots,
                # sort order) and only its label differs.
                source_type = _loaded_dataset_source_type(ds)
            except Exception:
                try:
                    ds = workspace.load_view(f"{collection}.{dataset}")
                    source_type = "View"
                except Exception as exc:
                    # Last rung, and the only one that costs an extra read --
                    # paid here, where the alternative was to 404. A task is a
                    # name in the same namespace as the two above, so this
                    # endpoint owes it an answer; it just has no storage, and
                    # therefore no `ds`.
                    task = _task_record(workspace, collection, dataset)
                    if task is None:
                        logger.error(
                            f"Failed to load dataset/view/task from catalog for {table_name}: {exc}"
                        )
                        raise HTTPException(
                            status_code=404, detail="dataset not found in catalog"
                        ) from exc
                    source_type = TASK_SOURCE
                    # Fixed, and known without a schema document: a task reads
                    # as the single row of its own definition.
                    for column in TASK_DEFINITION_COLUMNS:
                        col_names.append(column.name)
                        col_types.append(column.type)
                        col_metadata[column.name] = {
                            "type": column.type,
                            "facets": column.facets,
                            "source_type": column.source_type,
                        }

            # Set source header for clients
            response.headers["X-Dataset-Source"] = source_type

            # The current snapshot costs nothing: `snapshot()` with no argument
            # is answered from the metadata `load_dataset` already fetched
            # (SimpleDataset.snapshot -> DatasetMetadata.current_snapshot), so
            # it is read here rather than dispatched alongside the two calls
            # that really do go to the network.
            #
            # A `@{label}` selector is a hard requirement, not something to
            # degrade quietly the way the unlabeled path does: a caller who
            # asked for a specific tag/snapshot/previous version that doesn't
            # exist (or a dataset kind that doesn't support one) gets a 400/404
            # from _resolve_labeled_snapshot, not a metadata document silently
            # missing its Custom.Snapshot.* annotations.
            snapshot = None
            if label is None:
                try:
                    if (
                        source_type in TABLE_LIKE_SOURCES
                        and hasattr(ds, "snapshot")
                        and callable(getattr(ds, "snapshot"))
                    ):
                        snapshot = ds.snapshot()
                except Exception:
                    snapshot = None
            else:
                if source_type not in TABLE_LIKE_SOURCES or not (
                    hasattr(ds, "snapshot") and callable(getattr(ds, "snapshot"))
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=f"{collection}.{dataset} does not support snapshot versioning",
                    )
                snapshot = _resolve_labeled_snapshot(workspace, collection, dataset, ds, label)

            # Which immutable artifacts a description would be computed from --
            # None when there is no snapshot to pin it to, in which case the
            # description is fetched but not cached.
            snapshot_id = getattr(snapshot, "snapshot_id", None)
            describe_key = (
                (
                    table_name,
                    snapshot_id,
                    getattr(getattr(ds, "metadata", None), "current_schema_id", None),
                )
                if snapshot_id is not None
                else None
            )

            # schema, describe and tags are independent catalog calls (a
            # Firestore doc get, a GCS manifest fetch and a Firestore
            # subcollection stream respectively) with no data dependency on one
            # another, so fetch them concurrently instead of serially -- this
            # endpoint's latency is otherwise their sum rather than the slowest
            # of the three. Adding tags to the existing pool is what keeps this
            # annotation free: it finishes inside the window the other two
            # already cost.
            def _fetch_schema():
                try:
                    return ds.schema() if hasattr(ds, "schema") else None
                except Exception:
                    return None

            def _fetch_description():
                if describe_key is not None:
                    cached_description = describe_cache_get(describe_key)
                    if cached_description is not None:
                        return cached_description
                try:
                    description = ds.describe() if hasattr(ds, "describe") else None
                except Exception:
                    return None
                if describe_key is not None and description is not None:
                    describe_cache_put(describe_key, description)
                return description

            def _fetch_tags():
                # Only table-like datasets have snapshots, and a tag is a pin on
                # one -- asking for a view's tags would stream a subcollection
                # under a dataset document that does not exist, paying a round
                # trip to be told what is already known.
                if source_type not in TABLE_LIKE_SOURCES:
                    return []
                try:
                    return workspace.list_tags(f"{collection}.{dataset}")
                except Exception as exc:
                    # Best effort, like every other enrichment here: an older
                    # catalog wheel has no list_tags(), and a failed read must
                    # not cost the caller their whole metadata document. The
                    # cost of degrading is one missing badge.
                    logger.warning(f"Could not read tags for {table_name}: {exc}")
                    return []

            def _fetch_vector_indexes():
                # Definitions only -- one read of the dataset's `indexes`
                # subcollection. How far each index covers the head snapshot
                # needs the manifest, which is what SHOW INDEXES FROM is for;
                # this document only says which columns carry one.
                if source_type not in TABLE_LIKE_SOURCES:
                    return []
                lister = getattr(workspace, "list_vector_indexes", None)
                if lister is None:
                    # A catalog wheel older than vector indexes.
                    return []
                try:
                    return lister(f"{collection}.{dataset}")
                except Exception as exc:
                    logger.warning(f"Could not read vector indexes for {table_name}: {exc}")
                    return []

            with ThreadPoolExecutor(max_workers=4) as pool:
                schema_future = pool.submit(_fetch_schema)
                description_future = pool.submit(_fetch_description)
                tags_future = pool.submit(_fetch_tags)
                indexes_future = pool.submit(_fetch_vector_indexes)
                schema = schema_future.result()
                description = description_future.result()
                tags = tags_future.result()
                vector_indexes = indexes_future.result()

            if schema and hasattr(schema, "columns"):
                mv_materialized = True
                for field in schema.columns:
                    col_name = field.name
                    col_names.append(col_name)
                    field_id = getattr(field, "id", None)
                    if field_id is not None:
                        col_id_to_name[field_id] = col_name
                    # Map the catalog column type to its OData EDM type (unified mapper).
                    col = edm_property(
                        col_name, field.type, getattr(field, "element_type", None)
                    )
                    col_types.append(col.type)
                    col_metadata[col_name] = {
                        "type": col.type,
                        "facets": col.facets,
                        "source_type": col.source_type,
                        "nullable": field.nullable,
                    }
            else:
                # Two kinds legitimately have no schema, and neither is an error.
                #
                # A view whose CURRENT statement was registered before columns
                # were recorded carries none -- a view's columns belong to the
                # statement that produces them, and `View.schema()` answers None
                # when that statement has no `columns`. This used to be EVERY
                # view: `CatalogView` had no `schema()` at all, so the probe
                # above answered False for all of them and every view in the
                # service was described as one string column called `id`.
                #
                # A materialized view has none until its first refresh runs:
                # CREATE MATERIALIZED VIEW registers the dataset, its SQL, its
                # sources and its trigger, and the refresh is what produces the
                # schema and first snapshot -- so a freshly created MV sits in
                # exactly this state, and 404ing it made its details page
                # unreachable for the whole window between creation and first
                # refresh.
                #
                # Both get a single-column placeholder so the EDMX stays
                # structurally valid (an EntitySet's EntityType needs a key).
                # Custom.MaterializedView.Materialized, emitted below, is what tells
                # a client those columns are a placeholder and not the real schema.
                if source_type == TASK_SOURCE:
                    # Expected, and not a placeholder: a task has no schema
                    # document because it has no rows, and its columns were
                    # filled in from TASK_DEFINITION_COLUMNS when it was
                    # identified above. Nothing to do, and emphatically not a
                    # 404 -- the object exists and has just been read.
                    pass
                elif source_type in (MATERIALIZED_VIEW_SOURCE, "View"):
                    # Single string key placeholder
                    col_names = ["id"]
                    col_metadata = {"id": {"type": "Edm.String", "nullable": True}}
                else:
                    # For tables, lack of schema indicates invalid catalog state
                    raise HTTPException(status_code=404, detail="dataset schema unavailable in catalog")

            # Resolve the dataset's physical sort order (if any) to a column name, for the
            # Custom.SortOrder annotation below. This reuses the same sort_orders field and
            # id -> name -> index resolution precedence that resolve_sort_column applies
            # when deciding whether sort-preserving compaction is available, so it costs no
            # extra catalog round trip -- everything it needs (ds.metadata, schema columns)
            # is already fetched above.
            sort_order_column = None
            sort_order_ascending = True
            if source_type in TABLE_LIKE_SOURCES:
                try:
                    from opteryx_catalog.catalog.sort_order import normalize_sort_order

                    raw_sort_orders = getattr(getattr(ds, "metadata", None), "sort_orders", None)
                    normalized = normalize_sort_order(raw_sort_orders)
                    if normalized:
                        sort_order_ascending = normalized.get("ascending", True)
                        target_fid = normalized.get("field_id")
                        target_name = normalized.get("name")
                        target_index = normalized.get("index")
                        if target_fid is not None:
                            sort_order_column = col_id_to_name.get(target_fid)
                        if sort_order_column is None and target_name is not None:
                            sort_order_column = target_name if target_name in col_names else None
                        if (
                            sort_order_column is None
                            and target_index is not None
                            and 0 <= target_index < len(col_names)
                        ):
                            sort_order_column = col_names[target_index]
                except Exception:
                    sort_order_column = None

            # Statistics from describe(): min/max, cardinality, distribution.
            _merge_column_statistics(col_metadata, description)

            # Get snapshot for last modified info only for tables (views typically don't expose snapshot)
            last_ts_ms = None
            snapshot_info = {}
            try:
                if snapshot:
                    # Snapshot is an object with attributes
                    timestamp_ms = getattr(snapshot, "timestamp_ms", None)
                    if timestamp_ms:
                        response.headers["X-Dataset-LastModified"] = str(timestamp_ms)
                        last_ts_ms = timestamp_ms
                    # Collect snapshot id and summary where available
                    snapshot_id = getattr(snapshot, "snapshot_id", None)
                    if snapshot_id:
                        snapshot_info["snapshot_id"] = str(snapshot_id)
                        # Expose snapshot id in a header for quick visibility
                        response.headers["X-Dataset-Snapshot-Id"] = str(snapshot_id)
                    summary = getattr(snapshot, "summary", None)
                    if isinstance(summary, dict):
                        total_records = summary.get("total-records")
                        total_data_size = summary.get("total-data-size")
                        if total_records is not None:
                            snapshot_info["total_records"] = int(total_records)
                        if total_data_size is not None:
                            snapshot_info["total_data_size"] = int(total_data_size)
                    commit_message = getattr(snapshot, "commit_message", None)
                    if commit_message:
                        snapshot_info["commit_message"] = str(commit_message)
                    snapshot_author = getattr(snapshot, "author", None)
                    if snapshot_author:
                        snapshot_info["author"] = str(snapshot_author)
                    # WHAT MADE this commit (PROVENANCE_DESIGN.md S2.5), as
                    # `kind:name`. Absent is a STATE here and not a gap in the
                    # record -- it is a statement somebody ran by hand, made by
                    # no registered thing -- so nothing is collected for it and
                    # no annotation is emitted below. `author` is not a
                    # substitute: it is the identity the run executed as.
                    produced_by = getattr(snapshot, "produced_by", None)
                    if produced_by:
                        snapshot_info["produced_by"] = str(produced_by)
            except Exception:
                # Be defensive: missing or failing snapshot should not break metadata
                pass

            # THE RECEIPT (PROVENANCE_DESIGN.md S2.1): the (dataset, snapshot)
            # pairs the statement that produced THIS snapshot read, taken off
            # the snapshot the `@{label}` selector resolved to -- so
            # `dataset@{id}` reports what that version was built from, not what
            # the current one was.
            #
            # It answers a different question from the standing source list
            # rendered beside it, which is why both are here: the list names
            # every dataset whose data is in the current content, at no
            # particular version; the receipt names the exact versions ONE
            # commit read, which is what re-deriving its output needs.
            #
            # A receipt of `None` is a writer that did not report (or one older
            # than receipts), which is why "reported" is sent separately: `[]`
            # is the honest receipt of a statement that read no catalog
            # relation -- an upload, a literal INSERT, a compaction -- and a
            # client must not read that as "unknown".
            if snapshot is not None:
                entries = getattr(snapshot, "read_sources", None)
                receipt = {
                    "reported": entries is not None,
                    "truncated": bool(getattr(snapshot, "read_sources_truncated", False)),
                    "entries": _receipt_entries(entries),
                }
        except HTTPException:
            # re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Failed to load metadata from catalog: {e}\n{tb_str}")
            raise

    # Who read the version this document describes. Asked AFTER the snapshot is
    # resolved, so `dataset@{id}` reports the consumers of THAT version rather
    # than of the head -- which is the whole point of putting it here rather
    # than on a route that would have to be told the version separately.
    consumers = _consumers_of(
        table_name,
        (snapshot_info or {}).get("snapshot_id"),
        source_type,
    )

    # Build EDMX XML with annotations using ElementTree
    NS_EDMX = "http://docs.oasis-open.org/odata/ns/edmx"
    NS_EDM = "http://docs.oasis-open.org/odata/ns/edm"

    # Entity naming: use underscore form for CSDL SimpleIdentifier compliance
    entity_set_name = f"{workstream}_{collection}_{dataset}"
    entity_type_name = f"{collection}_{dataset}"
    schema_namespace = f"{collection}"

    # Root EDMX element
    edmx = ET.Element(f"{{{NS_EDMX}}}Edmx", attrib={"Version": "4.0"})

    # DataServices
    data_services = ET.SubElement(edmx, f"{{{NS_EDMX}}}DataServices")

    # Schema
    schema = ET.SubElement(
        data_services,
        f"{{{NS_EDM}}}Schema",
        attrib={
            "Namespace": schema_namespace,
            "xmlns": NS_EDM,
        },
    )

    # EntityType
    entity_type = ET.SubElement(schema, f"{{{NS_EDM}}}EntityType", attrib={"Name": entity_type_name})

    # Add Key element (use first column as key)
    if col_names:
        key = ET.SubElement(entity_type, f"{{{NS_EDM}}}Key")
        ET.SubElement(key, f"{{{NS_EDM}}}PropertyRef", attrib={"Name": col_names[0]})

    # Add Property elements with annotations for statistics
    for col_name in col_names:
        meta = col_metadata.get(col_name, {})
        edm_type = meta.get("type", EDM_UNKNOWN)

        # First property is key, must be non-nullable
        is_key = col_name == col_names[0]
        prop_attrib = {"Name": col_name, "Type": edm_type}
        if not is_key:
            prop_attrib["Nullable"] = "true"
        else:
            prop_attrib["Nullable"] = "false"
        # Facets are load-bearing, not decoration: CSDL defaults Scale to 0 and
        # temporal Precision to 0, either of which would misdescribe the rows.
        prop_attrib.update(meta.get("facets") or {})

        prop = ET.SubElement(entity_type, f"{{{NS_EDM}}}Property", attrib=prop_attrib)

        # Bounds are rendered in the property's own terms. An IPv4 column is
        # physically a UINT32 and its bounds arrive as the raw address
        # integers; emitting those verbatim would contradict the dotted-decimal
        # rows this same property serves, so they become addresses, plus the
        # smallest CIDR block enclosing the range -- the form an address range
        # is actually read in.
        stat_min = meta.get("min")
        stat_max = meta.get("max")
        range_cidr = None
        if is_ipv4_source(meta.get("source_type")):
            range_cidr = ipv4_enclosing_cidr(stat_min, stat_max)
            stat_min = ipv4_address(stat_min) or stat_min
            stat_max = ipv4_address(stat_max) or stat_max

        # Add annotations if we have statistics
        if stat_min is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.Min", "String": str(stat_min)},
            )

        # The platform type OData cannot express (unsigned width, IPv4 vs blob
        # vs JSON, decimal vs float). Emitted for every property so its absence
        # is never ambiguous.
        if meta.get("source_type"):
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": ORIGINAL_TYPE_TERM, "String": str(meta["source_type"])},
            )

        if stat_max is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.Max", "String": str(stat_max)},
            )

        # A text column has no Min/Max, and cannot: its manifest bound is an
        # ordinal key built from the value's leading bytes, so it recovers a
        # PREFIX. A prefix is a sound lower bound and an unsound upper one, so
        # publishing it as Min/Max would state as the maximum a string that is
        # not in the column. These terms are named for what they are, and a
        # client that does not know them ignores them rather than reading a
        # prefix as a value -- which is why this is not Min/Max with a flag.
        for term, value in (
            ("Custom.Statistics.MinPrefix", meta.get("min_display")),
            ("Custom.Statistics.MaxPrefix", meta.get("max_display")),
        ):
            if value:
                ET.SubElement(
                    prop,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": term, "String": str(value)},
                )

        # Only for address columns, and only when both bounds are real: a
        # block computed from one bound would claim a range the data does not
        # have.
        if range_cidr is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.CIDR", "String": range_cidr},
            )

        if "cardinality" in meta and meta["cardinality"] is not None and meta["cardinality"] > 0:
            cardinality_key = "DistinctValueCount"
            cardinality_value = meta["cardinality"]
            is_estimate = not bool(meta.get("cardinality_is_exact", False))

            # Cap cardinality estimate at total record count (can't have more unique values than rows)
            if is_estimate and "total_records" in snapshot_info:
                cardinality_value = min(cardinality_value, snapshot_info["total_records"])

            if is_estimate:
                cardinality_key += "Estimate"

            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={
                    "Term": f"Custom.Statistics.{cardinality_key}",
                    "Int": str(cardinality_value),
                },
            )

        # ARRAY columns only: how many distinct ELEMENTS the column holds,
        # pooled across every row's list. A different question from the count
        # above, which for an array column counts distinct LISTS -- and the one
        # a reader of a tag column actually has. Exactness is carried the same
        # way, in the term name, because it comes from the same kind of sketch.
        #
        # Not capped at total_records: a column of 58M rows can easily hold
        # more distinct elements than rows, since each row contributes a list.
        element_cardinality = meta.get("element_cardinality")
        if element_cardinality:
            element_key = "ElementDistinctValueCount"
            if not meta.get("element_cardinality_is_exact", False):
                element_key += "Estimate"
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={
                    "Term": f"Custom.Statistics.{element_key}",
                    "Int": str(element_cardinality),
                },
            )

        if "null_count" in meta and meta["null_count"] is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.NullCount", "Int": str(meta["null_count"])},
            )

        if "distribution" in meta and meta["distribution"] and any(meta["distribution"]):
            # Distribution as a Collection of Int values
            dist_ann = ET.SubElement(
                prop, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Statistics.Distribution"}
            )
            coll = ET.SubElement(dist_ann, f"{{{NS_EDM}}}Collection")
            for val in meta["distribution"]:
                ET.SubElement(coll, f"{{{NS_EDM}}}Int").text = str(val)

    # EntityContainer
    container = ET.SubElement(schema, f"{{{NS_EDM}}}EntityContainer", attrib={"Name": "Container"})

    # EntitySet
    entity_set = ET.SubElement(
        container,
        f"{{{NS_EDM}}}EntitySet",
        attrib={"Name": entity_set_name, "EntityType": f"{schema_namespace}.{entity_type_name}"},
    )

    # The dotted resource name behind the underscored identifiers above; the
    # service-wide document carries the same term for every entity set.
    ET.SubElement(
        entity_set,
        f"{{{NS_EDM}}}Annotation",
        attrib={"Term": RESOURCE_TERM, "String": f"{workstream}.{collection}.{dataset}"},
    )

    # Add source type annotation
    ET.SubElement(
        entity_set,
        f"{{{NS_EDM}}}Annotation",
        attrib={"Term": "Custom.SourceType", "String": source_type},
    )

    # Snapshot tags. The count is emitted for every table-like dataset, zero
    # included, so a client can tell "no tags" from "this service is too old to
    # say" and from "this kind cannot hold one" -- a view or an external
    # projection gets no annotation at all rather than a misleading 0. `ds is
    # not None` is what draws that line: the virtual and external-stub branches
    # answer without a catalog handle, and an external table's source type is
    # itself table-like, so source type alone would claim "0 tags" for a
    # dataset this service never asked about.
    #
    # A Collection of Records rather than a joined string: both clients read
    # this one document (the Studio's details panel and the Manage Dataset
    # page hit the same endpoint), and the Manage page lists each tag with the
    # date of the snapshot it pins and who created it. A flat name list cannot
    # carry those, and `list_tags()` returns them already -- so they are sent
    # rather than discarded, and the panel that only draws names ignores the
    # rest. Same shape Custom.Statistics.Distribution already uses for a
    # multi-valued annotation.
    #
    # SnapshotId doubles as the pinned snapshot's timestamp: a commit takes
    # `snapshot_id = int(time.time() * 1000)` and uses it as both the id and
    # the snapshot's timestamp_ms (see the "timestamp-ms" note in metadata.py),
    # so a client renders the tag's date from it without a second lookup. It
    # fits a JS number exactly -- ~1.8e12, well inside 2**53.
    if ds is not None and source_type in TABLE_LIKE_SOURCES:
        # A tag with no name is not renderable and not addressable -- it could
        # not be the target of a DROP TAG -- so it is dropped here rather than
        # sent as a row a client would have to defend against. `list_tags()`
        # falls back to the document id, which IS the name, so this should
        # never fire; it is counted before the count is emitted anyway, so a
        # client can never badge "2 tags" over a list of one.
        named = [tag for tag in tags if tag.get("name")]
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={"Term": "Custom.Tags.Count", "Int": str(len(named))},
        )
        if named:
            tags_annotation = ET.SubElement(
                entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Tags"}
            )
            collection_element = ET.SubElement(tags_annotation, f"{{{NS_EDM}}}Collection")
            for tag in named:
                record = ET.SubElement(collection_element, f"{{{NS_EDM}}}Record")
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={"Property": "Name", "String": str(tag["name"])},
                )
                snapshot_id = _as_tag_int(tag.get("snapshot-id"))
                if snapshot_id is not None:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "SnapshotId", "Int": str(snapshot_id)},
                    )
                created_by = tag.get("created-by")
                if created_by:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "CreatedBy", "String": str(created_by)},
                    )

    # Vector indexes: one Record per index, omitted when there are none. Name,
    # Column, Method and Build are the definition as stored; whether an index
    # has caught up with the head snapshot is NOT here (it needs the manifest)
    # -- a client that wants the state runs SHOW INDEXES FROM.
    named_indexes = [
        index for index in vector_indexes if index.get("name") and index.get("column")
    ]
    if ds is not None and source_type in TABLE_LIKE_SOURCES and named_indexes:
        indexes_annotation = ET.SubElement(
            entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.VectorIndexes"}
        )
        indexes_collection = ET.SubElement(indexes_annotation, f"{{{NS_EDM}}}Collection")
        for index in named_indexes:
            record = ET.SubElement(indexes_collection, f"{{{NS_EDM}}}Record")
            for prop, key in (
                ("Name", "name"),
                ("Column", "column"),
                ("Method", "method"),
                ("Build", "build"),
            ):
                value = index.get(key)
                if value:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": prop, "String": str(value)},
                    )

    # The standing source list -- what the CURRENT content was built from
    # (PROVENANCE_DESIGN.md S2.2) -- beside the tags and gated as they are:
    # only a catalog-backed table can have been built from anything, and
    # Custom.Sources.Complete is emitted even over an empty list so a client
    # can tell "nothing on record" from "older service", the reason
    # Custom.Tags.Count is emitted at 0. The values were read from the dataset
    # document before the cache check (see `sources_key`) rather than off `ds`,
    # so the document and the key it is cached under cannot disagree.
    #
    # EVERY SOURCE IS NAMED, whether or not the caller can read it. A source
    # list is a citation list: it records what this data was built from, and a
    # citation the reader lacks a subscription to is still a citation. Withheld
    # names were tried (an entry kept, its name nulled) and are not a weaker
    # version of this record, they are a different one -- provenance nobody can
    # follow, which is the only thing provenance is for.
    #
    # What that discloses is the NAME of a dataset in a workspace the caller
    # may hold no grant on, and nothing else: no schema, no statistics, no
    # rows. Reading it needs a grant like anything else -- and `Visible` beside
    # the name says whether this caller holds one, so a client can mark the
    # entry as a dead end instead of offering a link into a 403.
    #
    # A Collection of Records rather than a list of names so the shape matches
    # Custom.ReadSources below, which has more than a name per entry.
    if ds is not None and source_type in TABLE_LIKE_SOURCES:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={"Term": "Custom.Sources.Complete", "Bool": str(sources_complete).lower()},
        )
        if sources:
            sources_annotation = ET.SubElement(
                entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Sources"}
            )
            sources_collection = ET.SubElement(sources_annotation, f"{{{NS_EDM}}}Collection")
            for name in sources:
                record = ET.SubElement(sources_collection, f"{{{NS_EDM}}}Record")
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={"Property": "Dataset", "String": name},
                )
                # Whether the caller can FOLLOW the name, beside the name
                # itself. Not the elision this field used to be: the name is
                # there either way, and a client that ignores the flag still
                # renders a complete and truthful list. It exists so a client
                # can decline to offer a link that would 403, and say why.
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={
                        "Property": "Visible",
                        "Bool": str(source_readable.get(name, False)).lower(),
                    },
                )

    # What this dataset was FORKED from (FORKS_DESIGN.md S10.2), when it was.
    # Absent entirely for a dataset nobody cloned, which is nearly all of them -
    # an annotation emitted empty would make every client check a field that is
    # almost never set, where absence already says it.
    #
    # The upstream is NAMED whether or not the caller can read it, for the same
    # reason Custom.Sources names every source: this is a citation, and a
    # citation the reader lacks a grant for is still a citation. What it
    # discloses is a name and nothing else.
    #
    # RevisionsBehind and RevisionsAhead are UPPER BOUNDS, not commit counts - a
    # sequence number advances on every commit, maintenance included. A client
    # must render them as "at most N"; zero is exact.
    if ds is not None and fork:
        fork_annotation = ET.SubElement(
            entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Fork"}
        )
        fork_record = ET.SubElement(fork_annotation, f"{{{NS_EDM}}}Record")
        ET.SubElement(
            fork_record,
            f"{{{NS_EDM}}}PropertyValue",
            attrib={"Property": "Upstream", "String": fork["upstream"]},
        )
        ET.SubElement(
            fork_record,
            f"{{{NS_EDM}}}PropertyValue",
            attrib={"Property": "RevisionsBehind", "Int": str(fork["revisions_behind"])},
        )
        ET.SubElement(
            fork_record,
            f"{{{NS_EDM}}}PropertyValue",
            attrib={"Property": "RevisionsAhead", "Int": str(fork["revisions_ahead"])},
        )
        if fork.get("base_snapshot") is not None:
            ET.SubElement(
                fork_record,
                f"{{{NS_EDM}}}PropertyValue",
                attrib={"Property": "BaseSnapshot", "String": str(fork["base_snapshot"])},
            )
        if fork.get("last_sync_ms") is not None:
            ET.SubElement(
                fork_record,
                f"{{{NS_EDM}}}PropertyValue",
                attrib={"Property": "LastSyncMs", "String": str(fork["last_sync_ms"])},
            )
        if fork.get("forked_by"):
            ET.SubElement(
                fork_record,
                f"{{{NS_EDM}}}PropertyValue",
                attrib={"Property": "ForkedBy", "String": str(fork["forked_by"])},
            )

    # The receipt for the snapshot this document describes -- the exact
    # versions the commit that produced it read (PROVENANCE_DESIGN.md S2.1).
    #
    # Custom.ReadSources.Reported is emitted even over an empty receipt, for
    # the reason Custom.Sources.Complete is: `[]` (the statement read no
    # catalog relation) and "the writer said nothing" are different facts, and
    # only the first is something a client may act on. Truncated rides with it
    # so a client that sees MAX_READ_SOURCES entries knows whether there were
    # more.
    #
    # Named in full for every caller, on the same reading Custom.Sources above
    # is: a receipt that cannot be followed to the version it names is not a
    # receipt. `Visible` rides along for the same reason it does there. The version and how it was chosen go with the name for the same
    # reason -- they are what re-deriving the output needs, and a name without
    # them only says that something happened.
    if receipt is not None and source_type in TABLE_LIKE_SOURCES:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={"Term": "Custom.ReadSources.Reported", "Bool": str(receipt["reported"]).lower()},
        )
        if receipt["reported"]:
            ET.SubElement(
                entity_set,
                f"{{{NS_EDM}}}Annotation",
                attrib={
                    "Term": "Custom.ReadSources.Truncated",
                    "Bool": str(receipt["truncated"]).lower(),
                },
            )
        if receipt["entries"]:
            read_sources_annotation = ET.SubElement(
                entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.ReadSources"}
            )
            read_sources_collection = ET.SubElement(
                read_sources_annotation, f"{{{NS_EDM}}}Collection"
            )
            for entry in receipt["entries"]:
                record = ET.SubElement(read_sources_collection, f"{{{NS_EDM}}}Record")
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={"Property": "Dataset", "String": entry["dataset"]},
                )
                # As on Custom.Sources above: can the caller follow this name.
                # Computed here rather than looked up in `source_readable` --
                # a receipt for an older version can name a dataset the
                # current standing list no longer carries, so there is no
                # entry to look up.
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={
                        "Property": "Visible",
                        "Bool": str(
                            _source_is_readable(entry["dataset"], identity, access_policies_list)
                        ).lower(),
                    },
                )
                if entry["snapshot_id"] is not None:
                    # String, like Custom.Snapshot.Id and unlike Custom.Tags'
                    # SnapshotId: a snapshot id is a millisecond timestamp that
                    # fits a JS number today, and this document is read by
                    # browsers.
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "SnapshotId", "String": str(entry["snapshot_id"])},
                    )
                if entry["resolved_by"] is not None:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "ResolvedBy", "String": entry["resolved_by"]},
                    )

    # WHO READ THIS VERSION -- the downstream half, and the only question the
    # dataset's own documents cannot answer (S2.1 and S2.2 both look upwards).
    #
    # It lives HERE, on the version's own document, rather than behind a route
    # of its own. It is a fact about the version this document describes, it
    # follows the same `@{label}` selector Custom.ReadSources.* follows, and
    # this document is Studio's -- opened a handful of times a session, not
    # polled like the service-wide one, and carrying no validator for a
    # cross-dataset answer to churn.
    #
    # Named in full for every caller, as the two records above are, with
    # Visible saying who can follow the name. Cross-workspace by design: an
    # impact answer that will not say who is affected does not answer the
    # question it was asked.
    #
    # Truncated rides beside it so a client seeing the cap knows the list is
    # short. OMITTED ENTIRELY when the lookup could not run -- "nobody read
    # this version" and "we could not look" are different facts, and only the
    # Truncated flag's presence says which one an empty Collection is.
    if consumers is not None:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={
                "Term": "Custom.Consumers.Truncated",
                "Bool": str(bool(consumers.get("truncated"))).lower(),
            },
        )
        rows = consumers.get("consumers") or []
        if rows:
            consumers_annotation = ET.SubElement(
                entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Consumers"}
            )
            consumers_collection = ET.SubElement(
                consumers_annotation, f"{{{NS_EDM}}}Collection"
            )
            for row in rows:
                record = ET.SubElement(consumers_collection, f"{{{NS_EDM}}}Record")
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={"Property": "Dataset", "String": str(row.get("dataset"))},
                )
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={
                        "Property": "Visible",
                        "Bool": str(
                            _source_is_readable(
                                row.get("dataset"), identity, access_policies_list
                            )
                        ).lower(),
                    },
                )
                # The consumer's OWN version -- the most recent of its commits
                # that read this one. String for the reason Custom.Snapshot.Id
                # is: a millisecond timestamp read by browsers.
                if row.get("latest_snapshot_id") is not None:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={
                            "Property": "SnapshotId",
                            "String": str(row["latest_snapshot_id"]),
                        },
                    )
                # How many of its commits read this version. One row per
                # consuming DATASET, not per commit (impact.find_readers folds
                # them), so this is what the folding would otherwise lose.
                if row.get("commits") is not None:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "Commits", "Int": str(int(row["commits"]))},
                    )
                if row.get("produced_by"):
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "ProducedBy", "String": str(row["produced_by"])},
                    )

    # Declared relationships, as a Collection of Records -- the same shape
    # Custom.Tags uses, and for the same reason: a client needs more than one
    # value per entry.
    #
    # NOT a NavigationProperty here, unlike the service-wide document. A
    # NavigationProperty's Type must name an EntityType the document contains,
    # and a per-dataset document contains exactly one: this dataset. Pointing at
    # a table that is not in the document would not be a missing link, it would
    # be an invalid document that a strict client rejects whole.
    if relationships:
        relationships_annotation = ET.SubElement(
            entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.Relationships"}
        )
        relationships_collection = ET.SubElement(
            relationships_annotation, f"{{{NS_EDM}}}Collection"
        )
        for row in relationships:
            record = ET.SubElement(relationships_collection, f"{{{NS_EDM}}}Record")
            for property_name, value in (
                ("Name", row.get("name")),
                ("Column", row.get("column")),
                (
                    "ReferencedTable",
                    f"{row.get('references-collection')}.{row.get('references-dataset')}",
                ),
                ("ReferencedColumn", row.get("references-column")),
                ("Cardinality", row.get("cardinality")),
            ):
                if value:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": property_name, "String": str(value)},
                    )

    # The reverse direction: relationships declared elsewhere that point at a
    # column of this dataset. A separate term, not more Custom.Relationships
    # records, because they are not the same fact -- one says this column
    # points somewhere, the other says something points here -- and a client
    # that could not tell them apart would draw one arrow for both.
    if inbound_relationships:
        referenced_annotation = ET.SubElement(
            entity_set, f"{{{NS_EDM}}}Annotation", attrib={"Term": "Custom.ReferencedBy"}
        )
        referenced_collection_element = ET.SubElement(
            referenced_annotation, f"{{{NS_EDM}}}Collection"
        )
        for row in inbound_relationships:
            record = ET.SubElement(referenced_collection_element, f"{{{NS_EDM}}}Record")
            for property_name, value in (
                ("Name", row.get("name")),
                # The column ON THIS dataset that is being pointed at.
                ("Column", row.get("references-column")),
                ("ReferencingTable", f"{row.get('collection')}.{row.get('dataset')}"),
                ("ReferencingColumn", row.get("column")),
                ("Cardinality", row.get("cardinality")),
            ):
                if value:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": property_name, "String": str(value)},
                    )

    # Add access annotations (role, policy) when available
    if assigned_role:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={"Term": "Custom.Role", "String": assigned_role},
        )
    if assigned_policy:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={"Term": "Custom.Policy", "String": assigned_policy},
        )

    # A task's description is its own if it was given one at registration, and
    # otherwise the fixed sentence saying what a task is -- the same treatment
    # virtual datasets get below, for the same reason: there is no describe()
    # to fall back on, and an object with no description at all reads in the UI
    # as an object nobody can explain.
    if source_type == TASK_SOURCE:
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={
                "Term": "Custom.Description",
                "String": str((task or {}).get("description") or TASK_DESCRIPTION),
            },
        )

    # Virtual datasets have no catalog-backed metadata, so give them a fixed description
    # rather than leaving Custom.Description absent.
    if source_type == "Virtual":
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={
                "Term": "Custom.Description",
                "String": VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS.get(dataset, ""),
            },
        )

    # If this is a view, try to enrich EntitySet with view metadata
    if source_type == "View":
        try:
            definition = getattr(ds, "sql", None) or getattr(ds, "definition", None)
            if definition:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": "Custom.Definition", "String": str(definition)},
                )

            meta = getattr(ds, "metadata", None)
            if meta is not None:
                author = getattr(meta, "author", None)
                if author:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={"Term": "Custom.Author", "String": str(author)},
                    )

                description = getattr(meta, "description", None)
                if description:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={"Term": "Custom.Description", "String": str(description)},
                    )

                describer = getattr(meta, "describer", None)
                llm_flag = isinstance(describer, str) and (
                    describer.startswith("Gemini:") or describer.startswith("Llama-")
                )
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": "Custom.LLMDescribed", "Bool": str(llm_flag).lower()},
                )

                # If metadata timestamp present and header not set, set last modified header
                meta_ts = getattr(meta, "timestamp_ms", None)
                if meta_ts:
                    if not response.headers.get("X-Dataset-LastModified"):
                        response.headers["X-Dataset-LastModified"] = str(meta_ts)
                    last_ts_ms = meta_ts
        except Exception:
            pass

    # If this is a table (or a materialized view, which is one physically), add
    # snapshot and table-level metadata annotations
    if source_type in TABLE_LIKE_SOURCES:
        try:
            # Snapshot annotations
            if snapshot_info:
                sid = snapshot_info.get("snapshot_id")
                if sid:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={"Term": "Custom.Snapshot.Id", "String": str(sid)},
                    )
                if "total_records" in snapshot_info:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={
                            "Term": "Custom.Snapshot.TotalRecords",
                            "Int": str(snapshot_info["total_records"]),
                        },
                    )
                if "total_data_size" in snapshot_info:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={
                            "Term": "Custom.Snapshot.TotalDataSize",
                            "Int": str(snapshot_info["total_data_size"]),
                        },
                    )
                if "commit_message" in snapshot_info:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={
                            "Term": "Custom.Snapshot.CommitMessage",
                            "String": snapshot_info["commit_message"],
                        },
                    )
                if "author" in snapshot_info:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={
                            "Term": "Custom.Snapshot.Author",
                            "String": snapshot_info["author"],
                        },
                    )
                # Emitted verbatim, `kind:name` intact: the kind is what
                # decides what the name means (a catalog object for task and
                # view, a channel for upload), so splitting them here would
                # hand a client a name it cannot interpret. Named in full for
                # every caller, as Custom.Sources and Custom.ReadSources are.
                if "produced_by" in snapshot_info:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={
                            "Term": "Custom.Snapshot.ProducedBy",
                            "String": snapshot_info["produced_by"],
                        },
                    )

            # Table metadata
            meta = getattr(ds, "metadata", None)
            if meta is not None:
                descr = getattr(meta, "description", None)
                if descr:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={"Term": "Custom.Description", "String": str(descr)},
                    )
                describer = getattr(meta, "describer", None)
                llm_flag = isinstance(describer, str) and (
                    describer.startswith("Gemini:") or describer.startswith("Llama-")
                )
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": "Custom.LLMDescribed", "Bool": str(llm_flag).lower()},
                )

            # Sort order: the column the underlying storage is physically sorted by,
            # if any. Absent when the dataset has no configured sort order.
            if sort_order_column:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": "Custom.SortOrder.Column", "String": sort_order_column},
                )
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.SortOrder.Direction",
                        "String": "asc" if sort_order_ascending else "desc",
                    },
                )
        except Exception:
            pass

    # A task's registration record. Unlike the materialized-view block below,
    # nothing here needs a second catalog call: `_task_record` already read the
    # whole document (statement included) to decide this WAS a task, and `task`
    # is that record. A task with no rows and no schema is almost entirely
    # description, so these annotations are the document rather than a garnish
    # on it.
    if source_type == TASK_SOURCE and task is not None:
        # Custom.Definition is the term views and materialized views already use
        # for their defining SQL; a task's statement is the same kind of thing
        # and gets the same term, so a client that can already show one can show
        # this without learning a new name.
        if task.get("sql"):
            ET.SubElement(
                entity_set,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Definition", "String": str(task["sql"])},
            )
        # `runs-as` is deliberately NOT emitted, though the catalog stores it on
        # the task document. A task is a unit of work anyone may trigger, and
        # whether it runs follows the permissions of the caller who triggered
        # it. An act-as identity is a property of a TRIGGER -- the thing that
        # fires work when a dataset is updated, where that work needs rights the
        # updater may not have. Publishing it here invites a client to present
        # it as "the identity this task runs as", which is precisely the
        # privilege lending the task/trigger split exists to prevent.
        # WHAT THE TASK WRITES, derived from its own statement when it was
        # registered. Emitted because a trigger says which dataset FIRES a task
        # and nothing else says which dataset it FEEDS: without this, a client
        # drawing a pipeline has an edge going into a task and none coming out.
        # Comma-separated, matching `information_schema.tasks.writes`; omitted
        # entirely when the task writes nothing, or was registered by a catalog
        # too old to have recorded it.
        writes = task.get("writes") or []
        if writes:
            ET.SubElement(
                entity_set,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Task.Writes", "String": ",".join(writes)},
            )
        for term, key in (
            ("Custom.Task.CreatedBy", "created-by"),
            ("Custom.Task.LastUpdatedBy", "last-updated-by"),
            ("Custom.Task.LastFiredStatus", "last-fired-status"),
            ("Custom.Task.StatementId", "statement-id"),
        ):
            value = task.get(key)
            if value:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": term, "String": str(value)},
                )
        for term, key in (
            ("Custom.Task.CreatedAt", "created-at-ms"),
            ("Custom.Task.LastUpdatedAt", "last-updated-at-ms"),
            ("Custom.Task.LastFiredAt", "last-fired-at-ms"),
            # The version the last SUCCESSFUL run consumed to. Stamped only on
            # success, so a gap behind a failed run stays visible.
            ("Custom.Task.LastWindowTo", "last-window-to"),
        ):
            value = task.get(key)
            if value is not None:
                try:
                    ET.SubElement(
                        entity_set,
                        f"{{{NS_EDM}}}Annotation",
                        attrib={"Term": term, "Int": str(int(value))},
                    )
                except (TypeError, ValueError):
                    pass
        # There is deliberately NO Custom.Task.Suspended here, and there was:
        # emitted unconditionally so that `false` would mean "running" rather
        # than "this service is too old to say". It was always false. Nothing
        # writes `suspended-at-ms` on a task document, because A TASK CANNOT BE
        # SUSPENDED -- a task is WHAT runs and a trigger is WHEN, and what you
        # suppress is the when. The catalog's suspend setters are
        # set_trigger_suspended and set_materialized_view_suspended; there is no
        # third one to wire this to, and a field with one possible value tells a
        # client "this workflow is running" about a workflow that may well be
        # paused. Suspension of a task's schedule is read from its TRIGGERS:
        # information_schema.triggers.suspended_at, which is projected as of
        # opteryx-core 0.9.94 (the floor in pyproject.toml).

    # A materialized view additionally has a registration record: the SQL it is
    # refreshed from, the tables it reads, and its last refresh outcome. Best
    # effort only -- older catalog wheels have no get_materialized_view(), and a
    # missing record must not break an otherwise valid metadata document.
    if source_type == MATERIALIZED_VIEW_SOURCE:
        # Has this MV ever been built? False means the Property list above is the
        # placeholder, not a schema, and there is no snapshot behind it -- a state
        # a client cannot otherwise tell apart from an MV that really does hold one
        # nullable string column. Emitted unconditionally so its absence means
        # "older service", never "not yet materialized".
        ET.SubElement(
            entity_set,
            f"{{{NS_EDM}}}Annotation",
            attrib={
                "Term": "Custom.MaterializedView.Materialized",
                "Bool": str(mv_materialized).lower(),
            },
        )
        try:
            mv = workspace.get_materialized_view(f"{collection}.{dataset}")
            # Custom.Definition is the term views already use for defining SQL.
            if mv.get("sql"):
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={"Term": "Custom.Definition", "String": str(mv["sql"])},
                )
            source_tables = mv.get("source-tables") or []
            if source_tables:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.SourceTables",
                        "String": ",".join(str(t) for t in source_tables),
                    },
                )
            refreshed_at = mv.get("last-refreshed-at-ms")
            if refreshed_at:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.LastRefreshed",
                        "Int": str(int(refreshed_at)),
                    },
                )
            # Whether an operator has PAUSED this view's refresh, as opposed to
            # its refresh being broken. `last-refresh-status` still reports the
            # last real refresh outcome and is deliberately untouched by a
            # suspension, so a suspended view whose last refresh succeeded reads
            # "ok" and looks entirely healthy without this. Emitted
            # unconditionally, unlike the annotations around it, so `false`
            # means "refreshing" rather than "this service is too old to say" --
            # the distinction a client cannot otherwise draw from an absent
            # annotation.
            #
            # Nothing equivalent exists on information_schema: a materialized
            # view lists there as an ordinary BASE TABLE (the engine's `tables`
            # projection knows only BASE TABLE and VIEW), so this document is
            # currently the only place the state is legible at all.
            suspended_at = mv.get("suspended-at-ms")
            ET.SubElement(
                entity_set,
                f"{{{NS_EDM}}}Annotation",
                attrib={
                    "Term": "Custom.MaterializedView.Suspended",
                    "Bool": str(suspended_at is not None).lower(),
                },
            )
            if suspended_at:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.SuspendedAt",
                        "Int": str(int(suspended_at)),
                    },
                )
            suspended_by = mv.get("suspended-by")
            if suspended_by:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.SuspendedBy",
                        "String": str(suspended_by),
                    },
                )
            refresh_status = mv.get("last-refresh-status")
            if refresh_status:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.LastRefreshStatus",
                        "String": str(refresh_status),
                    },
                )
            # The identity a refresh runs as. It lives on each of the view's
            # REFRESH TRIGGERS - the view carries none of its own, exactly as
            # a task does not - pinned when the trigger is landed and moved by
            # ALTER MATERIALIZED VIEW ... OWNER TO (every trigger at once) or
            # ALTER TRIGGER ... OWNER TO (one). It answers "whose grants keep
            # this view fresh" - a different question from who last changed
            # its definition, immediately below.
            # Matched by qualified target where the handle knows its
            # workspace; an older wheel without the attribute gets every
            # refresh trigger on the source, which is right for all but a
            # source feeding two views.
            workspace_name = getattr(workspace, "workspace", None)
            runs_as = _materialized_view_runs_as(
                workspace,
                mv,
                f"{workspace_name}.{collection}.{dataset}" if workspace_name else None,
            )
            if runs_as:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.RunsAs",
                        "String": str(runs_as),
                    },
                )
            updated_by = mv.get("last-updated-by")
            if updated_by:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.LastUpdatedBy",
                        "String": str(updated_by),
                    },
                )
            updated_at = mv.get("last-updated-at-ms")
            if updated_at:
                ET.SubElement(
                    entity_set,
                    f"{{{NS_EDM}}}Annotation",
                    attrib={
                        "Term": "Custom.MaterializedView.LastUpdatedAt",
                        "Int": str(int(updated_at)),
                    },
                )
        except Exception as mv_exc:  # noqa: BLE001 - catalog boundary
            # A lookup that FAILED is not the same as one that found nothing.
            # Swallowing both silently means a broken view renders as merely
            # empty - no owner, no refresh state - which is exactly the failure
            # this metadata exists to surface. "Not a materialized view" stays
            # quiet (the Materialized annotation above already said so);
            # anything else is logged.
            if type(mv_exc).__name__ not in ("DatasetNotFound", "MaterializedViewError"):
                logger.warning(
                    "materialized view metadata unavailable for %s.%s: %s: %s",
                    collection,
                    dataset,
                    type(mv_exc).__name__,
                    mv_exc,
                )

    # Add LastModified annotation if we have a timestamp
    if last_ts_ms:
        try:
            import datetime as _dt

            iso = (
                _dt.datetime.utcfromtimestamp(last_ts_ms / 1000.0)
                .replace(tzinfo=_dt.timezone.utc)
                .isoformat()
            )
            ET.SubElement(
                entity_set,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.LastModified", "String": iso},
            )
        except Exception:
            pass

    # Serialize to XML string. The tree is swept for characters XML cannot
    # carry FIRST: a column's text-bound prefixes are the leading bytes of
    # real stored values, and one control byte among them would make this
    # whole document unparseable (app/odata/xml_safety.py says more).
    xml_str = ET.tostring(sanitize_xml_tree(edmx), encoding="unicode", method="xml")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml = xml_declaration + xml_str

    # The X-Dataset-* headers were set on the injected `response` as the document
    # was assembled; carry them onto the response actually returned (and into the
    # cache, so a hit reproduces them).
    headers = dict(response.headers)
    metadata_cache_put(cache_key, "", xml, headers)
    return xml_response(xml, headers)


def _receipt_entries(entries) -> list[dict]:
    """A snapshot's receipt as `[{"dataset", "snapshot_id", "resolved_by"}]`.

    Every entry is named -- see the Custom.ReadSources renderer for why -- so
    this normalizes rather than filters: it accepts the stored hyphenated keys
    and the underscored spelling a library caller may pass, and coerces the
    snapshot id to an int.

    An entry that is not a `{dataset: str, ...}` map is DROPPED. Catalog
    documents are not schema-enforced, and an entry with no name cites nothing;
    rendering it as a Record with no Dataset would put a hole in a list whose
    whole claim is that it has none.
    """
    normalized = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        dataset = entry.get("dataset")
        if not isinstance(dataset, str) or not dataset:
            continue
        snapshot_id = entry.get("snapshot-id", entry.get("snapshot_id"))
        try:
            snapshot_id = None if snapshot_id is None else int(snapshot_id)
        except (TypeError, ValueError):
            snapshot_id = None
        resolved_by = entry.get("resolved-by", entry.get("resolved_by"))
        normalized.append(
            {
                "dataset": dataset,
                "snapshot_id": snapshot_id,
                "resolved_by": str(resolved_by) if resolved_by else None,
            }
        )
    return normalized


__all__ = ["router"]
