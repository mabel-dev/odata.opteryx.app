"""OData v4 EDMX metadata endpoint."""

import xml.etree.ElementTree as ET
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from google.cloud.firestore_v1.base_query import FieldFilter
from orso.logging import get_logger
from opteryx_catalog import OpteryxCatalog

from app.auth.deps import require_authentication
from app.core import _get_firestore_client

logger = get_logger()
router = APIRouter(prefix="/api/v4")

# Get catalog configuration from environment
import os

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")


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


def _map_py_type_to_edm(py_type: type) -> str:
    """Map Python type to EDM type."""
    import datetime as _dt
    import decimal as _dec

    try:
        import uuid as _uuid
    except Exception:
        _uuid = None

    if py_type is int:
        return "Edm.Int64"
    if py_type is float:
        return "Edm.Double"
    if py_type is bool:
        return "Edm.Boolean"
    if py_type is str:
        return "Edm.String"
    if _uuid and py_type is _uuid.UUID:
        return "Edm.Guid"
    if py_type is _dec.Decimal:
        return "Edm.Double"
    if py_type in (_dt.datetime, _dt.date, _dt.time):
        return "Edm.DateTimeOffset"
    return "Edm.String"


def _get_dataset_schema(dataset_info: DatasetInfo) -> list[tuple[str, str]]:
    """Get dataset schema from catalog instead of querying Opteryx.
    
    Args:
        dataset_info: DatasetInfo with workspace, collection, name
        
    Returns:
        List of (column_name, edm_type) tuples
    """
    try:
        # Initialize catalog for this workspace
        catalog = OpteryxCatalog(
            dataset_info.workspace,
            firestore_project=GCP_PROJECT_ID,
            firestore_database=FIRESTORE_DATABASE,
            gcs_bucket=BUCKET_NAME,
        )
        
        # Load dataset from catalog (namespace.table_name format)
        dataset_path = f"{dataset_info.collection}.{dataset_info.name}"
        dataset = catalog.load_dataset(dataset_path)
        
        # Get schema from catalog
        schema = dataset.schema()
        
        columns = []
        for field in schema.fields:
            col_name = field.name
            # Map Arrow/Iceberg type to EDM type
            edm_type = _map_py_type_to_edm(field.type)
            columns.append((col_name, edm_type))
        
        return columns
        
    except Exception as exc:
        logger.warning(f"Failed to get schema from catalog for {dataset_info.resource}: {exc}")
        return []


@router.get(
    "/$metadata",
    status_code=status.HTTP_200_OK,
    summary="OData v4 EDMX Metadata Document",
    description="Returns the OData v4 EDMX metadata document describing all accessible EntityTypes.",
    dependencies=[Depends(require_authentication)],
)
def get_edmx_metadata(
    request: Request,
    response: Response,
) -> Any:
    """
    Return OData v4 EDMX metadata document.

    The metadata document provides a complete description of the data model,
    including EntityTypes (with properties and types) and EntitySets for all
    datasets the authenticated user has access to.
    """
    # Set OData-Version header and XML content type
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
    workspace_roles: dict[str, list[str]] = {}  # workspace -> [roles]
    for role, workspace in permissions:
        workspace_roles.setdefault(workspace, []).append(role)

    db = _get_firestore_client(database="catalogs")
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="dependent service unavailable: firestore",
        )

    # Determine which workspace ids to query (access workspaces + public)
    workspace_ids = set(workspace_roles.keys()) | {"public"}

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
        roles = workspace_roles.get(workspace, [])
        if any(_role_allows_read(role) for role in roles):
            filtered_datasets.append(dataset)

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
    entity_types_created = set()
    for dataset in filtered_datasets:
        entity_type_name = dataset.resource.replace(".", "_")

        # Skip if already created (duplicate names)
        if entity_type_name in entity_types_created:
            continue

        # Get schema for this dataset
        columns = _get_dataset_schema(dataset)

        if not columns:
            # Skip datasets with no accessible schema
            logger.debug(f"Skipping dataset with no schema: {dataset.resource}")
            continue

        # Create EntityType
        entity_type = ET.SubElement(
            schema, "{" + NS_EDM + "}EntityType", attrib={"Name": entity_type_name}
        )

        # Add Key element (use first column as key - OData requires a key)
        key = ET.SubElement(entity_type, "{" + NS_EDM + "}Key")
        ET.SubElement(
            key, "{" + NS_EDM + "}PropertyRef", attrib={"Name": columns[0][0]}
        )

        # Add properties
        for col_name, edm_type in columns:
            ET.SubElement(
                entity_type,
                "{" + NS_EDM + "}Property",
                attrib={"Name": col_name, "Type": edm_type, "Nullable": "true"},
            )

        entity_types_created.add(entity_type_name)

    # Create EntityContainer
    entity_container = ET.SubElement(
        schema, "{" + NS_EDM + "}EntityContainer", attrib={"Name": "DefaultContainer"}
    )

    # Add EntitySets
    for dataset in filtered_datasets:
        entity_type_name = dataset.resource.replace(".", "_")

        # Skip if EntityType wasn't created (no schema)
        if entity_type_name not in entity_types_created:
            continue

        # EntitySet name uses dots (as in service document)
        entity_set_name = dataset.resource

        ET.SubElement(
            entity_container,
            "{" + NS_EDM + "}EntitySet",
            attrib={"Name": entity_set_name, "EntityType": f"OData.{entity_type_name}"},
        )

    # Convert to XML string
    xml_str = ET.tostring(edmx, encoding="unicode", method="xml")

    # Add XML declaration
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml_content = xml_declaration + xml_str

    return Response(content=xml_content, media_type="application/xml")


__all__ = ["router"]
