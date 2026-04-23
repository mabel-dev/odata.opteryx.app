"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
"""

from __future__ import annotations

import os
import traceback
from typing import Any
from typing import List
from typing import Optional

import opteryx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog
from orso.logging import get_logger
from orso.types import OrsoTypes

from app.auth.deps import require_authentication
from opteryx.odata import execute_odata_query as _execute_odata_query_core
from opteryx.odata import sanitize_value_for_odata as _sanitize_value_for_odata_core
from opteryx.odata import ODataLogicalPlanBuilder
from opteryx.odata import ODataLogicalPlanOptimizer

logger = get_logger()
logger.setLevel(5)

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
DEFAULT_TOP = int(os.environ.get("ODATA_DEFAULT_TOP", "100"))

opteryx.set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)


def _sanitize_value_for_odata(value: Any) -> Any:
    """Normalize values for OData/JSON compatibility using the shared core helper."""
    return _sanitize_value_for_odata_core(value)


def execute_odata_query(
    table_name: str,
    filter_expr: Optional[str] = None,
    orderby_expr: Optional[str] = None,
    select_cols: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    apply_expr: Optional[str] = None,
    count_requested: bool = False,
) -> dict:
    """Execute an OData query and return results.

    This wrapper delegates the core execution logic to opteryx-core.
    """
    return _execute_odata_query_core(
        table_name=table_name,
        filter_expr=filter_expr,
        orderby_expr=orderby_expr,
        select_cols=select_cols,
        top=top,
        skip=skip,
        apply_expr=apply_expr,
        count_requested=count_requested,
        plan_builder_cls=ODataLogicalPlanBuilder,
        plan_optimizer_cls=ODataLogicalPlanOptimizer,
    )


def _orso_type_to_edm(orso_type: OrsoTypes) -> str:
    """Convert Orso/Opteryx type to OData EDM type.

    Args:
        orso_type: Orso/Opteryx DataType

    Returns:
        OData EDM type string
    """

    # Handle Orso/Opteryx type strings
    if orso_type == OrsoTypes.INTEGER:
        return "Edm.Int64"
    if orso_type == OrsoTypes.DOUBLE:
        return "Edm.Double"
    if orso_type == OrsoTypes.BOOLEAN:
        return "Edm.Boolean"
    if orso_type == OrsoTypes.VARCHAR:
        return "Edm.String"
    if orso_type == OrsoTypes.TIMESTAMP:
        return "Edm.DateTimeOffset"
    if orso_type == OrsoTypes.DATE:
        return "Edm.Date"
    if orso_type == OrsoTypes.TIME:
        return "Edm.TimeOfDay"
    if orso_type == OrsoTypes.DECIMAL:
        return "Edm.Decimal"
    if orso_type == OrsoTypes.BLOB:
        return "Edm.Binary"

    # Fallback for unknown types
    return "Edm.String"


def _role_allows_read(role: str | None) -> bool:
    """Check if a given role string allows read access."""
    if not role:
        return False
    return role.lower() in {"reader", "writer", "admin", "owner"}


router = APIRouter(
    prefix="/api/v4",
    tags=["OData v4"],
    dependencies=[Depends(require_authentication)],
)


@router.get("/{workstream}/{collection}/{dataset}")
def read_dataset(
    request: Request,
    response: Response,
    workstream: str,
    collection: str,
    dataset: str,
    filter: Optional[str] = Query(None, alias="$filter"),
    top: Optional[int] = Query(None, alias="$top"),
    skip: Optional[int] = Query(None, alias="$skip"),
    orderby: Optional[str] = Query(None, alias="$orderby"),
    count: Optional[str] = Query(None, alias="$count"),
    select: Optional[str] = Query(None, alias="$select"),
    search: Optional[str] = Query(None, alias="$search"),
    apply: Optional[str] = Query(None, alias="$apply"),
) -> Any:
    """Read rows from a specified dataset.

    This implementation exposes OData-style query string names (e.g. `$filter`).
    For performance and safety we require a `$top` limit to be provided and it
    must be in the range 1..10000.

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    table_name = f"{workstream}.{collection}.{dataset}"

    # Parse and validate $top parameter
    # Detect duplicate OData query parameters in the raw query string and disallow them
    from urllib.parse import parse_qs

    parsed_qs = parse_qs(request.url.query)
    # Parameters for which duplicates are disallowed
    duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby"]
    duplicates = [
        p for p in duplicate_sensitive if p in parsed_qs and len(parsed_qs.get(p, [])) > 1
    ]
    if duplicates:
        raise HTTPException(
            status_code=400, detail=f"Multiple parameters not allowed: {', '.join(duplicates)}"
        )

    if top is None:
        top = DEFAULT_TOP
    else:
        top = int(top)  # Will raise ValueError if not a valid integer

    # Validate $top range
    if count and count.lower() in ("true", "1", "yes"):
        if not (0 <= top <= 10000):
            raise HTTPException(
                status_code=400,
                detail="$top must be between 0 and 10000 (0 allowed with $count=true)",
            )
    else:
        if not (1 <= top <= 10000):
            raise HTTPException(status_code=400, detail="$top must be between 1 and 10000")

    # Parse count parameter
    want_count = False
    if count is not None:
        try:
            if isinstance(count, str):
                want_count = count.lower() in ("true", "1", "yes")
            else:
                want_count = bool(count)
        except Exception:
            want_count = False

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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except Exception as e:
        logger.error(f"Error executing OData query for {table_name}: {e}")
        raise

    rows = result["rows"] or []
    col_names = result["col_names"] or []
    total_count = result["total_count"]

    # All paging (LIMIT/OFFSET) should have been pushed down to SQL when
    # possible; no Python-side slicing is necessary.

    def _map_py_type_to_edm(py_type: type) -> str:
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

    # Build column metadata (name + EDM type). Prefer explicit column metadata
    # when available; otherwise infer from the first non-null row value.
    columns: List[dict] = []
    if col_names:
        for idx, name in enumerate(col_names):
            edm_type = None
            # Infer type from first non-null value in rows
            if edm_type is None:
                for r in rows:
                    if isinstance(r, dict) and name in r:
                        v = r[name]
                    elif isinstance(r, (list, tuple)):
                        v = r[idx] if idx < len(r) else None
                    else:
                        v = None
                    if v is not None:
                        edm_type = _map_py_type_to_edm(type(v))
                        break

            if edm_type is None:
                edm_type = "Edm.String"

            columns.append({"name": name, "type": edm_type})

    # Return OData-compliant response with required @odata.context annotation
    # The fragment must match the EntitySet name declared in the metadata
    # Per OData spec, top-level resource set can only have: @odata.context, value, @odata.count, @odata.nextLink
    # Add @odata.id, @odata.etag, @odata.editLink to each entity
    enriched_rows = []
    for row in rows:
        # Use the first key column value to build @odata.id
        key_value = None
        if col_names:
            key_col = col_names[0]
            key_value = row.get(key_col)

        # Build entity annotations
        if key_value is not None:
            entity = {
                "@odata.id": f"/api/v1/{workstream}/{collection}/{dataset}({key_value})",
                "@odata.etag": 'W/"1"',
                "@odata.editLink": f"/api/v1/{workstream}/{collection}/{dataset}({key_value})",
            }
            entity.update(row)
            enriched_rows.append(entity)
        else:
            enriched_rows.append(row)

    # Use full resource path for context URI to match EntitySet name in metadata
    full_resource = f"{workstream}.{collection}.{dataset}"
    response = {
        "@odata.context": f"/api/v4/$metadata#{full_resource}",
        "value": enriched_rows,
    }
    if total_count is not None:
        response["@odata.count"] = total_count

    logger.debug(
        f"OData response: {len(enriched_rows)} rows, total_count={total_count}, col_names={col_names}"
    )
    return response


