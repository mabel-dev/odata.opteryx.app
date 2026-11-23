"""OData v4 service document endpoint."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_v1.base_query import FieldFilter
from opteryx_access import PERSONAL_POLICY
from opteryx_shared_services.logging import get_logger

from app.auth.deps import require_authentication
from app.odata.content_negotiation import MEDIA_JSON, require_acceptable
from opteryx_shared_services.config import get_config
from app.core import _get_firestore_client
from opteryx_catalog.catalog.sort_order import normalize_sort_order

logger = get_logger()
router = APIRouter(prefix="/api/v4", tags=["OData v4"])


# Resolved per use, not at import. A module-level constant is evaluated once
# at startup and then frozen for the life of the process, so config changes
# would never reach it without a redeploy.
def _firestore_database() -> str:
    return get_config("FIRESTORE_DATABASE")

# A materialized view is a real dataset -- it lives in the `datasets`
# subcollection and has snapshots, a schema and a sort order -- that the catalog
# tags with a `dataset-type` field (MATERIALIZED_VIEW_TYPE in
# opteryx_catalog.opteryx_catalog). Plain datasets carry no such field. The value
# is duplicated rather than imported so these route modules stay importable
# without the catalog package (several tests load them against stubs).
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


# A task -- a statement the platform runs on its own -- lives at
# `{collection}/tasks/{name}`, a sibling subcollection of `datasets` and `views`.
# It shares the one `workspace.collection.<object>` namespace with them, so it is
# listed beside them rather than in a section of its own: a name in that namespace
# identifies exactly one object, and a task that is invisible here still takes its
# name -- CREATE TABLE on it fails with "already exists as a task" for something
# the user can find nowhere in the UI.
#
# A task holds no rows, so it is listed as the single-row table of its own
# definition (app.odata.virtual_datasets.TASK_DEFINITION_COLUMNS) -- a real
# EntitySet with a real url, not an entry a client has to know to skip.
TASK_SOURCE = "Task"
TASKS_SUBCOLLECTION = "tasks"

# A property graph -- SQL/PGQ, `{collection}/property_graphs/{name}` -- shares the
# namespace too, and holds no rows either: it reads as its STRUCTURE, one row per
# element table (PROPERTY_GRAPH_STRUCTURE_COLUMNS), served by the engine. Unlike
# a task its document carries `workspace` and `collection`, so it is listed by
# the same collection-group query as datasets and views.
PROPERTY_GRAPH_SOURCE = "PropertyGraph"
PROPERTY_GRAPHS_SUBCOLLECTION = "property_graphs"


def _source_type_from_doc(data, collection: str) -> str:
    """Source label for a catalog document: View, MaterializedView or Table.

    `collection` is the subcollection the document came from ("datasets" or
    "views"); materialized views live in "datasets" and are told apart from
    plain datasets by their `dataset-type` field. Tasks are not routed through
    here -- they come from their own listing, which knows what they are without
    having to read the document to find out.
    """
    if collection == "views":
        return "View"
    if collection == PROPERTY_GRAPHS_SUBCOLLECTION:
        return PROPERTY_GRAPH_SOURCE
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
    if resource.endswith(".*"):
        resource = resource[:-2]

    # Remove trailing dot if present
    if resource.endswith("."):
        resource = resource[:-1]

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
    """Information about a dataset accessible to the user."""

    def __init__(
        self,
        resource: str,
        workspace: str,
        collection: str,
        name: str,
        source: str = "Table",
        role: str | None = None,
        policy: str | None = None,
        ordered: bool = False,
        order_by: str | None = None,
        order_direction: str | None = None,
        schema_ref=None,
        pending_sort_key: dict | None = None,
    ) -> None:
        self.resource = resource
        self.workspace = workspace
        self.collection = collection
        self.name = name
        self.source = source
        self.policy = policy
        self.role = role
        self.ordered = ordered
        self.order_by = order_by
        self.order_direction = order_direction
        # Internal only (not serialized): where to look up the column name when
        # `pending_sort_key` says the sort key couldn't be name-resolved for free.
        self._schema_ref = schema_ref
        self._pending_sort_key = pending_sort_key

    def __repr__(self) -> str:
        return f"\nDatasetInfo(resource={self.resource}, workspace={self.workspace}, collection={self.collection}, name={self.name}, source={self.source}, role={self.role}, policy={self.policy})"


def _sort_order_from_doc(data: dict) -> tuple[bool, str | None, str | None, dict | None]:
    """Derive (ordered, order_by, order_direction, pending_sort_key) from an
    already-fetched dataset doc.

    ``sort-orders`` is stored on the same doc every listing here already reads, so
    this costs no extra Firestore round trip. When the raw entry is already
    name-based (bare string, or an Iceberg-style dict naming its field), ``order_by``
    is populated directly. When it's index- or field-id-based, the column name isn't
    known without the schema doc; the normalized key is returned as
    ``pending_sort_key`` so the caller can batch-resolve it in a single extra
    Firestore round trip (see ``_resolve_pending_sort_orders`` below) instead of one
    read per dataset.
    """
    normalized = normalize_sort_order(data.get("sort-orders"))
    if not normalized:
        return False, None, None, None
    ascending = normalized.get("ascending", True)
    direction = "asc" if ascending else "desc"
    name = normalized.get("name")
    if name:
        return True, name, direction, None
    return True, None, direction, normalized


def _resolve_pending_sort_orders(db, datasets: list["DatasetInfo"]) -> None:
    """Batch-resolve column names for sort keys that need the schema (index- or
    field-id-based). Every dataset needing resolution is fetched in a SINGLE
    ``get_all()`` call, regardless of how many there are -- one extra Firestore
    round trip for the whole request, not one per dataset. Mutates ``order_by``
    on the given DatasetInfo objects in place; leaves it None (falling back to
    "ordered but unnamed") if a schema doc is missing or the key can't be matched.
    """
    pending = [d for d in datasets if d.ordered and d.order_by is None and d._schema_ref is not None]
    if not pending:
        return

    try:
        schema_docs = db.get_all([d._schema_ref for d in pending])
    except Exception as exc:
        logger.warning(f"Failed to batch-resolve sort order column names: {exc}")
        return

    by_path = {}
    for sdoc in schema_docs:
        try:
            if sdoc.exists:
                by_path[sdoc.reference.path] = sdoc.to_dict() or {}
        except Exception:
            continue

    for dataset in pending:
        sdata = by_path.get(dataset._schema_ref.path)
        if not sdata:
            continue
        columns = sdata.get("columns", [])
        key = dataset._pending_sort_key or {}
        target_fid = key.get("field_id")
        target_index = key.get("index")

        resolved = None
        if target_fid is not None:
            resolved = next((c.get("name") for c in columns if c.get("id") == target_fid), None)
        if resolved is None and target_index is not None and 0 <= target_index < len(columns):
            col = columns[target_index]
            resolved = col.get("name") if isinstance(col, dict) else None

        if resolved:
            dataset.order_by = resolved


def _query_collection_group(
    db,
    collection: str,
    workspace_filter: str | None = None,
    limit: int = 1000,
) -> list[DatasetInfo]:
    """
    Query a collection group (datasets) and return formatted results.

    Args:
        db: Firestore client
        collection: Collection name ("datasets")
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
                # doc.reference.parent is the "datasets"/"views" subcollection;
                # .parent.parent is the collection-document itself, whose id is
                # "$dropped" for drop tombstones or "$properties" for workspace
                # metadata -- both live in a subcollection literally named
                # "datasets"/"views" too, so a bare collection_group query picks
                # them up. Same $-prefix guard as list_collections() uses.
                parent_doc_id = doc.reference.parent.parent.id
                if parent_doc_id.startswith("$"):
                    continue

                data = doc.to_dict()
                workspace = data.get("workspace")
                collection_name = data.get("collection")
                name = data.get("name")

                # Fallback to document ID
                name = name or doc.id

                # Construct fully qualified resource name
                resource_name = f"{workspace}.{collection_name}.{name}"
                resource_name = _normalize_resource_name(resource_name)

                # Determine source type from the collection group ('views' => View)
                # and, for datasets, the doc's `dataset-type` (=> MaterializedView)
                source_type = _source_type_from_doc(data, collection)
                ordered, order_by, order_direction, pending_sort_key = _sort_order_from_doc(data)

                schema_ref = None
                if pending_sort_key is not None:
                    schema_id = data.get("current-schema-id")
                    if schema_id:
                        schema_ref = doc.reference.collection("schemas").document(str(schema_id))

                results.append(
                    DatasetInfo(
                        resource=resource_name,
                        workspace=workspace,
                        collection=collection_name,
                        name=name,
                        source=source_type,
                        role=None,
                        policy=None,
                        ordered=ordered,
                        order_by=order_by,
                        order_direction=order_direction,
                        schema_ref=schema_ref,
                        pending_sort_key=pending_sort_key,
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
        # Query personal datasets and views from the top-level 'personal' collection
        for child in ("datasets", "views"):
            datasets_coll = db.collection("personal").document(identity).collection(child)
            for doc in datasets_coll.stream():
                try:
                    data = doc.to_dict()
                    workspace = "personal"
                    collection_name = identity
                    name = data.get("name") or doc.id

                    resource_name = f"{workspace}.{collection_name}.{name}"
                    resource_name = _normalize_resource_name(resource_name)

                    source_type = _source_type_from_doc(data, child)
                    ordered, order_by, order_direction, pending_sort_key = _sort_order_from_doc(data)

                    schema_ref = None
                    if pending_sort_key is not None:
                        schema_id = data.get("current-schema-id")
                        if schema_id:
                            schema_ref = doc.reference.collection("schemas").document(str(schema_id))

                    results.append(
                        DatasetInfo(
                            resource=resource_name,
                            workspace=workspace,
                            collection=collection_name,
                            name=name,
                            source=source_type,
                            role="owner",
                            policy=PERSONAL_POLICY,
                            ordered=ordered,
                            order_by=order_by,
                            order_direction=order_direction,
                            schema_ref=schema_ref,
                            pending_sort_key=pending_sort_key,
                        )
                    )
                except Exception:
                    continue
    except Exception as exc:
        logger.error(f"Failed to retrieve personal datasets for {identity}: {type(exc)} {exc}")

    return results


def _get_tasks(
    db, workspace_ids: set[str], limit: int = 1000, *, include_unlisted: bool = False
) -> list[DatasetInfo]:
    """Retrieve the tasks in the given workspaces, as single-row datasets.

    Deliberately NOT routed through `_query_collection_group`, because a task
    document is not shaped like a dataset or view document: `create_task` writes
    no `workspace` and no `collection` field (see OpteryxCatalog.create_task).
    Both are therefore read off the document's own path, and the workspace
    restriction is applied here in Python rather than pushed down as a
    `where("workspace", ...)` filter -- against these documents that filter
    matches nothing at all, so reusing that query would have listed no tasks,
    silently and identically to there being none.

    One collection-group query covers every workspace, as opposed to one
    subcollection read per workspace-and-collection: the collections a workspace
    holds are not known here without listing them first, which is the round trip
    this avoids.

    Soft-deleted workspaces are dropped first, for the same reason
    `_get_workspace_datasets` drops them: a dropped workspace's documents can
    still be present momentarily and would otherwise come back looking live.
    """
    if not workspace_ids:
        return []

    results: list[DatasetInfo] = []

    try:
        workspace_ids = _drop_unlistable_workspaces(
            db, workspace_ids, include_unlisted=include_unlisted
        )
        if not workspace_ids:
            return []

        for doc in db.collection_group(TASKS_SUBCOLLECTION).limit(limit).stream():
            try:
                # `.parent` is the "tasks" subcollection, `.parent.parent` the
                # collection document that holds it, and `.parent.parent.parent`
                # the workspace collection -- so the two names a task document
                # does not carry are both one hop away in its path.
                collection_ref = doc.reference.parent.parent
                if collection_ref is None:
                    continue
                collection_name = collection_ref.id
                # Same $-prefix guard the dataset listing uses: "$dropped" and
                # "$properties" are collection documents too, and a bare
                # collection-group query reaches anything filed beneath them.
                if collection_name.startswith("$"):
                    continue

                workspace = collection_ref.parent.id
                if workspace not in workspace_ids:
                    continue

                data = doc.to_dict() or {}
                name = data.get("name") or doc.id

                resource_name = _normalize_resource_name(
                    f"{workspace}.{collection_name}.{name}"
                )

                results.append(
                    DatasetInfo(
                        resource=resource_name,
                        workspace=workspace,
                        collection=collection_name,
                        name=name,
                        source=TASK_SOURCE,
                        role=None,
                        policy=None,
                        # A task has no storage behind it, so there is no
                        # physical sort order to report -- not "unordered by
                        # accident", but nothing that could carry an order.
                        ordered=False,
                    )
                )
            except Exception:
                continue
    except FailedPrecondition as exc:
        # Almost certainly the missing COLLECTION_GROUP_ASC index on `tasks`.
        # Logged at ERROR rather than swallowed for the reason the relationships
        # listing does the same (see metadata._get_relationships): it is a
        # deployment gap that will not heal, and its only symptom is tasks never
        # appearing in the object tree -- which is indistinguishable from a
        # workspace that has no tasks.
        logger.error(
            "Tasks are missing from the service document: the Firestore "
            "collection-group index on `tasks` does not exist. Tasks will not be "
            "listed until it is created. %s",
            exc,
        )
        return []
    except Exception as exc:
        # Non-fatal, unlike the dataset listing: a service document without
        # tasks is incomplete, but a service document that 504s lists nothing
        # at all.
        logger.warning(f"Failed to retrieve tasks: {type(exc)} {exc}")
        return []

    return results


def _starred_workspaces(db, workspace_ids: set[str], identity: str) -> list[str]:
    """Which of `workspace_ids` the caller has starred, for `Custom.Favourites`.

    Delegated to `opteryx_catalog.favourites.starred_workspaces`, which is N
    direct document lookups in one batched `get_all()` over paths already
    known from the policies claim -- NOT a collection-group query, so it
    needs no index and cannot fail the silent way the `tasks` listing above
    can. The library also owns the two guards that matter and must live in
    exactly one place: it keys on the caller's concrete identity and refuses
    the wildcard principal outright (a wildcard star would be every user's
    star at once), and it fails CLOSED to an empty list.

    A second batched round trip rather than refs appended to the one
    `_drop_unlistable_workspaces` issues, deliberately: merging them means
    re-implementing that read here, and with it the wildcard guard the
    library exists to hold once. One more batched read over a workspace set
    that is already in hand is the cheaper of the two mistakes.

    Imported lazily: the module ships in opteryx-catalog >= 0.4.165, and an
    older installed release must cost the caller their stars, not their
    service document. Every failure is an empty list for the same reason --
    the tree falls back to alphabetical, which is what it did before this
    feature existed, and never to another principal's list.
    """
    if not identity or not workspace_ids:
        return []
    try:
        from opteryx_catalog import favourites
    except ImportError:
        return []
    try:
        return favourites.starred_workspaces(db, sorted(workspace_ids), identity)
    except Exception as exc:
        logger.debug(f"Favourites unavailable for this service document: {type(exc)} {exc}")
        return []


def _drop_unlistable_workspaces(
    db, workspace_ids: set[str], *, include_unlisted: bool = False
) -> set[str]:
    """Filter out workspaces that should not be listed, so their datasets never
    reach the collection-group query below in the first place.

    TWO REASONS, one read. A workspace is dropped here when it no longer really
    exists, and when it exists but has asked not to appear in listings
    (`listed: false` - see opteryx_catalog's `LISTED_PROPERTY`). Both are
    answered from the same `$properties` document this already fetches, so the
    second costs nothing.

    `listed` IS NOT A PERMISSION. An unlisted workspace is still queryable by
    name, still appears in information_schema, and is still named in provenance
    and fork relationships. What it is not is put in front of everyone: a
    library namespace like `samples` holds datasets meant to be forked once,
    and listing them would put the same forty rows in every account's catalog
    forever. Who may READ any of it is decided by grants, in opteryx-access,
    and nowhere else - including here.

    Reads each workspace's `$properties` doc directly rather than
    constructing a catalog handle per workspace, since that handle also
    stands up its own Firestore/GCS clients and this call site only needs
    to know whether the doc is there. One batched get_all() covers every
    workspace_id in a single round trip, the same batching shape
    _resolve_pending_sort_orders uses below.

    A missing `$properties` doc now means "gone" (excluded) for anything
    except `public`/`personal`, which are special-cased because they are
    never provisioned through the normal create flow and so never had a
    doc to begin with. DROP WORKSPACE (opteryx_catalog.OpteryxCatalog.
    drop_workspace) deletes the doc outright rather than flagging it, so a
    workspace that was dropped is now indistinguishable, at the Firestore
    level, from one that never existed -- both read as "doc missing", and
    both belong out of the result for the same reason: there is nothing
    real behind the name. A `deleted-at-ms` flag on a doc that DOES still
    exist is checked too, for a workspace stuck with that legacy marker
    from before DROP WORKSPACE replaced the soft-delete model it belonged
    to -- that field is not written by anything anymore, but a workspace
    already carrying it from before this change is still effectively
    deleted until something clears the doc outright.

    `include_unlisted` keeps a `listed: false` workspace while still dropping a
    deleted one. For the ANONYMOUS listing only: `demo` is unlisted so it stays
    out of signed-in catalogs, but it is the whole of what a caller with no
    credentials can read, and a listing that hid it would show them nothing.
    (A signed-in listing never fetches `demo` in the first place - implicit
    grants never reach a token's policies - so the flag only ever matters here.)
    """
    if not workspace_ids:
        return workspace_ids

    try:
        # Building the refs is inside the guard, not before it. It reaches into
        # `db` exactly as the read does, so a client that cannot answer raises
        # HERE - and with the construction outside, that raise escaped the
        # fail-open this comment promises and took the whole listing down.
        refs = [db.collection(ws).document("$properties") for ws in workspace_ids]
        docs = db.get_all(refs)
    except Exception as exc:
        # Fail open: a Firestore hiccup here must not take the whole listing
        # down, and the alternative (treating every workspace as deleted, or
        # as unlisted) would empty every caller's dataset list on a transient
        # error.
        logger.warning(f"Failed to read workspace properties for listing check: {exc}")
        return workspace_ids

    gone = set()
    for doc in docs:
        try:
            workspace_id = doc.reference.parent.id
            if not doc.exists:
                if workspace_id not in ("public", "personal"):
                    gone.add(workspace_id)
                continue
            properties = doc.to_dict() or {}
            if properties.get("deleted-at-ms") is not None:
                gone.add(workspace_id)
                continue
            # Listed unless the workspace explicitly says otherwise, and an
            # unrecognised value reads as listed. The failure modes are not
            # symmetric: an unlisted workspace that shows up is clutter, while
            # a listed one that vanishes is somebody's data disappearing out of
            # their catalog - so this fails toward the status quo, not toward
            # hiding. Mirrors opteryx_catalog's `_guard_is_on`.
            listed = properties.get("listed")
            if not include_unlisted and listed is not None and not listed:
                gone.add(workspace_id)
        except Exception:
            continue

    return workspace_ids - gone


def _get_workspace_datasets(
    db, workspace_ids: set[str], *, include_unlisted: bool = False
) -> list[DatasetInfo]:
    """Retrieve all datasets from the given workspace IDs.

    Soft-deleted workspaces are excluded before the collection-group query
    runs, not filtered out of its results afterward -- a dropped workspace's
    datasets can still be real Firestore documents momentarily (drop_workspace
    tombstones each one for reclamation rather than deleting synchronously)
    and would otherwise come back from this query exactly like any live
    workspace's do. Re-filters even when the caller already filtered
    `workspace_ids` (get_service_document does, for the information_schema
    loop's sake) -- redundant in that case, not incorrect, and this is the
    only thing standing between the anonymous caller's fixed workstream and
    the same leak if it's ever soft-deleted.
    """
    results = []

    try:
        workspace_ids = _drop_unlistable_workspaces(
            db, workspace_ids, include_unlisted=include_unlisted
        )
        for workspace_id in workspace_ids:
            # Query both datasets and views collection groups
            for coll in ("datasets", "views", PROPERTY_GRAPHS_SUBCOLLECTION):
                datasets = _query_collection_group(
                    db,
                    coll,
                    workspace_filter=workspace_id,
                    limit=1000,
                )
                results.extend(datasets)
    except Exception as exc:
        logger.error(f"Failed to retrieve datasets: {type(exc)} {exc}")
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="failed to read datasets from firestore",
        ) from exc

    # A name identifies one object - the catalog refuses to create a second under
    # a taken name - but a document written before that guard can still pair a
    # dataset (e.g. a materialized view) with a leftover `views` document of the
    # same name, which listed the name twice. Keep one entry per resource, the
    # `datasets` document winning, as $metadata (`_ordered_datasets`) and the
    # per-dataset $metadata (load_dataset before load_view) already resolve it.
    unique: dict[str, DatasetInfo] = {}
    for dataset in results:
        kept = unique.setdefault(dataset.resource, dataset)
        if kept is not dataset:
            logger.warning(
                f"{dataset.resource} is catalogued as both {kept.source} and "
                f"{dataset.source}; listing it once, as {kept.source}"
            )
    return list(unique.values())


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="OData v4 Service Document",
    description="Returns the OData v4 service document listing all accessible EntitySets grouped by workspace and collection.",
    responses={
        200: {"description": "Service document with EntitySet list and access metadata"},
        401: {"description": "Missing or invalid authentication"},
        406: {"description": "Accept header does not admit application/json, the only format this route returns"},
    },
    dependencies=[Depends(require_authentication), Depends(require_acceptable(MEDIA_JSON))],
)
def get_service_document(
    request: Request,
    response: Response,
) -> dict:
    """
    Return OData v4 service document listing all accessible EntitySets.

    The service document is the entry point for OData clients. It lists all datasets
    (EntitySets) the authenticated user can access, organized by workspace and collection.

    Response includes:
    - @odata.context: service root URL
    - value: array of EntitySet objects with:
        * name: fully-qualified dataset name (workspace.collection.dataset)
        * kind: always "EntitySet"
        * url: relative URL for querying (workspace/collection/dataset)
        * source: "Table", "View", "MaterializedView" (a dataset the catalog marks
          with dataset-type "materialized_view" -- physically a table, refreshed
          from a query), "ExternalTable" (a projection of a workspace's own
          catalog), "Task" (a statement the platform runs on its own, listed here
          because it shares the workspace.collection.<object> namespace with the
          rest, and readable as the single row of its own definition) or
          "PropertyGraph" (a SQL/PGQ graph over catalog tables, readable as its
          structure -- one row per vertex or edge table) or "Virtual"
          (synthetic, e.g. information_schema.tables) indicating dataset type
        * role: user's permission level (reader, writer, admin, owner)
        * policy: access-control policy that grants permission
        * ordered: whether the underlying storage has a configured physical sort order
        * orderBy: the sort column name. Name-based sort_orders entries resolve for
          free from the dataset doc already read; index/field-id-based entries are
          resolved with one extra *batched* Firestore round trip covering every
          visible dataset that needs it (not one read per dataset)
        * orderDirection: "asc" or "desc" when ordered is true, else null

    Access control:
    - Personal workspace (personal.{identity}): owned datasets accessible only to owner
    - Public workspace: read-only access for all authenticated users
    - Team workspaces: access granted by role-based policies in JWT claims

    Example: GET /api/v4/

    Requires authentication via bearer token or basic auth.
    """
    # Add OData-Version header
    response.headers["OData-Version"] = "4.0"

    # Get claims from request state (set by require_authentication dependency)
    claims = request.state.claims
    identity = claims.get("identity")

    from opteryx_access import VIRTUAL_INFORMATION_SCHEMA_POLICY
    from opteryx_access import implicitly_readable_workspaces

    from app.auth.permissions import grants_from_policies
    from app.auth.permissions import read_access

    db = _get_firestore_client(database=_firestore_database())
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="dependent service unavailable: firestore",
        )

    # None for an anonymous caller (nobody to have starred anything), so the
    # annotation is omitted rather than emitted empty -- a client that sees
    # no annotation keeps whatever it already knew, and an anonymous session
    # has nothing to correct it with.
    starred_workspaces = None

    if identity is None:
        # Anonymous callers (no Authorization header at all — see
        # app.auth.deps.require_authentication) see what opteryx-access lets a
        # caller with no identity read: the `demo` workspace. The library names
        # the workspaces to fetch (a superset) and decides each dataset in them.
        #
        # NOT passed through `_drop_unlistable_workspaces`: `demo` is unlisted
        # for signed-in catalogs, but it is the whole of the anonymous one.
        anonymous_workspaces = set(implicitly_readable_workspaces(None))
        all_datasets = _get_workspace_datasets(db, anonymous_workspaces, include_unlisted=True)
        all_datasets.extend(_get_tasks(db, anonymous_workspaces, include_unlisted=True))
        filtered_datasets = []
        for dataset in all_datasets:
            access = read_access([], dataset.resource, None, grants=[])
            if access is not None:
                dataset.role, dataset.policy = access
                filtered_datasets.append(dataset)
    else:
        # Determine access policies from JWT "policies" claim (do not rely on a "permissions" key).
        from app.auth.permissions import accessible_workstreams
        from app.auth.permissions import entitlements_from_claims
        from app.auth.permissions import get_access_policies_from_claims
        from app.auth.permissions import visible_virtual_information_schema

        access_policies_list = get_access_policies_from_claims(claims)
        caller_grants = grants_from_policies(access_policies_list)

        # Which workspaces to *fetch* from the catalog. This is a superset of what the
        # caller may see -- a policy on "opteryx.ops.findings" reaches the "opteryx"
        # workspace -- so every dataset it returns is authorized individually below.
        #
        # Filtered for soft-deleted workspaces here, once, rather than only
        # inside _get_workspace_datasets below: the virtual
        # information_schema loop further down used to reuse this same
        # (unfiltered) set directly, which meant a deleted workspace's real
        # datasets disappeared but `<workspace>.information_schema.*` kept
        # showing up regardless - an empty-looking but still-present
        # workspace folder in the tree UI.
        workspace_ids = _drop_unlistable_workspaces(
            # `public` is unioned in BY NAME because it is universally readable and
            # nothing in the caller's policies claim says so - implicit grants are
            # never minted into a token.
            #
            # `samples` is universally readable on exactly the same footing and is
            # DELIBERATELY NOT HERE. It holds the forkable sample datasets, which
            # are five scale factors of TPC-H: adding it would put forty datasets
            # in the catalog tree of every account on the platform, forever, to be
            # forked once. Readable is not the same as worth listing. Anyone can
            # still SELECT from it or CLONE it by name - see opteryx_access's
            # `implicit_grants`, which is where both namespaces get their read.
            db, accessible_workstreams(access_policies_list) | {"public"}
        )

        # The caller's stars, over the same set of workspaces just resolved
        # plus `personal`, which is starrable but never in `workspace_ids`
        # (that set is built from policy-bearing workstreams). Surfaced as a
        # document-level annotation, not as entries in `value`: the service
        # document has no workspace entity to carry a flag, and inventing one
        # would be exactly the "entry a client has to know to skip" the tasks
        # listing refuses to be.
        starred_workspaces = _starred_workspaces(db, workspace_ids | {"personal"}, identity)

        # Collect all datasets
        all_datasets: list[DatasetInfo] = []
        all_datasets.extend(_get_personal_datasets(db, identity))
        all_datasets.extend(_get_workspace_datasets(db, workspace_ids))
        # Tasks come from their own collection group rather than from either of
        # the two above, and are authorized by the same loop below -- a task the
        # caller has no read grant on is dropped exactly as a table would be.
        # `personal` is added to the fetch set (it is not in `workspace_ids`,
        # which is built from policy-bearing workstreams) so the caller's own
        # personal tasks are discovered; every other identity's are dropped
        # below, by the same rule that drops their datasets.
        all_datasets.extend(_get_tasks(db, workspace_ids | {"personal"}))

        # Filter datasets by read permission
        filtered_datasets: list[DatasetInfo] = []

        for dataset in all_datasets:
            # Every dataset is decided by opteryx-access on THIS relation -- the
            # caller's own personal namespace, `public`, and policy-granted
            # workspaces alike -- and stamped with the role and policy the
            # library reports. Someone else's personal datasets, and anything
            # held only by a grant on a neighbouring relation, come back None.
            access = read_access(
                access_policies_list, dataset.resource, identity, grants=caller_grants
            )
            if access is not None:
                dataset.role, dataset.policy = access
                filtered_datasets.append(dataset)

        # Every workspace the caller has any access to gets the virtual
        # information_schema datasets, including their own personal workspace. They
        # aren't backed by catalog entries, so they're appended directly here rather
        # than discovered via Firestore. Which tables are offered depends on the
        # role the caller holds in the workspace: `tables`, `columns` and the rest
        # of the shape for anyone, `triggers`, `tasks` and `grants` only where they
        # hold owner - the same tiers the engine filters the rows at, so a table
        # is never offered that would only ever come back empty. Which rows are in
        # it is decided downstream, at query time.
        for workspace in workspace_ids | {"personal"}:
            for name, role in visible_virtual_information_schema(
                access_policies_list,
                workspace,
                entitlements=sorted(entitlements_from_claims(claims)),
                identity=identity,
            ):
                filtered_datasets.append(
                    DatasetInfo(
                        resource=f"{workspace}.information_schema.{name}",
                        workspace=workspace,
                        collection="information_schema",
                        name=name,
                        source="Virtual",
                        role=role,
                        policy=VIRTUAL_INFORMATION_SCHEMA_POLICY,
                    )
                )

    # One extra Firestore round trip (batched) to resolve column names for any
    # visible dataset whose sort order is index/field-id based. Only runs against
    # the already-filtered, visible set -- no wasted reads for datasets the caller
    # can't see.
    _resolve_pending_sort_orders(db, filtered_datasets)

    entity_sets = []
    for dataset in filtered_datasets:
        entity_sets.append(
            {
                "name": dataset.resource,
                "kind": "EntitySet",
                "url": f"{dataset.workspace}/{dataset.collection}/{dataset.name}",
                "source": getattr(dataset, "source", "Table"),
                "role": getattr(dataset, "role", None),
                "policy": getattr(dataset, "policy", None),
                "ordered": getattr(dataset, "ordered", False),
                "orderBy": getattr(dataset, "order_by", None),
                "orderDirection": getattr(dataset, "order_direction", None),
            }
        )

    document = {
        "@odata.context": "/api/v4/$metadata",
        "value": entity_sets,
    }
    # Present for every authenticated caller, EMPTY included: an empty list is
    # a real answer ("nothing starred") and lets a client discard stars it
    # remembered from another device the caller has since unstarred on. Only
    # an anonymous document leaves it out.
    if starred_workspaces is not None:
        document["Custom.Favourites"] = starred_workspaces
    return document


__all__ = ["router"]
