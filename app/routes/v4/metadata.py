"""OData v4 EDMX metadata endpoint."""

# Get catalog configuration from environment
import datetime as _dt
import hashlib
import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from google.cloud.firestore_v1.base_query import FieldFilter

# The catalog's own renderer for a persisted column's type. Imported rather than
# reimplemented so a DECIMAL's precision and an ARRAY's element type are spelled
# here exactly as they are everywhere else; if the catalog ever moves it, this
# fails loudly at import instead of quietly emitting a different type.
from opteryx_catalog.catalog.dataset import _stored_type_display

from app.auth.deps import require_authentication
from app.config_client import get_config
from app.core import _get_firestore_client
from app.logging import get_logger
from app.odata.edm_types import ORIGINAL_TYPE_TERM, RESOURCE_TERM, EdmProperty, edm_property
from app.odata.virtual_datasets import (
    VIRTUAL_INFORMATION_SCHEMA_TABLES,
    is_virtual_information_schema,
)
from app.routes.v4.metadata_cache import (
    clear_metadata_cache,
    etag_for,
    metadata_cache_get,
    metadata_cache_put,
    metadata_response,
)

logger = get_logger()
router = APIRouter(prefix="/api/v4", tags=["OData v4"])


# Resolved per use, not at import. A module-level constant is evaluated once
# at startup and then frozen for the life of the process, so config changes
# would never reach it without a redeploy.
def _firestore_database() -> str:
    return get_config("FIRESTORE_DATABASE")


def _metadata_workers() -> int:
    """How many Firestore reads this endpoint may have in flight at once.

    Every unit of work it fans out is a network round trip -- the thread spends
    its life blocked, not running Python -- so the useful width is set by what
    Firestore will tolerate, not by the core count.
    """
    try:
        return max(1, int(get_config("ODATA_METADATA_WORKERS", "16")))
    except ValueError:
        return 16


# A materialized view is a real dataset -- it lives in the `datasets`
# subcollection and has snapshots, a schema and a sort order -- that the catalog
# tags with a `dataset-type` field (MATERIALIZED_VIEW_TYPE in
# opteryx_catalog.opteryx_catalog). Plain datasets carry no such field. The value
# is duplicated rather than imported so this module stays importable without the
# catalog package (several tests load it against stubs).
MATERIALIZED_VIEW_DATASET_TYPE = "materialized_view"
MATERIALIZED_VIEW_SOURCE = "MaterializedView"


# A dataset document projected from a workspace's own external catalog rather
# than owned by this one (opteryx-catalog's stub_projection). Reported as its
# own source type, not as "Table": the two are not the same thing to a client -
# one is a table Opteryx stores and can describe from its own manifests, the
# other is a name-and-schema projection whose data lives somewhere else and
# whose listing is only as fresh as the last refresh. The Studio already
# expects this token (see sql_ide.js's Custom.SourceType list) and draws it a
# distinct glyph.
EXTERNAL_TABLE_SOURCE = "ExternalTable"
STUB_MARKER = "external-catalog"


def _source_type_from_doc(data, collection: str) -> str:
    """Source label for a catalog document: View, MaterializedView or Table.

    `collection` is the subcollection the document came from ("datasets" or
    "views"); materialized views live in "datasets" and are told apart from
    plain datasets by their `dataset-type` field.
    """
    if collection == "views":
        return "View"
    if isinstance(data, dict) and data.get(STUB_MARKER):
        return EXTERNAL_TABLE_SOURCE
    if isinstance(data, dict) and data.get("dataset-type") == MATERIALIZED_VIEW_DATASET_TYPE:
        return MATERIALIZED_VIEW_SOURCE
    return "Table"


def _normalize_resource_name(resource: str) -> str:
    """Normalize resource name by removing trailing '.*' or '.' segments."""
    if not resource:
        return resource

    # Remove trailing wildcard pattern
    resource = resource.removesuffix(".*")

    # Remove trailing dot if present
    resource = resource.removesuffix(".")

    return resource


