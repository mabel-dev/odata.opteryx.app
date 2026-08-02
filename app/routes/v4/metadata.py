"""OData v4 EDMX metadata endpoint."""

# Get catalog configuration from environment
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import Response
from fastapi import status
from google.cloud.firestore_v1.base_query import FieldFilter
from opteryx_catalog import OpteryxCatalog
from app.logging import get_logger

from app.auth.deps import require_authentication
from app.core import _get_firestore_client
from app.odata.edm_types import column_type_to_edm
from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_TABLES
from app.odata.virtual_datasets import is_virtual_information_schema

logger = get_logger()
router = APIRouter(prefix="/api/v4")


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

    def __init__(
        self,
        resource: str,
        workspace: str,
        collection: str,
        name: str,
        source: str = "Table",
        last_modified_ms: int | None = None,
    ):
        self.resource = resource
        self.workspace = workspace
        self.collection = collection
        self.name = name
        self.source = source
        self.last_modified_ms = last_modified_ms


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

                # Determine source type based on collection group ('datasets' => Table, 'views' => View)
                source_type = "View" if collection == "views" else "Table"

                # Try to capture last modified information from Firestore doc (common field names)
                last_modified = None
                for key in (
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
                                import datetime as _dt

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
                    )
                )
            except Exception:
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






@lru_cache(maxsize=512)
def _get_dataset_schema(dataset_info: DatasetInfo) -> list[tuple[str, str]]:
    """Get dataset schema from catalog instead of querying Opteryx.

    Args:
        dataset_info: DatasetInfo with workspace, collection, name

    Returns:
        List of (column_name, edm_type) tuples
    """
    if is_virtual_information_schema(dataset_info.collection, dataset_info.name):
        return VIRTUAL_INFORMATION_SCHEMA_TABLES[dataset_info.name]

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
        for column in schema.columns:
            col_name = column.name
            # Map Opteryx type to EDM type
            edm_type = column_type_to_edm(column.type)
            columns.append((col_name, edm_type))

        return columns

    except Exception as exc:
        logger.warning(f"Failed to get schema from catalog for {dataset_info.resource}: {exc}")
        return []


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
def get_edmx_metadata(
    request: Request,
    response: Response,
) -> Any:
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
            - Annotations: Custom.SourceType (Table/View), Custom.LastModified
          * EntityContainer (wrapper for EntitySets)
            - EntitySet elements: one per dataset
              * Name: underscored dataset name (CSDL SimpleIdentifier)
              * EntityType: reference to matching EntityType
              * Annotations: source type, access control, last modified timestamp

    Custom annotations:
    - Custom.SourceType: "Table" or "View"
    - Custom.LastModified: ISO 8601 timestamp of last modification
    - Per-dataset column statistics available at /{ws}/{coll}/{ds}/$metadata

    Access control: Filtered by authenticated user's permissions. Personal/public
    workspaces handled automatically. Returns 504 if catalog service unavailable.

    Example: GET /api/v4/$metadata

    Content-Type: application/xml

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header and XML content type
    response.headers["OData-Version"] = "4.0"

    # Get claims from request state (set by require_authentication dependency)
    claims = request.state.claims
    identity = claims.get("identity")

    from app.auth.permissions import ANONYMOUS_EXEMPT_PAIRS, ANONYMOUS_EXEMPT_WORKSTREAM

    db = _get_firestore_client(database="catalogs")
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
        from app.auth.permissions import accessible_workstreams
        from app.auth.permissions import get_access_policies_from_claims
        from app.auth.permissions import read_grant_for_relation

        access_policies_list = get_access_policies_from_claims(claims)

        # Which workspaces to *fetch* from the catalog. This is a superset of what the
        # caller may see -- a policy on "opteryx.ops.findings" reaches the "opteryx"
        # workspace -- so every dataset it returns is authorized individually below.
        workspace_ids = accessible_workstreams(access_policies_list) | {"public"}

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
            continue

        # Create EntityType
        entity_type = ET.SubElement(
            schema, "{" + NS_EDM + "}EntityType", attrib={"Name": entity_type_name}
        )

        # Add Key element (use first column as key - OData requires a key)
        key = ET.SubElement(entity_type, "{" + NS_EDM + "}Key")
        ET.SubElement(key, "{" + NS_EDM + "}PropertyRef", attrib={"Name": columns[0][0]})

        # Add properties
        for col_name, edm_type in columns:
            # First property is the key and must not be nullable
            is_key = col_name == columns[0][0]
            attrib = {"Name": col_name, "Type": edm_type}
            if not is_key:
                attrib["Nullable"] = "true"
            else:
                attrib["Nullable"] = "false"
            ET.SubElement(entity_type, "{" + NS_EDM + "}Property", attrib=attrib)

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

        # EntitySet name uses underscores (CSDL SimpleIdentifier compliance)
        entity_set_name = dataset.resource.replace(".", "_")

        entity_set = ET.SubElement(
            entity_container,
            "{" + NS_EDM + "}EntitySet",
            attrib={"Name": entity_set_name, "EntityType": f"OData.{entity_type_name}"},
        )

        # Add annotation indicating source type (Table or View)
        ET.SubElement(
            entity_set,
            "{" + NS_EDM + "}Annotation",
            attrib={"Term": "Custom.SourceType", "String": dataset.source},
        )

        # Add last-modified annotation if available (try catalog first, then Firestore doc timestamp)
        try:
            # Try to get last modified from catalog snapshot
            catalog = OpteryxCatalog(
                dataset.workspace,
                firestore_project=GCP_PROJECT_ID,
                firestore_database=FIRESTORE_DATABASE,
                gcs_bucket=BUCKET_NAME,
            )
            ds_obj = catalog.load_dataset(f"{dataset.collection}.{dataset.name}")
            snapshot = ds_obj.snapshot()
            ts = getattr(snapshot, "timestamp_ms", None)
        except Exception:
            ts = None

        # Fallback to dataset.last_modified_ms (from Firestore doc)
        if ts is None:
            ts = getattr(dataset, "last_modified_ms", None)

        if ts:
            try:
                import datetime as _dt

                # Convert milliseconds to ISO 8601 UTC string
                iso = (
                    _dt.datetime.fromtimestamp(ts / 1000.0, tz=_dt.timezone.utc).isoformat()
                )
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

    return Response(content=xml_content, media_type="application/xml")


__all__ = ["router"]
