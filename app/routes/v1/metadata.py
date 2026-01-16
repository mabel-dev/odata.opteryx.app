"""OData metadata endpoints for discovering datasets."""

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from google.cloud.firestore_v1.base_query import FieldFilter
from orso.logging import get_logger

from app.auth.deps import require_bearer_token
from app.core import _get_firestore_client

logger = get_logger()
router = APIRouter(prefix="/v1")


def _extract_identity_from_claims(claims: dict) -> str:
    """Extract user identity from JWT claims."""
    identity = claims.get("sub") or claims.get("email") or claims.get("preferred_username")
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token subject"
        )
    return identity


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


def _extract_workspace_from_pattern(pattern: str) -> str | None:
    """Return the workspace id embedded in a policy pattern."""
    if not pattern or not isinstance(pattern, str):
        return None

    # Use the portion before the first dot/wildcard as the workspace identifier
    first_segment = pattern.strip(".")
    first_segment = first_segment.split(".", 1)[0]
    workspace = first_segment.replace("*", "").strip()

    return workspace or None


def _extract_policies_from_claims(claims: dict) -> list[tuple[str, str, str]]:
    """Normalize policy triplets from JWT claims.

    Returns a list of (policy_id, role, pattern) tuples.
    """
    normalized: list[tuple[str, str, str]] = []
    for entry in claims.get("policies", []) or []:
        policy_id: str | None = None
        role: str | None = None
        pattern: str | None = None

        if isinstance(entry, (list, tuple)) and len(entry) == 3:
            policy_id, role, pattern = entry
        elif isinstance(entry, dict):
            policy_id = entry.get("id") or entry.get("policy_id")
            role = entry.get("role")
            pattern = entry.get("pattern") or entry.get("path")

        if not (policy_id and isinstance(role, str) and isinstance(pattern, str)):
            continue

        normalized.append((str(policy_id), role, pattern))

    return normalized


def _group_policies_by_workspace(
    policies: list[tuple[str, str, str]],
) -> dict[str, list[tuple[str, str, str]]]:
    """Organize policy triplets by workspace for quick lookups."""
    grouped: dict[str, list[tuple[str, str, str]]] = {}
    for policy_id, role, pattern in policies:
        workspace = _extract_workspace_from_pattern(pattern)
        if not workspace:
            continue
        grouped.setdefault(workspace, []).append((pattern, role, policy_id))

    return grouped


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
    "/datasets",
    status_code=status.HTTP_200_OK,
    summary="Get accessible datasets for user",
    description="Returns all datasets that the user can read.",
)
def get_accessible_datasets(
    claims: dict = Depends(require_bearer_token),
) -> dict:
    """
    Query Firestore for datasets the user can read.

    Returns a list of datasets organized by workspace and collection that the user
    has read access to through their assigned policies and personal collections.
    """
    identity = _extract_identity_from_claims(claims)

    # Extract policy triplets from the JWT and group them by workspace
    policies = _extract_policies_from_claims(claims)
    access_policies = _group_policies_by_workspace(policies)

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

    # Filter datasets by read permission and access policy patterns
    import fnmatch

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

        # Public datasets - read-only
        if dataset.resource.startswith("public."):
            filtered_datasets.append(dataset)
            continue

        # Workspace datasets - check role permissions
        workspace = dataset.resource.split(".")[0]
        policies_for_workspace = access_policies.get(workspace, [])
        allowed = False
        for pattern, role, _policy_id in policies_for_workspace:
            if fnmatch.fnmatch(dataset.resource, pattern) and _role_allows_read(role):
                allowed = True
                break

        if allowed:
            filtered_datasets.append(dataset)

    # Format response
    datasets_by_workspace: dict[str, dict[str, list[str]]] = {}
    for dataset in filtered_datasets:
        workspace = datasets_by_workspace.setdefault(dataset.workspace, {})
        collection_list = workspace.setdefault(dataset.collection, [])
        collection_list.append(dataset.name)

    return {
        "datasets": datasets_by_workspace,
    }


__all__ = ["router"]