def _parse_path_segments(path: str) -> tuple[str | None, str | None, str | None]:
    """
    Parse Firestore path to extract workspace, collection, and table name.

    Expected path pattern: /{workspace}/{collection}/{datasets|views}/{table_name}

    Returns:
        Tuple of (workspace, collection, table_name)
    """
    segments = path.strip("/").split("/")

    # Find the index of 'datasets' or 'views' in the path
    for i, segment in enumerate(segments):
        if segment in ("datasets", "views"):
            # Extract components
            workspace = segments[i - 2] if i >= 2 else None
            collection = segments[i - 1] if i >= 1 else None
            table_name = segments[i + 1] if i + 1 < len(segments) else None
            return workspace, collection, table_name

    return None, None, None


class DatasetInfo:
    """Information about a dataset accessible to the user.

    Everything here comes out of the catalog document the listing query already
    streamed. The last three fields are what let the EDMX document be built
    without going back to the catalog for a second look at the same document:
    `schema_id` and `doc_ref` locate the one thing the listing does not carry
    (the column list), and `inline_schema` means even that is already in hand.
    """

    def __init__(
        self,
        resource: str,
        workspace: str,
        collection: str,
        name: str,
        source: str = "Table",
        last_modified_ms: int | None = None,
        schema_id: str | None = None,
        inline_schema: list | None = None,
        doc_ref=None,
    ):
        self.resource = resource
        self.workspace = workspace
        self.collection = collection
        self.name = name
        self.source = source
        self.last_modified_ms = last_modified_ms
        #: `current-schema-id`: names the doc in the `schemas` subcollection.
        self.schema_id = schema_id
        #: Some documents carry their columns inline instead -- always, for a
        #: view, whose schema is stored on the view document itself.
        self.inline_schema = inline_schema
        #: The Firestore reference this document came from, so the schema
        #: subcollection can be addressed without reconstructing a path.
        self.doc_ref = doc_ref


def _query_collection_group(
    db,
    collection: str,
    workspace_filter: str | None = None,
    limit: int = 1000,
) -> list[DatasetInfo]:
    """
    Query a collection group and return formatted results.

    Each document is read once, here, and every field the EDMX document needs
    from it is captured on the DatasetInfo -- see that class for why.

    Args:
        db: Firestore client
        collection: Subcollection name ("datasets" or "views")
        workspace_filter: Optional workspace ID to filter by
        limit: Maximum number of documents to retrieve
    """
    results = []

    try:
        query = db.collection_group(collection)

        if workspace_filter:
            query = query.where(filter=FieldFilter("workspace", "==", workspace_filter))

        query = query.limit(limit)

        for doc in query.stream():
            try:
                # Same $-prefix guard as service_document.py's twin query and
                # opteryx_catalog.list_collections(): skip drop tombstones
                # ("$dropped") and workspace metadata ("$properties"), whose
                # datasets/views subcollections are otherwise indistinguishable
                # from a real collection's to a bare collection_group query.
                parent_doc_id = doc.reference.parent.parent.id
                if parent_doc_id.startswith("$"):
                    continue

                data = doc.to_dict()
                workspace = data.get("workspace")
                collection_name = data.get("namespace")
                name = data.get("name")

                # Parse path segments to extract missing pieces
                if not all([workspace, collection_name, name]):
                    parsed_workspace, parsed_collection, parsed_table_name = _parse_path_segments(
                        doc.reference.path
                    )
                    if not workspace:
                        workspace = parsed_workspace
                    if not collection_name:
                        collection_name = parsed_collection
                    if not name:
                        name = parsed_table_name

                # Fallback to document ID
                name = name or doc.id

                # Construct fully qualified resource name
                resource_name = f"{workspace}.{collection_name}.{name}"
                resource_name = _normalize_resource_name(resource_name)

                # Determine source type from the collection group ('views' => View)
                # and, for datasets, the doc's `dataset-type` (=> MaterializedView)
                source_type = _source_type_from_doc(data, collection)

                # Try to capture last modified information from Firestore doc (common field names).
                #
                # "timestamp-ms" is the one the catalog actually writes, and it
                # is the same number as the current snapshot's timestamp: a
                # commit takes `snapshot_id = int(time.time() * 1000)` and
                # assigns it to the snapshot's timestamp_ms *and* to the
                # dataset metadata's timestamp_ms (opteryx_catalog
                # SimpleDataset.append -> save_dataset_metadata). So this is the
                # last-modified time, not an approximation of it, and reading
                # the snapshot document to get the same number would be a round
                # trip for nothing. The rest are tolerated spellings from
                # documents this service does not write.
                last_modified = None
                for key in (
                    "timestamp-ms",
                    "last_modified",
                    "updated_at",
                    "updated",
                    "timestamp_ms",
                    "last_modified_ms",
                ):
                    val = data.get(key) if isinstance(data, dict) else None
                    if val is not None:
                        # If integer timestamp (ms) use directly
                        if isinstance(val, int):
                            last_modified = val
                            break
                        # If ISO datetime string, try to parse
                        if isinstance(val, str):
                            try:
                                iso = val
                                if iso.endswith("Z"):
                                    iso = iso.replace("Z", "+00:00")
                                dt = _dt.datetime.fromisoformat(iso)
                                last_modified = int(dt.timestamp() * 1000)
                                break
                            except Exception:
                                # ignore parse errors and continue
                                pass

                results.append(
                    DatasetInfo(
                        resource=resource_name,
                        workspace=workspace,
                        collection=collection_name,
                        name=name,
                        source=source_type,
                        last_modified_ms=last_modified,
                        schema_id=data.get("current-schema-id"),
                        inline_schema=data.get("schema"),
                        doc_ref=doc.reference,
                    )
                )
            except Exception:
                continue
    except Exception:
        pass

    return results


