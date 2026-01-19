"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
"""

from __future__ import annotations

import os
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

from app.auth.deps import require_authentication
from app.auth.permissions import get_access_policies_from_claims
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

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
    """
    Aggressively normalize values for OData/JSON compatibility.

    OData clients are strict about type matching. We prioritize compatibility
    over precision, converting everything to basic JSON-compatible primitives.

    Returns: int, float, str, bool, or None only
    """
    import datetime as dt
    import decimal

    if value is None:
        return None

    # Debug logging for datetime values to diagnose timezone issues
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        logger.debug(
            f"Datetime value: {value}, type: {type(value)}, tzinfo: {getattr(value, 'tzinfo', 'N/A')}"
        )
    elif isinstance(value, str) and "T" in value:
        logger.debug(f"String with T (possible datetime): {value}")

    # Boolean - check first since bool is subclass of int in Python
    if isinstance(value, bool):
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

    # String that looks like a datetime without timezone -> add Z suffix
    if isinstance(value, str):
        # Check if it's an ISO datetime string without timezone
        # Formats: "1988-10-01T04:00:00" (needs Z), "1988-10-01T04:00:00Z" (OK), "1988-10-01T04:00:00+00:00" (OK)
        if "T" in value and len(value) >= 19:
            # Has time component
            if not (value.endswith("Z") or "+" in value[-6:] or "-" in value[-6:]):
                # No timezone indicator, add Z
                return value + "Z"
        return value

    # Binary data -> None (OData clients struggle with binary in JSON)
    if isinstance(value, (bytes, bytearray)):
        return None

    # Lists/arrays -> stringify for compatibility
    # (OData collections are complex and have poor client support)
    if isinstance(value, (list, tuple)):
        return str(value)

    # Dicts/structs -> stringify
    if isinstance(value, dict):
        return str(value)

    # Everything else -> convert to string as fallback
    # This handles UUIDs, enums, custom objects, etc.
    if not isinstance(value, str):
        return str(value)

    return value


def _map_py_type_to_edm(py_type: type) -> str:
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


def execute_odata_query(
    table_name: str,
    filter_expr: Optional[str] = None,
    orderby_expr: Optional[str] = None,
    select_cols: Optional[str] = None,
    top: Optional[int] = None,
    skip: Optional[int] = None,
    count_requested: bool = False,
) -> dict:
    """Execute an OData query and return results.

    This is the core logic extracted from read_dataset for easier unit testing.
    It doesn't require authentication, request objects, or response headers.

    Args:
        table_name: Full table name (e.g., "workstream.collection.dataset")
        filter_expr: OData $filter expression
        orderby_expr: OData $orderby expression
        select_cols: OData $select columns
        top: $top limit (1-10000, default: DEFAULT_TOP)
        skip: $skip offset
        count_requested: Whether to include total count

    Returns:
        dict with keys:
            - rows: list of dicts containing the result rows
            - col_names: list of column names
            - total_count: total count (if requested), None otherwise
            - took_ms: execution time in milliseconds

    Raises:
        ValueError: If OData query is invalid
        Exception: If execution fails
    """
    import time

    start_time = time.time()

    # Set defaults
    if top is None:
        top = DEFAULT_TOP

    # Build logical plan from OData parameters
    builder = ODataLogicalPlanBuilder(table_name)
    try:
        logical_plan = builder.build(
            filter_expr=filter_expr,
            select_cols=select_cols,
            orderby_expr=orderby_expr,
            top=top,
            skip=skip,
            apply_expr=None,
        )
    except ValueError as e:
        logger.error(
            "Failed to build logical plan for table %s: filter=%s, select=%s, orderby=%s, error=%s",
            table_name,
            filter_expr,
            select_cols,
            orderby_expr,
            str(e),
        )
        raise
    except Exception as e:
        logger.error(
            "Unexpected error building logical plan for table %s: error=%s",
            table_name,
            str(e),
            exc_info=e,
        )
        raise

    logger.debug(f"Built logical plan with {len(builder.plan.nodes())} nodes for {table_name}")

    # Execute the logical plan using Opteryx
    rows = []
    col_names = []

    try:
        arrow_table = opteryx.execute_logical_plan(logical_plan)

        # Get column names from the Arrow table
        col_names = arrow_table.column_names
        logger.debug(f"Arrow table columns: {col_names}")
        logger.debug(f"Arrow table num_rows: {arrow_table.num_rows}")

        # Convert Arrow table to list of dicts
        # Even if top=0, we still need to get the schema from the Arrow table
        columns_dict = arrow_table.to_pydict()

        rows = []
        for i in range(arrow_table.num_rows):
            row_dict = {}
            for col in col_names:
                value = columns_dict[col][i]
                # Sanitize values to ensure OData compatibility
                row_dict[col] = _sanitize_value_for_odata(value)
            rows.append(row_dict)

        logger.debug(f"Created {len(rows)} rows with {len(rows[0].keys()) if rows else 0} keys")

    except Exception as exc:
        logger.error(
            "Error executing logical plan for table %s with orderby=%s, filter=%s: %s",
            table_name,
            orderby_expr,
            filter_expr,
            str(exc),
            exc_info=exc,
        )
        raise

    # Get total count if requested
    total_count = None
    if count_requested:
        try:
            count_builder = ODataLogicalPlanBuilder(table_name)
            count_plan = count_builder.build(
                filter_expr=filter_expr,
                select_cols=None,
                orderby_expr=None,
                top=None,
                skip=None,
                apply_expr=None,
            )

            arrow_table = opteryx.execute_logical_plan(count_plan)
            total_count = arrow_table.num_rows
            logger.debug(f"Count query result: {total_count}")

        except Exception as exc:
            logger.error(
                "Error executing count query for table %s: %s",
                table_name,
                str(exc),
                exc_info=exc,
            )
            raise

    elapsed_ms = (time.time() - start_time) * 1000

    return {
        "rows": rows,
        "col_names": col_names,
        "total_count": total_count,
        "took_ms": elapsed_ms,
    }


def _map_py_type_to_edm_complete(py_type: type) -> str:
    """Map Python types to OData EDM types."""
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
            count_requested=want_count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except Exception as e:
        logger.error(f"Error executing OData query for {table_name}: {e}")
        raise

    rows = result["rows"]
    col_names = result["col_names"]
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
    return response


@router.get("/{workstream}/{collection}/{dataset}/$metadata")
def dataset_metadata(
    request: Request,
    response: Response,
    workstream: str,
    collection: str,
    dataset: str,
) -> Any:
    """Return a minimal OData $metadata (EDMX) for the given dataset.

    The endpoint inspects the dataset column metadata (without scanning rows)
    and emits an EDMX Schema describing an EntityType and EntitySet for the
    dataset. This is intentionally minimal — a full EDMX generator can be
    implemented later.

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    # Extract claims from request state
    claims = getattr(request.state, "claims", {})
    access_policies = get_access_policies_from_claims(claims)

    table_name = f"{workstream}.{collection}.{dataset}"
    sql = f'SELECT * FROM "{table_name}" LIMIT 1'

    try:
        # Pass access policies to metadata query
        arrow_table = opteryx.query_to_arrow(sql, access_policies=access_policies)
        col_names = arrow_table.column_names
    except Exception as exc:
        logger.error(f"failed to query dataset for metadata {table_name}")
        if exc.__class__.__name__ == "DatasetNotFoundError":
            raise HTTPException(status_code=404, detail="dataset not found")
        raise HTTPException(status_code=500, detail="failed to read dataset metadata")

    col_types = []
    try:
        # col_names already set from query result above
        # Default all to Edm.String for now; could infer better from row data
        for col_name in col_names:
            col_types.append("Edm.String")
    except Exception:
        # fallback: no column metadata
        col_names = []
        col_types = []

    # Build EDMX XML
    schema_ns = f"{collection}"
    entity_name = f"{collection}_{dataset}"
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">',
        "  <edmx:DataServices>",
        f'    <Schema Namespace="{schema_ns}" xmlns="http://docs.oasis-open.org/odata/ns/edm">',
        f'      <EntityType Name="{entity_name}">',
    ]

    # Add Key element (use first column as key)
    if col_names:
        lines.append("        <Key>")
        lines.append(f'          <PropertyRef Name="{col_names[0]}" />')
        lines.append("        </Key>")

    for name, typ in zip(col_names, col_types):
        lines.append(f'        <Property Name="{name}" Type="{typ}" Nullable="true" />')

    lines.extend(
        [
            "      </EntityType>",
            '      <EntityContainer Name="Container">',
            f'        <EntitySet Name="{dataset}" EntityType="{schema_ns}.{entity_name}" />',
            "      </EntityContainer>",
            "    </Schema>",
            "  </edmx:DataServices>",
            "</edmx:Edmx>",
        ]
    )

    from fastapi.responses import Response

    xml = "\n".join(lines)
    return Response(content=xml, media_type="application/xml")


__all__ = ["router"]
