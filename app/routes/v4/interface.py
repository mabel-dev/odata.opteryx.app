"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
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

from opteryx_catalog import OpteryxCatalog
from opteryx_shared_services.logging import get_logger

from app.auth.deps import require_authentication
from app.core import _get_firestore_client
from opteryx_shared_services.config import get_config
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.plan_optimizer import ODataLogicalPlanOptimizer
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
from app.routes.v4.errors import describe_type_mismatch
from app.routes.v4.metadata_cache import clear_describe_cache
from app.routes.v4.metadata_cache import clear_metadata_cache
from app.routes.v4.metadata_cache import describe_cache_get
from app.routes.v4.metadata_cache import describe_cache_put
from app.routes.v4.metadata_cache import etag_for
from app.routes.v4.metadata_cache import metadata_cache_get
from app.routes.v4.metadata_cache import metadata_cache_put
from app.routes.v4.metadata_cache import metadata_response

logger = get_logger()

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")


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
    visibility_filters=None,
) -> int:
    """Run a COUNT(*) plan over the same filters as the row query, returning the total.

    Takes a session factory rather than a session because this runs concurrently
    with the row query (see execute_odata_query) and a session carries per-execution
    state, so the two must not share one.
    """
    count_builder = ODataLogicalPlanBuilder(table_name)
    count_plan = count_builder.build(
        filter_expr=filter_expr,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
        apply_expr=apply_expr,
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
    count_requested: bool = False,
    identity: Optional[str] = None,
    access_policies: Optional[List[dict]] = None,
    billing_account: Optional[str] = None,
    visibility_filters: Optional[dict] = None,
    entitlements: Optional[List[str]] = None,
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

    plan_builder = ODataLogicalPlanBuilder(table_name)
    try:
        logical_plan = plan_builder.build(
            filter_expr=filter_expr,
            select_cols=select_cols,
            orderby_expr=orderby_expr,
            top=fetch_top,
            skip=skip,
            apply_expr=apply_expr,
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
            visibility_filters,
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
                    schema_builder = ODataLogicalPlanBuilder(table_name)
                    schema_plan = schema_builder.build(
                        filter_expr=filter_expr,
                        select_cols=select_cols,
                        orderby_expr=None,
                        top=1,
                        skip=None,
                        apply_expr=apply_expr,
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
    description="Retrieve data from a dataset with OData v4 query parameters ($filter, $select, $orderby, $top, $skip, $apply, $count). Returns paginated results with total count and nextLink for server-driven paging.",
    responses={
        200: {"description": "Query succeeded; returns rows and pagination metadata"},
        400: {"description": f"Invalid query: malformed $filter, unsupported $top value (not 0-{_DOC_MAX_TOP}), negative $skip, $skip without $orderby, invalid $count value, or invalid $apply expression"},
        401: {"description": "Missing or invalid authentication (no bearer token or basic auth)"},
        403: {"description": "Forbidden: authenticated but no permission for dataset"},
        501: {"description": "Unsupported query feature: $search or $expand not implemented"},
    },
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
        )
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
        )
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

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    table_name = f"{workstream}.{collection}.{dataset}"

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
    duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
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
            count_requested=want_count,
            identity=identity,
            access_policies=access_policies,
            billing_account=billing_account,
            visibility_filters=visibility_filters,
            entitlements=entitlements,
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
        # remediation for them is `CAST(column AS type)`, which no $filter
        # expression can express — the commonest cause out here is a quoted
        # date/number, and the message now says so.
        if isinstance(
            e, (opteryx.exceptions.IncompatibleTypesError, opteryx.exceptions.IncorrectTypeError)
        ):
            raise HTTPException(status_code=400, detail=f"Invalid query: {describe_type_mismatch(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except opteryx.exceptions.DatasetReadError as e:
        # The dataset exists in the catalog but its stored metadata could not be
        # read -- a manifest the storage layer refused or could not return. Not a
        # 404: the dataset is registered, and answering "not found" sends the
        # caller looking for a name that is perfectly correct. Not a 400 either;
        # nothing about the query caused it and no rewriting of it will help.
        #
        # The engine's message embeds the storage URI it failed on, which names
        # the bucket and its internal layout, so it is logged and not returned.
        logger.error(f"Dataset metadata unreadable for {table_name}: {e}")
        raise HTTPException(
            status_code=503,
            detail="dataset is temporarily unavailable: its stored metadata could not be read",
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


@router.get(
    "/{workstream}/{collection}/{dataset}/$metadata",
    summary="Per-dataset OData EDMX metadata",
    description=(
        "Returns OData $metadata (EDMX) for a single dataset, including column types and "
        "nullability, plus custom annotations carrying column statistics "
        "(Custom.Statistics.Min/Max, DistinctValueCount, NullCount, Distribution, and CIDR for "
        "IPv4 columns), the source type name (Custom.OriginalType, Custom.SourceType), the "
        "caller's access (Custom.Role, Custom.Policy), dataset and column descriptions "
        "(Custom.Description, Custom.LLMDescribed), latest-snapshot metadata "
        "(Custom.Snapshot.Id/TotalRecords/TotalDataSize/CommitMessage/Author), physical sort "
        "order (Custom.SortOrder.Column/Direction), snapshot tags (Custom.Tags.Count and "
        "Custom.Tags, a Collection of Records with Name, SnapshotId and CreatedBy), the "
        "standing source list the current content was built from (Custom.Sources.Complete "
        "and Custom.Sources, a Collection of Records with Dataset and Visible - Dataset is "
        "omitted and Visible false for a source the caller cannot read), and "
        "materialized-view state (Custom.MaterializedView.*). Annotations are omitted where "
        "they do not apply to the dataset kind or are unavailable."
    ),
    responses={
        200: {"description": "EDMX metadata document returned as XML"},
        401: {"description": "Missing or invalid authentication"},
        403: {"description": "Forbidden: no permission to view dataset metadata"},
        404: {"description": "Dataset not found in catalog"},
    },
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
      * Custom.Statistics.DistinctValueCount: cardinality (exact or estimated)
      * Custom.Statistics.Distribution: histogram of value distribution
      * Custom.OriginalType: source database type (for mapped types like DECIMAL→Edm.Double)
      * Custom.Snapshot.TotalRecords: row count at last snapshot (table only)
      * Custom.Snapshot.Author: author of the last snapshot commit (table only)
      * Custom.SortOrder.Column/Custom.SortOrder.Direction: the column the
        underlying storage is physically sorted by, if any (table only)
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
        empty. Each Record carries Visible and, only where the caller holds
        READ on it, Dataset; an unreadable source keeps its place unnamed.

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
        allowed = True
        assigned_role = "reader"
        assigned_policy = "always-allow-public-read"

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
    if_none_match = request.headers.get("if-none-match")
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
    # the cache, for the reason the relationships are: its names are elided on
    # the caller's grants on OTHER relations (S4.4), so two callers holding the
    # same role on this dataset can be owed different documents, and it is the
    # visible names that tell them apart in the key. Naming the visible sources
    # rather than the caller keeps callers who see the same thing sharing an
    # entry. `load_dataset` reads the same document again further down; one
    # extra document get is the price of a key that is correct, and it is the
    # price the relationships already pay.
    source_names, sources_complete = (
        ([], False)
        if is_virtual_info_schema
        else _standing_sources(workstream, collection, dataset)
    )
    sources = _elided_names(claims, source_names)
    sources_key = (tuple(entry["dataset"] or "" for entry in sources), sources_complete)
    cache_key = (
        table_name,
        assigned_role,
        assigned_policy,
        is_virtual_info_schema,
        relationship_key,
        sources_key,
    )
    cached = metadata_cache_get(cache_key)
    if cached is not None:
        cached_etag, cached_xml, cached_headers = cached
        return metadata_response(cached_xml, cached_etag, cached_headers, if_none_match)

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

        # No per-column min/max: the projection deliberately does not read the
        # external catalog's manifests, so there are no bounds to annotate.
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
            snapshot = None
            try:
                if (
                    source_type in TABLE_LIKE_SOURCES
                    and hasattr(ds, "snapshot")
                    and callable(getattr(ds, "snapshot"))
                ):
                    snapshot = ds.snapshot()
            except Exception:
                snapshot = None

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

            with ThreadPoolExecutor(max_workers=3) as pool:
                schema_future = pool.submit(_fetch_schema)
                description_future = pool.submit(_fetch_description)
                tags_future = pool.submit(_fetch_tags)
                schema = schema_future.result()
                description = description_future.result()
                tags = tags_future.result()

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
                # A view's catalog object may simply not carry one. A materialized
                # view has none until its first refresh runs: CREATE MATERIALIZED
                # VIEW registers the dataset, its SQL, its sources and its trigger,
                # and the refresh is what produces the schema and first snapshot --
                # so a freshly created MV sits in exactly this state, and 404ing it
                # made its details page unreachable for the whole window between
                # creation and first refresh.
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

            # Get statistics from description (min/max, cardinality, distribution)
            if description and isinstance(description, dict):
                for col_name, stats in description.items():
                    if col_name in col_metadata and isinstance(stats, dict):
                        # Add stats to metadata and normalize naming
                        meta = col_metadata[col_name]
                        meta.update(stats)
                        # A column the manifest computed no bounds for carries
                        # the int64 sentinel, not a null, and describe() passes
                        # it straight through -- drop it here, at the single
                        # point stats enter, so nothing downstream mistakes
                        # -9223372036854775808 for a real bound.
                        meta["min"] = usable_bound(meta.get("min"))
                        meta["max"] = usable_bound(meta.get("max"))

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
            except Exception:
                # Be defensive: missing or failing snapshot should not break metadata
                pass
        except HTTPException:
            # re-raise HTTP exceptions as-is
            raise
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"Failed to load metadata from catalog: {e}\n{tb_str}")
            raise

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

    # The standing source list -- what the CURRENT content was built from
    # (PROVENANCE_DESIGN.md S2.2) -- beside the tags and gated as they are:
    # only a catalog-backed table can have been built from anything, and
    # Custom.Sources.Complete is emitted even over an empty list so a client
    # can tell "nothing on record" from "older service", the reason
    # Custom.Tags.Count is emitted at 0. The values were read from the dataset
    # document before the cache check (see `sources_key`) rather than off `ds`,
    # so the document and the key it is cached under cannot disagree.
    #
    # A Collection of Records rather than a list of names because an elided
    # entry has no name to give and must still occupy its place: its Record
    # carries no Dataset and Visible is false, the same three-way reading
    # (`dataset` null, `visible` false, entry kept) `$inbound-edges` and
    # `$lineage` give in JSON.
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
            for entry in sources:
                record = ET.SubElement(sources_collection, f"{{{NS_EDM}}}Record")
                if entry["dataset"] is not None:
                    ET.SubElement(
                        record,
                        f"{{{NS_EDM}}}PropertyValue",
                        attrib={"Property": "Dataset", "String": entry["dataset"]},
                    )
                ET.SubElement(
                    record,
                    f"{{{NS_EDM}}}PropertyValue",
                    attrib={"Property": "Visible", "Bool": str(entry["visible"]).lower()},
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

    # Serialize to XML string
    xml_str = ET.tostring(edmx, encoding="unicode", method="xml")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml = xml_declaration + xml_str

    # The X-Dataset-* headers were set on the injected `response` as the document
    # was assembled; carry them onto the response actually returned (and into the
    # cache, so a hit reproduces them).
    headers = dict(response.headers)
    etag = etag_for(xml)
    metadata_cache_put(cache_key, etag, xml, headers)
    return metadata_response(xml, etag, headers, if_none_match)


def _may_read(claims: dict, relation: str) -> bool:
    """Whether the caller may read one fully-qualified relation.

    The same three rungs the dataset endpoints climb, in the same order:
    `personal.<identity>.*` is the caller's own, `public.*` is readable by
    anyone, and everything else needs a read grant matched against the WHOLE
    relation name - not its workspace, which would let a grant on one dataset
    disclose every dataset beside it.
    """
    from app.auth.permissions import get_access_policies_from_claims
    from app.auth.permissions import read_grant_for_relation

    parts = relation.split(".")
    if len(parts) < 3:
        return False
    workstream, collection = parts[0], parts[1]
    if workstream == "personal":
        identity = claims.get("identity")
        return bool(identity) and collection == identity
    if workstream == "public":
        return True
    return read_grant_for_relation(get_access_policies_from_claims(claims), relation) is not None


@router.get(
    "/{workstream}/$inbound-edges",
    summary="What puts work into a dataset, from any workspace",
    description=(
        "Every trigger or task in the catalog whose work lands on `target`, wherever it "
        "lives. `information_schema.triggers` enumerates ONE workspace and a trigger is "
        "recorded in its SOURCE's workspace, so a chain that crosses a workspace boundary "
        "is invisible from the target's side: this is the read that answers it. Two records "
        "can put an edge into a dataset and both are reported - a trigger naming it "
        "(`materialized_view_refresh`, `task`) and a task whose statement writes it "
        "(`writes`). A row whose source the caller cannot read still comes back, with "
        "`source` and `workspace` null and `visible` false: that something upstream exists "
        "is not the secret, its name is."
    ),
    responses={
        200: {"description": "The inbound edges, in `value`"},
        400: {"description": "`target` missing, not fully qualified, or in another workspace than the path"},
        401: {"description": "Missing or invalid authentication"},
        403: {"description": "Forbidden: no read access to the target"},
    },
)
def inbound_edges(
    request: Request,
    response: Response,
    workstream: str,
    target: str = Query(
        ...,
        description=(
            "The fully-qualified `workspace.collection.name` whose upstream is wanted. Must "
            "be in the workspace named in the path."
        ),
    ),
) -> Any:
    """The trigger graph read backwards, for one dataset.

    WORKSPACE-SCOPED PATH for a deliberately cross-workspace answer: the rows
    come from anywhere, but the question is about a dataset in `workstream`, and
    the path is what the caller's read grant is checked against. Its own segment
    (`$inbound-edges`, in the OData manner of `$metadata`) rather than a virtual
    `information_schema` table, because it is not one: it takes a required
    argument, and the engine has no such table to keep this one honest against.

    ONE INDEXED QUERY EACH behind it - see opteryx_catalog.inbound_edges - not
    the walk over every collection and dataset that reading a workspace's whole
    trigger listing costs. That is what makes it affordable to ask per node.
    """
    response.headers["OData-Version"] = "4.0"

    claims = getattr(request.state, "claims", {}) or {}
    _require_provenance_target(claims, workstream, target)

    from opteryx_catalog.inbound_edges import find_inbound_edges

    client = _get_firestore_client(GCP_PROJECT_ID, _firestore_database())
    if client is None:
        raise HTTPException(status_code=503, detail="catalog unavailable")

    rows = find_inbound_edges(client, target)

    value = []
    for row in rows:
        visible = _may_read(claims, row["source"])
        # A task's declared read set (PROVENANCE_DESIGN.md S2.3) rides on its
        # `writes` edge; a trigger declares nothing about what its target
        # reads, and the catalog says so with None. Each name is elided on
        # its own grant, not the source's: the task may be readable while one
        # of the things it reads is not, and the reverse.
        reads = row.get("reads")
        value.append(
            {
                "target": row["target"],
                # Elided rather than dropped: the caller is owed the fact that
                # something upstream exists even where they may not be told what.
                "source": row["source"] if visible else None,
                "workspace": row["workspace"] if visible else None,
                "source_kind": row["source_kind"] if visible else None,
                "visible": visible,
                "kind": row["kind"],
                "trigger": row["trigger"] if visible else None,
                "runs_as": row["runs_as"] if visible else None,
                "last_fired_at": _iso_from_ms(row["last_fired_at_ms"]) if visible else None,
                "last_fired_status": row["last_fired_status"] if visible else None,
                "suspended": bool(row["suspended_at_ms"]) if visible else None,
                "reads": _elided_names(claims, reads) if isinstance(reads, list) else None,
            }
        )

    return Response(
        content=json.dumps({"target": target, "value": value}).encode("utf-8"),
        media_type="application/json",
        headers={"OData-Version": "4.0"},
    )


def _require_provenance_target(claims: dict, workstream: str, target: str) -> None:
    """The target of a provenance read, checked the way `$inbound-edges` checks it.

    Three routes ask about one fully-qualified dataset from a workspace-scoped
    path, and all three owe the same answer to the same three faults. A name
    that is not fully qualified cannot be matched against anything stored,
    because a bare `collection.dataset` means a different thing in every
    workspace (400). A target outside the path's workspace would have one
    dataset's read check guarding an answer about another, since the path is
    what authorization is decided on (400). And a caller without READ on the
    target is refused before the catalog is asked anything (403).
    """
    parts = (target or "").split(".")
    if len(parts) < 3:
        raise HTTPException(
            status_code=400,
            detail="target must be a fully-qualified workspace.collection.name",
        )
    if parts[0] != workstream:
        raise HTTPException(
            status_code=400,
            detail=f"target {target} is not in workspace {workstream}",
        )
    if not _may_read(claims, target):
        raise HTTPException(status_code=403, detail="forbidden: not allowed to read target")


def _elided_names(claims: dict, names) -> list[dict]:
    """Fully-qualified dataset names as `[{"dataset", "visible"}]`, elided per name.

    PROVENANCE_DESIGN.md S4.4: a provenance record is stored whole and crosses
    workspace boundaries, so every read surface tells the caller a name only
    where they hold READ on it. The ENTRY stays either way - "something
    upstream you cannot see" must remain sayable, and the list's length must
    stay honest - so what is nulled is the name, never the position.
    """
    entries = []
    for name in names or []:
        name = str(name)
        visible = _may_read(claims, name)
        entries.append({"dataset": name if visible else None, "visible": visible})
    return entries


def _producer_fields(claims: dict, produced_by) -> dict:
    """`produced_by` and `producer_visible` for one snapshot.

    The stored value is `task:<workspace.collection.name>` or `view:<...>`,
    and it is the name behind the prefix that READ is checked on. A hand-run
    statement has no producer at all, and that is a state rather than an
    elision (PROVENANCE_DESIGN.md S2.5), so both fields come back null there:
    a client can then tell "nobody declared" from "someone you may not know
    about", which is `produced_by` null beside `producer_visible` false.
    """
    if not produced_by:
        return {"produced_by": None, "producer_visible": None}
    text = str(produced_by)
    _, _, name = text.partition(":")
    visible = _may_read(claims, name or text)
    return {"produced_by": text if visible else None, "producer_visible": visible}


def _open_workspace(workstream: str):
    """A catalog handle on one workspace, built the way `dataset_metadata` builds its own."""
    return OpteryxCatalog(
        workstream,
        firestore_project=GCP_PROJECT_ID,
        firestore_database=_firestore_database(),
        gcs_bucket=_bucket_name(),
    )


class _SourceSnapshots:
    """Which snapshots of a source dataset are still live, one history load
    per source for the life of one `$lineage` answer.

    A receipt names a version and does not pin it (PROVENANCE_DESIGN.md
    S2.1): the snapshot it names can have expired since, and the row owes the
    reader that fact. Liveness is membership in a history load, which keeps
    only the live snapshots - the catalog's by-id fetch refuses a tombstone
    too (opteryx-catalog 0.4.144), but membership answers every row of a
    source from ONE read where by-id would be one per distinct version.
    Loaded once per distinct source rather than per row, because a table
    refreshed hourly from the same two sources names them on every one of
    its snapshots.
    """

    def __init__(self) -> None:
        self._live: dict[str, set | None] = {}

    def __call__(self, relation: str, snapshot_id) -> bool | None:
        if snapshot_id is None:
            # The source had no commits when it was read (S3): there is no
            # snapshot whose liveness could be asked about.
            return None
        if relation not in self._live:
            self._live[relation] = self._load(relation)
        live = self._live[relation]
        if live is None:
            return None
        try:
            return int(snapshot_id) in live
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _load(relation: str) -> set | None:
        workspace, _, relative = relation.partition(".")
        try:
            dataset = _open_workspace(workspace).load_dataset(relative, load_history=True)
            return {
                int(snapshot.snapshot_id)
                for snapshot in dataset.snapshots()
                if getattr(snapshot, "snapshot_id", None) is not None
            }
        except Exception as exc:  # noqa: BLE001 - catalog boundary
            # A source that has been dropped is a definite answer - nothing of
            # it is live - and is told apart by name the way the
            # materialized-view path does, because the type is the catalog's
            # own. Anything else is a read that did not answer, and null is
            # the honest report of that rather than a guess either way.
            if type(exc).__name__ == "DatasetNotFound":
                return set()
            logger.warning(f"Could not read the history of {relation} for source_exists: {exc}")
            return None


@router.get(
    "/{workstream}/$lineage",
    summary="What each committed version of a dataset was built from",
    description=(
        "The receipts (PROVENANCE_DESIGN.md): for every live snapshot of `target`, which "
        "versions of which other datasets the statement that produced it read, newest "
        "snapshot first, one row per (snapshot, source). Three states are told apart: a "
        "snapshot with no receipt gives ONE row with `recorded` false and the source columns "
        "null (pre-feature history, which cannot be backfilled); a snapshot that read no "
        "catalog relation gives one row with `recorded` true and the source columns null; "
        "otherwise each source is a row. A source the caller cannot read still comes back, "
        "with `source_dataset` and `source_exists` null and `visible` false. `source_exists` "
        "says whether the version read is still live - a receipt does not pin what it names. "
        "`sources` and `sources_complete` are the dataset's standing list, as on its "
        "`$metadata` document."
    ),
    responses={
        200: {"description": "The receipts, in `value`; the standing list in `sources`"},
        400: {"description": "`target` missing, not fully qualified, or in another workspace than the path"},
        401: {"description": "Missing or invalid authentication"},
        403: {"description": "Forbidden: no read access to the target"},
        404: {"description": "Target not found in the catalog"},
    },
)
def lineage(
    request: Request,
    response: Response,
    workstream: str,
    target: str = Query(
        ...,
        description=(
            "The fully-qualified `workspace.collection.dataset` whose receipts are wanted. "
            "Must be in the workspace named in the path."
        ),
    ),
) -> Any:
    """The receipts of one dataset, per snapshot, with each name elided per grant.

    The same path shape and gate as `$inbound-edges`, for the other half of
    the same question: that route says what is WIRED to write the target,
    this one says what its commits actually READ, and from which versions.
    One history load of the target, plus one per distinct source named for
    `source_exists` (see `_SourceSnapshots`).
    """
    response.headers["OData-Version"] = "4.0"

    claims = getattr(request.state, "claims", {}) or {}
    _require_provenance_target(claims, workstream, target)

    _, _, relative = target.partition(".")
    try:
        dataset = _open_workspace(workstream).load_dataset(relative, load_history=True)
    except Exception as exc:
        logger.error(f"Failed to load dataset history from catalog for {target}: {exc}")
        raise HTTPException(status_code=404, detail="dataset not found in catalog") from exc

    metadata = getattr(dataset, "metadata", None)
    # The head is the pointer, not the newest id: a rollback moves it
    # backwards past snapshots that stay live and listed here. `snapshot()`
    # with no argument reads that pointer from the metadata already in hand,
    # including the fallback for a dataset written before the pointer existed.
    head = getattr(dataset.snapshot(), "snapshot_id", None)
    source_exists = _SourceSnapshots()

    value = []
    snapshots = sorted(
        dataset.snapshots(), key=lambda s: -(getattr(s, "snapshot_id", None) or 0)
    )
    for snapshot in snapshots:
        base = {
            "snapshot_id": snapshot.snapshot_id,
            "committed_at": _iso_from_ms(getattr(snapshot, "timestamp_ms", None)),
            "is_current": snapshot.snapshot_id == head,
            **_producer_fields(claims, getattr(snapshot, "produced_by", None)),
        }
        receipt = getattr(snapshot, "read_sources", None)
        if not receipt:
            # The two marker states (S2.4). None is "the writer did not
            # report", which is permanent for pre-feature history; [] is
            # "read no catalog relation", which is a fact about the commit.
            # Neither has a source to name or to elide, so `visible` is null
            # rather than false - false would read as an elided name.
            value.append(
                {
                    **base,
                    "source_dataset": None,
                    "source_snapshot_id": None,
                    "resolved_by": None,
                    "source_exists": None,
                    "recorded": receipt is not None,
                    "visible": None,
                }
            )
            continue
        # Entries are stored deduplicated and sorted on (dataset, snapshot-id)
        # (S2.1), which is the row order the spec asks for, so they are not
        # re-sorted here.
        for entry in receipt:
            name = entry.get("dataset")
            source_snapshot_id = entry.get("snapshot-id")
            visible = bool(name) and _may_read(claims, str(name))
            value.append(
                {
                    **base,
                    "source_dataset": str(name) if visible else None,
                    "source_snapshot_id": source_snapshot_id,
                    "resolved_by": entry.get("resolved-by"),
                    # Liveness is asked only for a name the caller may see:
                    # true or false beside a null name would still say
                    # something about a dataset they hold no grant on.
                    "source_exists": (
                        source_exists(str(name), source_snapshot_id) if visible else None
                    ),
                    "recorded": True,
                    "visible": visible,
                }
            )

    body = {
        "target": target,
        "sources": _elided_names(claims, getattr(metadata, "sources", None)),
        "sources_complete": bool(getattr(metadata, "sources_complete", False)),
        "value": value,
    }
    return Response(
        content=json.dumps(body).encode("utf-8"),
        media_type="application/json",
        headers={"OData-Version": "4.0"},
    )


@router.get(
    "/{workstream}/$consumers",
    summary="Which commits, in any workspace, read a dataset",
    description=(
        "The outgoing half of provenance: every live snapshot in the catalog whose receipt "
        "names `target`, and with `snapshot` only those that read that version of it. A "
        "trigger says what a commit fires, not what read it, and a hand-run statement fires "
        "nothing, so the receipt on the consuming snapshot is the only record. Pre-feature "
        "commits carry no receipt and are not consumers here even if they did read the "
        "target; `$inbound-edges` answers from the plan for those. A consumer the caller "
        "cannot read still comes back, with `dataset`, `workspace` and `produced_by` null "
        "and `visible` false."
    ),
    responses={
        200: {"description": "The consuming snapshots, in `value`"},
        400: {"description": "`target` missing, not fully qualified, or in another workspace than the path"},
        401: {"description": "Missing or invalid authentication"},
        403: {"description": "Forbidden: no read access to the target"},
    },
)
def consumers(
    request: Request,
    response: Response,
    workstream: str,
    target: str = Query(
        ...,
        description=(
            "The fully-qualified `workspace.collection.dataset` whose readers are wanted. "
            "Must be in the workspace named in the path."
        ),
    ),
    snapshot: int | None = Query(
        None,
        description="Narrow the answer to the commits that read this snapshot of `target`.",
    ),
) -> Any:
    """Who read one dataset - or one version of it - from the receipts.

    ONE INDEXED QUERY behind it - see opteryx_catalog.consumers - over the
    `snapshots` collection group, the same shape `$inbound-edges` uses over
    `tasks`. READ on the target is the gate; each consumer is then elided on
    its own name, and its producer on the producer's name, since either may be
    in a workspace the caller cannot see.
    """
    response.headers["OData-Version"] = "4.0"

    claims = getattr(request.state, "claims", {}) or {}
    _require_provenance_target(claims, workstream, target)

    from opteryx_catalog.consumers import find_consumers

    client = _get_firestore_client(GCP_PROJECT_ID, _firestore_database())
    if client is None:
        raise HTTPException(status_code=503, detail="catalog unavailable")

    rows = find_consumers(client, target, snapshot)

    value = []
    for row in rows:
        visible = _may_read(claims, row["dataset"])
        # The producer is named only when BOTH the consumer and the producer
        # are readable: a task name beside an elided consumer would say what
        # writes the thing the caller may not know about.
        producer = _producer_fields(claims, row.get("produced_by")) if visible else None
        value.append(
            {
                "dataset": row["dataset"] if visible else None,
                "workspace": row["workspace"] if visible else None,
                "visible": visible,
                "snapshot_id": row["snapshot_id"],
                "committed_at": _iso_from_ms(row["committed_at_ms"]),
                "produced_by": producer["produced_by"] if producer else None,
                # Which versions of the TARGET were read, and how each was
                # chosen. About the dataset the caller already holds READ on,
                # so never elided.
                "source_snapshot_ids": list(row.get("source_snapshot_ids") or []),
                "resolved_by": list(row.get("resolved_by") or []),
            }
        )

    body = {"target": target, "snapshot": snapshot, "value": value}
    return Response(
        content=json.dumps(body).encode("utf-8"),
        media_type="application/json",
        headers={"OData-Version": "4.0"},
    )


def _iso_from_ms(value) -> Optional[str]:
    """Epoch milliseconds as an ISO 8601 UTC string, the spelling every other
    timestamp in this API carries. None stays None: a trigger that has never
    fired has no time, and 1970 is not it."""
    if value is None:
        return None
    try:
        return (
            dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError, OSError):
        return None


__all__ = ["router"]