def _get_personal_datasets(db, identity: str) -> list[DatasetInfo]:
    """Retrieve datasets from user's personal collection."""
    results = []

    try:
        # Query personal datasets from the top-level 'personal' collection
        datasets_coll = db.collection("personal").document(identity).collection("datasets")
        for doc in datasets_coll.stream():
            try:
                data = doc.to_dict()
                workspace = "personal"
                collection_name = identity
                name = data.get("name") or doc.id

                resource_name = f"{workspace}.{collection_name}.{name}"
                resource_name = _normalize_resource_name(resource_name)

                results.append(
                    DatasetInfo(
                        resource=resource_name,
                        workspace=workspace,
                        collection=collection_name,
                        name=name,
                        source=_source_type_from_doc(data, "datasets"),
                        last_modified_ms=data.get("timestamp-ms"),
                        schema_id=data.get("current-schema-id"),
                        inline_schema=data.get("schema"),
                        doc_ref=doc.reference,
                    )
                )
            except Exception:
                continue
    except Exception as exc:
        logger.error(f"Failed to retrieve personal datasets for {identity}: {type(exc)} {exc}")

    return results


def _get_workspace_datasets(db, workspace_ids: set[str]) -> list[DatasetInfo]:
    """Retrieve all datasets and views from the given workspace IDs.

    Both subcollections are queried, as the service document's twin listing
    does. Querying only "datasets" left every view out of the EDMX document
    entirely -- they were unlistable here while being perfectly queryable.

    One collection-group query per (workspace, subcollection), issued
    concurrently: they are independent Firestore round trips, so run serially
    the caller waits for their sum rather than for the slowest one.
    """
    results: list[DatasetInfo] = []
    queries = [
        (workspace_id, subcollection)
        for workspace_id in sorted(workspace_ids)
        for subcollection in ("datasets", "views")
    ]
    if not queries:
        return results

    def _run(query):
        workspace_id, subcollection = query
        return _query_collection_group(
            db, subcollection, workspace_filter=workspace_id, limit=1000
        )

    try:
        if len(queries) == 1:
            batches = [_run(queries[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(len(queries), _metadata_workers())) as pool:
                batches = list(pool.map(_run, queries))
        for batch in batches:
            results.extend(batch)
    except Exception as exc:
        logger.error(f"Failed to retrieve datasets: {type(exc)} {exc}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="failed to read datasets from firestore",
        ) from exc

    return results


class _DatasetFacts(NamedTuple):
    """Everything the EDMX document needs about one dataset.

    Both halves come from the catalog document the listing query already
    streamed -- the timestamp directly, the columns either inline or via the one
    schema document that document points at.
    """

    #: Column definitions, empty when no schema could be read. The facets are
    #: not optional decoration -- see edm_type_facets: a Decimal without Scale,
    #: or a DateTimeOffset without Precision, means something different in CSDL
    #: than what the rows carry.
    columns: list
    #: Last-modified time, straight off the catalog document.
    last_modified_ms: int | None


_NO_FACTS = _DatasetFacts(columns=[], last_modified_ms=None)

# How many schema documents to ask for in one Firestore batched read. Firestore
# resolves a BatchGetDocuments as a single streamed call, so the whole point is
# to make this large; it is chunked at all only to keep any one request a
# sensible size, and the chunks are then fetched concurrently anyway.
_SCHEMA_BATCH_SIZE = 300


def _columns_from_stored(stored_columns) -> list:
    """Turn a stored column list into EDM property definitions.

    `_stored_type_display` is the catalog's own renderer for a persisted column
    (it is what puts the precision and scale into `DECIMAL(38, 9)` and the
    element type into `ARRAY<VARCHAR>`), and is exactly what
    `SimpleDataset.schema()` runs to produce the `SchemaColumn.type` string this
    endpoint used to read. Reusing it rather than re-deriving the display form
    is what keeps a type meaning the same thing whichever endpoint reports it.
    """
    columns = []
    for column in stored_columns or []:
        if not isinstance(column, dict):
            continue
        name = column.get("name")
        if not name:
            continue
        columns.append(
            edm_property(
                name,
                _stored_type_display(column),
                column.get("element-type") or column.get("element_type"),
            )
        )
    return columns


def _facts_for(db, datasets: list[DatasetInfo]) -> dict[str, _DatasetFacts]:
    """Resolve every dataset's columns and last-modified time, keyed by resource.

    The listing query that produced `datasets` has already read each catalog
    document in full, so the timestamp needs no further lookup and neither do
    the columns of anything carrying its schema inline (a view always does).
    What is left is one `schemas/{current-schema-id}` document per remaining
    dataset -- immutable, addressable, and independent of one another, so they
    are fetched with Firestore's batched read rather than one at a time.

    This is why the endpoint no longer opens an OpteryxCatalog: `load_dataset`
    would re-read the very document the listing just streamed, behind a handle
    whose construction is itself a Firestore client plus a `$properties` read.
    """
    facts: dict[str, _DatasetFacts] = {}
    wanted: dict[str, Any] = {}  # schema doc path -> its Firestore reference
    pending: list[tuple[DatasetInfo, str]] = []  # datasets awaiting a batch read

    for dataset in datasets:
        if is_virtual_information_schema(dataset.collection, dataset.name):
            # Not backed by a catalog entry: fixed schema, and nothing to be
            # last-modified.
            facts[dataset.resource] = _DatasetFacts(
                columns=VIRTUAL_INFORMATION_SCHEMA_TABLES[dataset.name],
                last_modified_ms=None,
            )
            continue

        # `current-schema-id` wins over an inline `schema` when both are
        # present, matching SimpleDataset.schema(): it resolves the current
        # schema id first and only falls back to the inline copy when there
        # isn't one. Preferring the inline copy would serve a stale schema to
        # any dataset that still carries one from before it had schema
        # documents.
        if dataset.schema_id and dataset.doc_ref is not None:
            try:
                ref = dataset.doc_ref.collection("schemas").document(str(dataset.schema_id))
            except Exception as exc:
                logger.warning(f"Cannot address schema of {dataset.resource}: {exc}")
                facts[dataset.resource] = _NO_FACTS
                continue
            wanted[ref.path] = ref
            pending.append((dataset, ref.path))
            continue

        # Otherwise the columns are already in hand, or there are none to
        # declare -- in which case the caller's `if not columns` drops the
        # dataset from the document.
        facts[dataset.resource] = _DatasetFacts(
            columns=_columns_from_stored(dataset.inline_schema),
            last_modified_ms=dataset.last_modified_ms,
        )

    columns_by_path = _read_schema_documents(db, list(wanted.values()))
    for dataset, path in pending:
        facts[dataset.resource] = _DatasetFacts(
            columns=columns_by_path.get(path, []),
            last_modified_ms=dataset.last_modified_ms,
        )
    return facts


def _read_schema_documents(db, refs: list) -> dict[str, list]:
    """Batch-read schema documents, returning path -> column definitions."""
    if not refs:
        return {}

    chunks = [refs[i : i + _SCHEMA_BATCH_SIZE] for i in range(0, len(refs), _SCHEMA_BATCH_SIZE)]

    def _read(chunk) -> dict[str, list]:
        found: dict[str, list] = {}
        try:
            for doc in db.get_all(chunk):
                ref = getattr(doc, "reference", None)
                if ref is None or not getattr(doc, "exists", False):
                    continue
                found[ref.path] = _columns_from_stored((doc.to_dict() or {}).get("columns"))
        except Exception as exc:
            # A failed batch costs those datasets their columns, which drops
            # them from the document -- the same outcome an unreadable schema
            # had before, and not a reason to fail the whole request.
            logger.warning(f"Batched schema read failed for {len(chunk)} datasets: {exc}")
        return found

    if len(chunks) == 1:
        return _read(chunks[0])

    columns_by_path: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=min(len(chunks), _metadata_workers())) as pool:
        for found in pool.map(_read, chunks):
            columns_by_path.update(found)
    return columns_by_path


def _caller_fingerprint(claims: dict) -> str:
    """Stable digest of everything about the caller that changes the document.

    The document lists exactly the datasets this caller may see, so two callers
    may only share a cache entry when their identity and their policies match.
    """
    payload = [claims.get("identity"), claims.get("policies") or []]
    try:
        encoded = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        encoded = repr(payload)
    return hashlib.blake2b(encoded.encode("utf-8"), digest_size=16).hexdigest()


def _ordered_datasets(datasets: list[DatasetInfo]) -> list[DatasetInfo]:
    """Deduplicate by resource name and put the result in a deterministic order.

    Both halves matter. Duplicates cost a catalog round trip each and used to
    emit a repeated EntitySet for the same EntityType. The ordering is what
    makes the ETag usable at all: Firestore stream order and set iteration order
    are neither stable across processes nor guaranteed stable within one, so
    without this the same datasets would render different bytes -- and so a
    different validator -- on each replica the client happened to reach.
    """
    unique: dict[str, DatasetInfo] = {}
    for dataset in datasets:
        unique.setdefault(dataset.resource, dataset)
    return [unique[resource] for resource in sorted(unique)]


def _entity_names(datasets: list[DatasetInfo]) -> dict[str, str]:
    """Map each resource to the CSDL identifier its EntityType/EntitySet uses.

    A SimpleIdentifier cannot contain a dot, so the dots become underscores --
    and that flattening is not injective: "a.b.c_d" and "a.b_c.d" both render
    as "a_b_c_d". Colliding resources used to be silently dropped from the
    document, so a client never learned the second one existed. Instead, every
    member of a colliding group takes a suffix derived from its own full
    resource name: the disambiguated identifier then depends only on that
    resource, not on which other datasets the caller can see or on the order
    they were read in, so it stays stable across requests and replicas.

    The real dotted name is emitted alongside as a RESOURCE_TERM annotation, so
    clients read it rather than trying to reverse either form.
    """
    flattened: dict[str, list[str]] = {}
    for dataset in datasets:
        flattened.setdefault(dataset.resource.replace(".", "_"), []).append(dataset.resource)

    names: dict[str, str] = {}
    for base, resources in flattened.items():
        if len(resources) == 1:
            names[resources[0]] = base
            continue
        logger.warning(
            f"Resource names collide as EntityType {base}: {', '.join(sorted(resources))}; "
            "emitting each with a disambiguating suffix"
        )
        for resource in resources:
            digest = hashlib.sha256(resource.encode("utf-8")).hexdigest()[:8]
            names[resource] = f"{base}__{digest}"
    return names


@router.get(
    "/$metadata",
    status_code=status.HTTP_200_OK,
    summary="OData v4 Service-wide EDMX Metadata",
    description="Returns the complete OData v4 EDMX metadata document describing all EntityTypes and EntitySets accessible to the authenticated user.",
    responses={
        200: {"description": "EDMX metadata document (XML)"},
        401: {"description": "Missing or invalid authentication"},
        504: {"description": "Firestore unavailable; cannot enumerate datasets"},
    },
    dependencies=[Depends(require_authentication)],
)
def get_edmx_metadata(request: Request) -> Any:
    """
    Return service-wide OData v4 EDMX metadata document.

    The metadata document provides a complete, machine-readable description of the entire
    OData service data model. It lists all EntityTypes (dataset schemas) and EntitySets
    (queryable collections) that the authenticated user has access to.

    Response structure (XML):
    - Edmx element (version 4.0)
      * DataServices (wrapper)
        - Schema (namespace OData)
          * EntityType elements: one per dataset, with Properties describing columns
            - Name: fully-qualified dataset name (workspace_collection_dataset)
            - Key: first column used as primary key
            - Properties: column definitions with Name, Type, Nullable
            - Annotations: Custom.SourceType (Table/View/MaterializedView), Custom.LastModified
          * EntityContainer (wrapper for EntitySets)
            - EntitySet elements: one per dataset
              * Name: underscored dataset name (CSDL SimpleIdentifier)
              * EntityType: reference to matching EntityType
              * Annotations: source type, access control, last modified timestamp

    Custom annotations:
    - Custom.SourceType: "Table", "View", "MaterializedView" (a dataset the catalog
      marks with dataset-type "materialized_view" -- physically a table, refreshed
      from a query), or "Virtual" (synthetic information_schema datasets)
    - Custom.LastModified: ISO 8601 timestamp of last modification
    - Per-dataset column statistics available at /{ws}/{coll}/{ds}/$metadata

    Access control: Filtered by authenticated user's permissions. Personal/public
    workspaces handled automatically. Returns 504 if catalog service unavailable.

    Caching: the response carries an ETag, and a conditional request whose
    If-None-Match matches gets a 304 -- the document is still rebuilt (a dataset
    may have been created or dropped since), but an unchanged one costs no
    transfer and no client-side parse. Setting ODATA_METADATA_CACHE_TTL also
    skips the rebuild for that many seconds; see app.routes.v4.metadata_cache
    for why that half is opt-in.

    Example: GET /api/v4/$metadata

    Content-Type: application/xml

    Requires authentication via bearer token or basic auth.
    """
    # Get claims from request state (set by require_authentication dependency)
    claims = request.state.claims
    identity = claims.get("identity")

    # Nothing about the document depends on anything read below, so a cached
    # rendering can be served before a single catalog round trip is made.
    if_none_match = request.headers.get("if-none-match")
    cache_key = ("service", _caller_fingerprint(claims))
    cached = metadata_cache_get(cache_key)
    if cached is not None:
        cached_etag, cached_xml, cached_headers = cached
        return metadata_response(cached_xml, cached_etag, cached_headers, if_none_match)

    from app.auth.permissions import ANONYMOUS_EXEMPT_PAIRS, ANONYMOUS_EXEMPT_WORKSTREAM

    db = _get_firestore_client(database=_firestore_database())
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="dependent service unavailable: firestore",
        )

    if identity is None:
        # Anonymous callers (no Authorization header at all — see
        # app.auth.deps.require_authentication) only ever see the
        # workstream.collection pairs reachable without credentials.
        all_datasets = _get_workspace_datasets(db, {ANONYMOUS_EXEMPT_WORKSTREAM})
        filtered_datasets = [
            dataset
            for dataset in all_datasets
            if (dataset.workspace, dataset.collection) in ANONYMOUS_EXEMPT_PAIRS
        ]
    else:
        # Determine access policies from JWT "policies" claim (do not rely on a "permissions" key).
        from app.auth.permissions import (
            accessible_workstreams,
            get_access_policies_from_claims,
            read_grant_for_relation,
        )

        access_policies_list = get_access_policies_from_claims(claims)

        # Which workspaces to *fetch* from the catalog. This is a superset of what the
        # caller may see -- a policy on "opteryx.ops.findings" reaches the "opteryx"
        # workspace -- so every dataset it returns is authorized individually below.
        workspace_ids = accessible_workstreams(access_policies_list) | {"public"}

        # Collect all datasets. The personal collection and the workspace
        # collection groups are unrelated Firestore queries, so overlap them.
        with ThreadPoolExecutor(max_workers=2) as discovery:
            personal = discovery.submit(_get_personal_datasets, db, identity)
            workspace = discovery.submit(_get_workspace_datasets, db, workspace_ids)
            all_datasets: list[DatasetInfo] = list(personal.result())
            # Raises through, as it did serially, when Firestore is unreachable.
            all_datasets.extend(workspace.result())

        # Filter datasets by read permission
        filtered_datasets: list[DatasetInfo] = []

        for dataset in all_datasets:
            # Personal datasets - full access to own resources
            if dataset.resource == f"personal.{identity}" or dataset.resource.startswith(
                f"personal.{identity}."
            ):
                filtered_datasets.append(dataset)
                continue

            # If dataset is a personal workspace for someone else, do not allow access
            if dataset.resource.startswith("personal."):
                continue

            # Public datasets - read-only for all users
            if dataset.resource.startswith("public."):
                filtered_datasets.append(dataset)
                continue

            # Workspace datasets - the caller needs a read grant on THIS relation, not
            # merely on something else in the same workspace.
            if read_grant_for_relation(access_policies_list, dataset.resource) is not None:
                filtered_datasets.append(dataset)

        # Every workspace the caller has any access to gets the virtual
        # information_schema datasets, including their own personal workspace. They
        # aren't backed by catalog entries, so they're appended directly here rather
        # than discovered via Firestore. Finer-grained filtering happens downstream at
        # query time.
        for workspace in workspace_ids | {"personal"}:
            for name in VIRTUAL_INFORMATION_SCHEMA_TABLES:
                filtered_datasets.append(
                    DatasetInfo(
                        resource=f"{workspace}.information_schema.{name}",
                        workspace=workspace,
                        collection="information_schema",
                        name=name,
                        source="Virtual",
                    )
                )

    # One entry per distinct resource, in a fixed order, so the bytes below --
    # and therefore the ETag -- are a function of the content alone.
    filtered_datasets = _ordered_datasets(filtered_datasets)

    # Everything the document still needs from Firestore, in one batched read.
    facts_by_resource = _facts_for(db, filtered_datasets)

    # Build EDMX XML document
    # Namespaces
    NS_EDMX = "http://docs.oasis-open.org/odata/ns/edmx"
    NS_EDM = "http://docs.oasis-open.org/odata/ns/edm"

    # Root element
    edmx = ET.Element("{" + NS_EDMX + "}Edmx", attrib={"Version": "4.0", "xmlns:edmx": NS_EDMX})

    # DataServices
    data_services = ET.SubElement(edmx, "{" + NS_EDMX + "}DataServices")

    # Schema
    schema = ET.SubElement(
        data_services,
        "{" + NS_EDM + "}Schema",
        attrib={"Namespace": "OData", "xmlns": NS_EDM},
    )

    # Create EntityTypes for each dataset
    entity_names = _entity_names(filtered_datasets)
    entity_types_created = set()
    for dataset in filtered_datasets:
        entity_type_name = entity_names[dataset.resource]

        # Skip if already created (duplicate names)
        if entity_type_name in entity_types_created:
            continue

        # Get schema for this dataset
        columns = facts_by_resource.get(dataset.resource, _NO_FACTS).columns

        if not columns:
            # Skip datasets with no accessible schema
            continue

        # Create EntityType
        entity_type = ET.SubElement(
            schema, "{" + NS_EDM + "}EntityType", attrib={"Name": entity_type_name}
        )

        # The dotted resource name this identifier stands for. Without it the
        # underscored form is a dead end: it cannot be split back apart.
        ET.SubElement(
            entity_type,
            "{" + NS_EDM + "}Annotation",
            attrib={"Term": RESOURCE_TERM, "String": dataset.resource},
        )

        # Add Key element (use first column as key - OData requires a key)
        key = ET.SubElement(entity_type, "{" + NS_EDM + "}Key")
        ET.SubElement(key, "{" + NS_EDM + "}PropertyRef", attrib={"Name": columns[0][0]})

        # Add properties
        for col in columns:
            # Tolerate plain tuples as well as EdmProperty (test stubs supply them).
            col = EdmProperty(*col)
            # First property is the key and must not be nullable
            is_key = col.name == columns[0][0]
            attrib = {"Name": col.name, "Type": col.type}
            if not is_key:
                attrib["Nullable"] = "true"
            else:
                attrib["Nullable"] = "false"
            attrib.update(col.facets)
            prop = ET.SubElement(entity_type, "{" + NS_EDM + "}Property", attrib=attrib)
            # The platform type OData cannot express -- emitted for every
            # property so its absence is never ambiguous.
            if col.source_type:
                ET.SubElement(
                    prop,
                    "{" + NS_EDM + "}Annotation",
                    attrib={"Term": ORIGINAL_TYPE_TERM, "String": col.source_type},
                )

        entity_types_created.add(entity_type_name)

    # Create EntityContainer
    entity_container = ET.SubElement(
        schema, "{" + NS_EDM + "}EntityContainer", attrib={"Name": "DefaultContainer"}
    )

    # Add EntitySets
    for dataset in filtered_datasets:
        entity_type_name = entity_names[dataset.resource]

        # Skip if EntityType wasn't created (no schema)
        if entity_type_name not in entity_types_created:
            continue

        # EntitySet name uses underscores (CSDL SimpleIdentifier compliance)
        entity_set_name = entity_type_name

        entity_set = ET.SubElement(
            entity_container,
            "{" + NS_EDM + "}EntitySet",
            attrib={"Name": entity_set_name, "EntityType": f"OData.{entity_type_name}"},
        )

        # The real dotted resource name, repeated here so a client walking the
        # container alone never has to cross-reference the EntityType.
        ET.SubElement(
            entity_set,
            "{" + NS_EDM + "}Annotation",
            attrib={"Term": RESOURCE_TERM, "String": dataset.resource},
        )

        # Add annotation indicating source type (Table, View, MaterializedView, Virtual)
        ET.SubElement(
            entity_set,
            "{" + NS_EDM + "}Annotation",
            attrib={"Term": "Custom.SourceType", "String": dataset.source},
        )

        # Add last-modified annotation if available: the catalog snapshot read
        # alongside the schema above, falling back to the Firestore doc timestamp
        # picked up while enumerating.
        ts = facts_by_resource.get(dataset.resource, _NO_FACTS).last_modified_ms
        if ts is None:
            ts = getattr(dataset, "last_modified_ms", None)

        if ts:
            try:
                # Convert milliseconds to ISO 8601 UTC string
                iso = _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.UTC).isoformat()
                ET.SubElement(
                    entity_set,
                    "{" + NS_EDM + "}Annotation",
                    attrib={"Term": "Custom.LastModified", "String": iso},
                )
            except Exception:
                # If conversion fails, skip adding annotation
                pass

    # Convert to XML string
    xml_str = ET.tostring(edmx, encoding="unicode", method="xml")

    # Add XML declaration
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content = xml_declaration + xml_str

    # Returning a Response directly bypasses the injected `response`, whose
    # headers FastAPI does not merge in that case -- so OData-Version has to be
    # carried explicitly here rather than set on it.
    headers = {"OData-Version": "4.0"}
    etag = etag_for(xml_content)
    metadata_cache_put(cache_key, etag, xml_content, headers)
    return metadata_response(xml_content, etag, headers, if_none_match)


__all__ = ["clear_metadata_cache", "router"]
