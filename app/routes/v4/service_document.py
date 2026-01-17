"""OData v4 service document endpoint."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from google.cloud.firestore_v1.base_query import FieldFilter
from orso.logging import get_logger

from app.auth.deps import require_authentication
from app.core import _get_firestore_client

logger = get_logger()
router = APIRouter(prefix="/api/v4")


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

    def __init__(self, resource: str, workspace: str, collection: str, name: str):
        self.resource = resource
        self.workspace = workspace
        self.collection = collection
        self.name = name


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

                results.append(
                    DatasetInfo(
                        resource=resource_name,
                        workspace=workspace,
                        collection=collection_name,
                        name=name,
                    )
                )
            except Exception:
                logger.debug(f"Skipping malformed dataset document: {doc.id}")
                continue
    except Exception as exc:
        logger.debug(
            f"Failed to fetch {collection} for workspace {workspace_filter}: {type(exc)} {exc}"
        )

    return results


def _get_personal_datasets(db, identity: str) -> list[DatasetInfo]:
    """Retrieve datasets from user's personal collection."""
    results = []
    logger.debug(f"Getting personal datasets for {identity}")

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
                    )
                )
            except Exception:
                logger.debug(f"Skipping malformed personal dataset: {doc.id}")
                continue
    except Exception as exc:
        logger.error(f"Failed to retrieve personal datasets for {identity}: {type(exc)} {exc}")

    return results


def _get_workspace_datasets(db, workspace_ids: set[str]) -> list[DatasetInfo]:
    """Retrieve all datasets from the given workspace IDs."""
    results = []

    try:
        for workspace_id in workspace_ids:
            datasets = _query_collection_group(
                db,
                "datasets",
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

    return results


def _role_allows_read(role: str | None) -> bool:
    """Check if a role allows read action."""
    if not role:
        return False
    role_lower = role.lower()
    return role_lower in {"reader", "writer", "admin", "owner"}


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="OData v4 Service Document",
    description="Returns the OData v4 service document with accessible EntitySets.",
    dependencies=[Depends(require_authentication)],
)
def get_service_document(
    request: Request,
    response: Response,
) -> dict:
    """
    Return OData v4 service document listing all accessible EntitySets.

    The service document provides a list of all EntitySets (datasets) that the
    authenticated user has permission to access.
    """
    # Add OData-Version header
    response.headers["OData-Version"] = "4.0"

    # Get claims from request state (set by require_authentication dependency)
    claims = request.state.claims
    identity = claims.get("identity")
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token identity"
        )

    # Get normalized permissions: list of (role, workspace) tuples
    permissions = claims.get("permissions", [])

    # Group permissions by workspace
    access_policies: dict[str, list[str]] = {}  # workspace -> [roles]
    for role, workspace in permissions:
        access_policies.setdefault(workspace, []).append(role)

    db = _get_firestore_client(database="catalogs")
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="dependent service unavailable: firestore",
        )

    # Determine which workspace ids to query (access workspaces + public)
    workspace_ids = set(access_policies.keys()) | {"public"}

    # Collect all datasets
    all_datasets: list[DatasetInfo] = []
    all_datasets.extend(_get_personal_datasets(db, identity))
    all_datasets.extend(_get_workspace_datasets(db, workspace_ids))

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

        # Workspace datasets - check if user has any read role for this workspace
        workspace = dataset.resource.split(".")[0]
        roles = access_policies.get(workspace, [])
        if any(_role_allows_read(role) for role in roles):
            filtered_datasets.append(dataset)

    # Build OData service document
    entity_sets = []
    for dataset in filtered_datasets:
        # EntitySet name: workspace.collection.dataset (dots)
        name = dataset.resource
        # EntitySet url: workspace/collection/dataset (path segments)
        url = f"{dataset.workspace}/{dataset.collection}/{dataset.name}"

        entity_sets.append({"name": name, "kind": "EntitySet", "url": url})

    return {
        "@odata.context": "/api/v4/$metadata",
        "value": entity_sets,
    }


__all__ = ["router"]