@router.get("/{workstream}/{collection}/{dataset}/$metadata")
def dataset_metadata(
    request: Request,
    response: Response,
    workstream: str,
    collection: str,
    dataset: str,
) -> Any:
    """Return OData $metadata (EDMX) for the given dataset with enriched column information.

    Queries the catalog to get actual column types, cardinality, min/max values,
    and distribution information, then emits an EDMX Schema with annotations.

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    table_name = f"{workstream}.{collection}.{dataset}"

    # Authorization: determine whether the authenticated user may view this dataset's metadata
    # Get claims provided by the authentication dependency
    claims = getattr(request.state, "claims", {}) or {}
    identity = claims.get("identity")

    from app.auth.permissions import get_access_policies_from_claims

    # Default: no access
    allowed = False
    assigned_role = None
    assigned_policy = None

    # Build map: workspace -> [(role, policy), ...]
    access_policies_list = get_access_policies_from_claims(claims)
    workspace_policies: dict[str, list[tuple[str, str]]] = {}
    for p in access_policies_list:
        role = p.get("role")
        pattern = p.get("pattern")
        policy = p.get("policy", "none")
        if not role or not pattern:
            continue
        workspace = pattern.split(".")[0] if "." in pattern else pattern
        workspace_policies.setdefault(workspace, []).append((role, policy))

    # Personal workspace handling
    if workstream == "personal":
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

    # Workspace-level policies
    if not allowed:
        policies = workspace_policies.get(workstream, [])
        for role, policy in policies:
            # Local helper checks for read-capable roles
            def _role_allows_read(r: str | None) -> bool:
                if not r:
                    return False
                return r.lower() in {"reader", "writer", "admin", "owner"}

            if _role_allows_read(role):
                allowed = True
                assigned_role = role
                assigned_policy = policy
                break

    if not allowed:
        raise HTTPException(status_code=403, detail="forbidden: not allowed to view metadata")

    # Try to get enriched metadata from catalog
    col_names = []
    col_types = []
    col_metadata = {}  # Maps col_name -> {type, min, max, cardinality, description}

    try:
        workspace = OpteryxCatalog(
            workstream,
            firestore_project=GCP_PROJECT_ID,
            firestore_database=FIRESTORE_DATABASE,
            gcs_bucket=BUCKET_NAME,
        )

        # Try to load as a dataset first, then as a view
        ds = None
        source_type = "Table"
        try:
            ds = workspace.load_dataset(f"{collection}.{dataset}")
            source_type = "Table"
        except Exception:
            try:
                ds = workspace.load_view(f"{collection}.{dataset}")
                source_type = "View"
            except Exception as exc:
                logger.error(f"Failed to load dataset/view from catalog for {table_name}: {exc}")
                raise HTTPException(status_code=404, detail="dataset not found in catalog") from exc

        # Set source header for clients
        response.headers["X-Dataset-Source"] = source_type

        # Get schema for column types and precision info
        schema = None
        try:
            if hasattr(ds, "schema"):
                schema = ds.schema()
        except Exception:
            schema = None

        logger.debug(f"Schema from catalog: {schema}")

        if schema and hasattr(schema, "columns"):
            for field in schema.columns:
                col_name = field.name
                col_names.append(col_name)
                # Map Orso type to EDM type (fallback may be generic)
                edm_type = _orso_type_to_edm(field.type)
                col_types.append(edm_type)
                col_metadata[col_name] = {
                    "type": edm_type,
                    "source_type": str(field.type),
                    "nullable": field.nullable,
                }

            # Post-process column `source_type` to refine types for arrays/structs/binary
            try:
                import re

                for col_name, meta in col_metadata.items():
                    st = str(meta.get("source_type", "")).upper()
                    # ARRAY -> Collection(Edm.<inner>) when inner exists
                    if "ARRAY" in st:
                        inner = None
                        m = re.search(r"ARRAY\s*[<\(]\s*([^>\)]+)\s*[>\)]", st)
                        if m:
                            inner = m.group(1).strip().upper()
                        if inner in ("INTEGER", "INT", "BIGINT", "TINYINT", "SMALLINT"):
                            meta["type"] = "Collection(Edm.Int64)"
                        elif inner in ("DOUBLE", "FLOAT", "REAL", "DECIMAL", "NUMERIC"):
                            meta["type"] = "Collection(Edm.Double)"
                        else:
                            meta["type"] = "Collection(Edm.String)"
                        meta["original_type"] = st

                    # STRUCT/JSON -> expose as binary and annotate original type
                    if any(x in st for x in ("STRUCT", "JSON", "JSONB")):
                        meta["type"] = "Edm.Binary"
                        meta["original_type"] = st

                    # Blob/varbinary/binary -> binary EDM type
                    if any(x in st for x in ("BLOB", "VARBINARY", "BINARY")):
                        meta["type"] = "Edm.Binary"
                        meta["original_type"] = st
            except Exception:
                # Be conservative: do not fail if introspection fails
                logger.debug("Failed to post-process column types", exc_info=True)
        else:
            # If this is a view and the catalog object does not provide a schema,
            # emit a minimal placeholder so the metadata document remains valid.
            if source_type == "View":
                # Single string key placeholder
                col_names = ["id"]
                col_metadata = {"id": {"type": "Edm.String", "nullable": True}}
            else:
                # For tables, lack of schema indicates invalid catalog state
                raise HTTPException(status_code=404, detail="dataset schema unavailable in catalog")

        # Get statistics from description (min/max, cardinality, distribution)
        description = None
        try:
            if hasattr(ds, "describe"):
                description = ds.describe()
        except Exception:
            description = None

        logger.debug(
            f"Description keys: {list(description.keys()) if isinstance(description, dict) else 'not a dict'}"
        )
        if description and isinstance(description, dict):
            for col_name, stats in description.items():
                if col_name in col_metadata and isinstance(stats, dict):
                    # Add stats to metadata and normalize naming
                    meta = col_metadata[col_name]
                    meta.update(stats)

        # Get snapshot for last modified info only for tables (views typically don't expose snapshot)
        last_ts_ms = None
        snapshot_info = {}
        try:
            snapshot = None
            if (
                source_type == "Table"
                and hasattr(ds, "snapshot")
                and callable(getattr(ds, "snapshot"))
            ):
                snapshot = ds.snapshot()
            logger.debug(f"Snapshot: {snapshot}")
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
        except Exception:
            # Be defensive: missing or failing snapshot should not break metadata
            logger.debug(
                "Snapshot unavailable or raised when retrieving last-modified", exc_info=True
            )

        logger.debug(
            f"Loaded enriched metadata for {table_name} from catalog with {len(col_names)} columns; source={source_type}"
        )
    except HTTPException:
        # re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        tb_str = traceback.format_exc()
        logger.error(f"Failed to load metadata from catalog: {e}\n{tb_str}")
        raise

    # Build EDMX XML with annotations
    schema_ns = f"{collection}"
    entity_name = f"{collection}_{dataset}"
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">',
        "  <edmx:DataServices>",
        f'    <Schema Namespace="{schema_ns}" xmlns="http://docs.oasis-open.org/odata/ns/edm"',
        '           xmlns:sap="http://www.sap.com/Protocols/SAPData">',
        f'      <EntityType Name="{entity_name}">',
    ]

    # Add Key element (use first column as key)
    if col_names:
        lines.append("        <Key>")
        lines.append(f'          <PropertyRef Name="{col_names[0]}" />')
        lines.append("        </Key>")

    # Add Property elements with annotations for statistics
    for col_name in col_names:
        meta = col_metadata.get(col_name, {})
        edm_type = meta.get("type", "Edm.String")

        lines.append(f'        <Property Name="{col_name}" Type="{edm_type}" Nullable="true">')

        # Add annotations if we have statistics
        if "min" in meta and meta["min"] is not None:
            min_val = (
                str(meta["min"])
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            lines.append(f'          <Annotation Term="Custom.Statistics.Min" String="{min_val}"/>')

        # Emit original source type when we mapped to a different EDM type
        if "original_type" in meta and meta["original_type"]:
            ot = (
                str(meta["original_type"])
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            lines.append(f'          <Annotation Term="Custom.OriginalType" String="{ot}"/>')
        if "max" in meta and meta["max"] is not None:
            max_val = (
                str(meta["max"])
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
            )
            lines.append(f'          <Annotation Term="Custom.Statistics.Max" String="{max_val}"/>')
        if "cardinality" in meta and meta["cardinality"] is not None and meta["cardinality"] > 0:
            cardinality_key = "DistinctValueCount"
            cardinality_value = meta["cardinality"]
            is_estimate = not bool(meta.get("cardinality_is_exact", False))

            # Cap cardinality estimate at total record count (can't have more unique values than rows)
            if is_estimate and "total_records" in snapshot_info:
                cardinality_value = min(cardinality_value, snapshot_info["total_records"])

            if is_estimate:
                cardinality_key += "Estimate"
            lines.append(
                f'          <Annotation Term="Custom.Statistics.{cardinality_key}" Int="{cardinality_value}"/>'
            )
        if "null_count" in meta and meta["null_count"] is not None:
            lines.append(
                f'          <Annotation Term="Custom.Statistics.NullCount" Int="{meta["null_count"]}"/>'
            )
        if "distribution" in meta and meta["distribution"] and any(meta["distribution"]):
            # Distribution as a Collection of Int values instead of string
            # Skip if all values are zero (no meaningful distribution data)
            lines.append('          <Annotation Term="Custom.Statistics.Distribution">')
            lines.append("            <Collection>")
            for val in meta["distribution"]:
                lines.append(f"              <Int>{val}</Int>")
            lines.append("            </Collection>")
            lines.append("          </Annotation>")

        lines.append("        </Property>")

    lines.extend(
        [
            "      </EntityType>",
            '      <EntityContainer Name="Container">',
            f'        <EntitySet Name="{dataset}" EntityType="{schema_ns}.{entity_name}">',
            f'          <Annotation Term="Custom.SourceType" String="{source_type}"/>',
        ]
    )

    # Add access annotations (role, policy) when available
    if assigned_role:
        lines.append(f'          <Annotation Term="Custom.Role" String="{assigned_role}"/>')
    if assigned_policy:
        lines.append(f'          <Annotation Term="Custom.Policy" String="{assigned_policy}"/>')

    # If this is a view, try to enrich EntitySet with view metadata (definition, author, description, LLM flag)
    if source_type == "View":
        try:
            definition = getattr(ds, "sql", None) or getattr(ds, "definition", None)
            if definition:
                def_val = (
                    str(definition)
                    .replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;")
                )
                lines.append(f'          <Annotation Term="Custom.Definition" String="{def_val}"/>')

            meta = getattr(ds, "metadata", None)
            if meta is not None:
                author = getattr(meta, "author", None)
                if author:
                    lines.append(
                        f'          <Annotation Term="Custom.Author" String="{str(author)}"/>'
                    )

                description = getattr(meta, "description", None)
                if description:
                    desc_val = (
                        str(description)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace('"', "&quot;")
                    )
                    lines.append(
                        f'          <Annotation Term="Custom.Description" String="{desc_val}"/>'
                    )

                describer = getattr(meta, "describer", None)
                llm_flag = isinstance(describer, str) and (
                    describer.startswith("Gemini:") or describer.startswith("Llama-")
                )
                lines.append(
                    f'          <Annotation Term="Custom.LLMDescribed" Bool="{str(llm_flag).lower()}"/>'
                )

                # If metadata timestamp present and header not set, set last modified header
                meta_ts = getattr(meta, "timestamp_ms", None)
                if meta_ts:
                    if not response.headers.get("X-Dataset-LastModified"):
                        response.headers["X-Dataset-LastModified"] = str(meta_ts)
                    last_ts_ms = meta_ts
        except Exception:
            logger.debug("Failed to extract view metadata for annotations", exc_info=True)

    # If this is a table, add snapshot and table-level metadata annotations
    if source_type == "Table":
        try:
            # Snapshot annotations
            if snapshot_info:
                sid = snapshot_info.get("snapshot_id")
                if sid:
                    lines.append(
                        f'          <Annotation Term="Custom.Snapshot.Id" String="{sid}"/>'
                    )
                if "total_records" in snapshot_info:
                    lines.append(
                        f'          <Annotation Term="Custom.Snapshot.TotalRecords" Int="{snapshot_info["total_records"]}"/>'
                    )
                if "total_data_size" in snapshot_info:
                    lines.append(
                        f'          <Annotation Term="Custom.Snapshot.TotalDataSize" Int="{snapshot_info["total_data_size"]}"/>'
                    )

            # Table metadata
            meta = getattr(ds, "metadata", None)
            if meta is not None:
                descr = getattr(meta, "description", None)
                if descr:
                    desc_val = (
                        str(descr)
                        .replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace('"', "&quot;")
                    )
                    lines.append(
                        f'          <Annotation Term="Custom.Description" String="{desc_val}"/>'
                    )
                describer = getattr(meta, "describer", None)
                llm_flag = isinstance(describer, str) and (
                    describer.startswith("Gemini:") or describer.startswith("Llama-")
                )
                lines.append(
                    f'          <Annotation Term="Custom.LLMDescribed" Bool="{str(llm_flag).lower()}"/>'
                )
        except Exception:
            logger.debug("Failed to extract table metadata for annotations", exc_info=True)

    # Add LastModified annotation if we have a timestamp
    if last_ts_ms:
        try:
            import datetime as _dt

            iso = (
                _dt.datetime.utcfromtimestamp(last_ts_ms / 1000.0)
                .replace(tzinfo=_dt.timezone.utc)
                .isoformat()
            )
            lines.append(f'          <Annotation Term="Custom.LastModified" String="{iso}"/>')
        except Exception:
            pass

    lines.extend(
        [
            "        </EntitySet>",
            "      </EntityContainer>",
            "    </Schema>",
            "  </edmx:DataServices>",
            "</edmx:Edmx>",
        ]
    )

    xml = "\n".join(lines)
    return Response(content=xml, media_type="application/xml")


__all__ = ["router"]
