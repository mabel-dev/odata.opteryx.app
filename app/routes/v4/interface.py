"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../opteryx-core")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../pyiceberg-firestore-gcs"))
)

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

# Some opteryx releases may not export OpteryxConnector (e.g. in lightweight installs).
# Provide a minimal stand-in so imports (and docs generation) still work.
try:
    from opteryx.connectors import OpteryxConnector
except ImportError:  # pragma: no cover
    OpteryxConnector = type("OpteryxConnector", (), {})

from opteryx_catalog import OpteryxCatalog
from app.logging import get_logger

from app.auth.deps import require_authentication
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.plan_optimizer import ODataLogicalPlanOptimizer

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
    import base64
    import json

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
    if not isinstance(value, str):
        return str(value)

    return value


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

    This function orchestrates the core OData query execution pipeline:
    1. Build logical plan from OData parameters (parsing + plan construction)
    2. Optimize the plan based on constraints (e.g., top=0 with count)
    3. Execute via Opteryx, handling optimization strategy

    Args:
        table_name: Full table name (e.g., "workstream.collection.dataset")
        filter_expr: OData $filter expression
        orderby_expr: OData $orderby expression
        select_cols: OData $select columns
        top: $top limit (1-10000, default: DEFAULT_TOP)
        skip: $skip offset
        count_requested: Whether to include total count
        apply_expr: OData $apply expression

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

    # Step 1: Set defaults
    if top is None:
        top = DEFAULT_TOP

    # Step 2: Build logical plan from OData parameters
    # This parses each OData expression ($filter, $orderby, etc.) and builds
    # a structured representation of the query
    plan_builder = ODataLogicalPlanBuilder(table_name)
    try:
        logical_plan = plan_builder.build(
            filter_expr=filter_expr,
            select_cols=select_cols,
            orderby_expr=orderby_expr,
            top=top,
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

    logger.debug(f"Built logical plan with {len(logical_plan.nodes())} nodes for {table_name}")

    # Debug: log validation inputs
    logger.debug(
        f"Validation inputs: apply_expr={apply_expr!r}, count_requested={count_requested!r}"
    )

    # Validate unsupported combinations
    if apply_expr and count_requested:
        # Historically these query combinations were considered invalid in our test-suite
        logger.error("Invalid query: $count with $apply is not supported")
        raise ValueError("$count with $apply is not supported")

    # Step 3: Optimize the plan based on query constraints
    # This makes strategic decisions about query execution (e.g., schema+count split)
    plan_optimizer = ODataLogicalPlanOptimizer()
    optimized_plan, strategy_info = plan_optimizer.optimize_with_constraints(
        logical_plan, top, count_requested
    )
    logger.debug(
        f"Applied optimization strategy: {strategy_info['strategy']} - {strategy_info['description']}"
    )

    # Step 4: Execute based on optimization strategy
    strategy = strategy_info.get("strategy")

    if strategy == "schema_count_split":
        # Special optimization: top=0 with count_requested
        # Fetch schema and count separately, no data rows
        rows = []
        col_names = []
        total_count = None

        # Get schema by running a query with top=1
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
            session = opteryx.session()
            schema_table = session.execute_logical_plan(schema_plan)
            col_names = schema_table.column_names or []
            logger.debug(f"Got schema from query: {col_names}")
        except Exception as exc:
            logger.error(f"Error getting schema for table {table_name}: {exc}")
            raise

        # Get total count
        try:
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
            session = opteryx.session()
            count_table = session.execute_logical_plan(count_plan)
            # Strict validation: count query MUST return a 1x1 numeric result. Fail fast with clear error otherwise.
            if not count_table:
                raise ValueError(f"Count query returned no table object for {table_name}")
            data = count_table.to_pydict()
            if not data:
                raise ValueError(f"Count query returned empty data for {table_name}")
            first_col = next(iter(data))
            vals = data.get(first_col)
            if vals is None or len(vals) == 0:
                raise ValueError(
                    f"Count query returned no values for column '{first_col}' for {table_name}"
                )
            try:
                total_count = int(vals[0])
            except Exception as e:
                # Include original data for debugging
                raise ValueError(
                    f"Count query returned non-integer value: {vals[0]} (col={first_col}) for {table_name}"
                ) from e

            logger.debug(f"Count query result: {total_count}")
        except Exception as exc:
            tb = traceback.format_exc()
            logger.error(f"Error executing count(*) query for table {table_name}: {exc}\n{tb}")
            raise
    else:
        # Standard path: execute the optimized logical plan
        rows = []
        col_names = []

        try:
            # Execute the logical plan using Opteryx executor
            session = opteryx.session()
            arrow_table = session.execute_logical_plan(optimized_plan)

            # Get column names from the Arrow table
            col_names = arrow_table.column_names or []
            logger.debug(f"Arrow table columns: {col_names}")
            logger.debug(f"Arrow table num_rows: {arrow_table.num_rows}")

            # Convert Arrow table to list of dicts
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
                f"Error executing logical plan for table {table_name} with orderby={orderby_expr}, filter={filter_expr}: {exc}"
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
                    apply_expr=apply_expr,
                    count_only=True,
                )
                session = opteryx.session()
                arrow_table = session.execute_logical_plan(count_plan)
                # Strict validation: count query MUST return a 1x1 numeric result. Fail fast with clear error otherwise.
                if not arrow_table:
                    raise ValueError(f"Count query returned no table object for {table_name}")
                data = arrow_table.to_pydict()
                if not data:
                    raise ValueError(f"Count query returned empty data for {table_name}")
                first_col = next(iter(data))
                vals = data.get(first_col)
                if vals is None or len(vals) == 0:
                    raise ValueError(
                        f"Count query returned no values for column '{first_col}' for {table_name}"
                    )
                try:
                    total_count = int(vals[0])
                except Exception as e:
                    raise ValueError(
                        f"Count query returned non-integer value: {vals[0]} (col={first_col}) for {table_name}"
                    ) from e
                logger.debug(f"Count(*) query result: {total_count}")

            except Exception as exc:
                logger.error(f"Error executing count(*) query for table {table_name}: {exc}")
                raise

    elapsed_ms = (time.time() - start_time) * 1000

    return {
        "rows": rows,
        "col_names": col_names,
        "total_count": total_count,
        "took_ms": elapsed_ms,
    }


# Base column-type category (as reported by the catalog's canonical type
# string, e.g. "INTEGER", "DECIMAL(15, 2)", "TIMESTAMP") -> OData EDM type.
_EDM_TYPE_BY_CATEGORY = {
    "INTEGER": "Edm.Int64",
    "FLOAT": "Edm.Double",
    "DOUBLE": "Edm.Double",
    "BOOLEAN": "Edm.Boolean",
    "VARCHAR": "Edm.String",
    "NVARCHAR": "Edm.String",
    "VARIANT": "Edm.String",
    "TIMESTAMP": "Edm.DateTimeOffset",
    "DATE": "Edm.Date",
    "TIME": "Edm.TimeOfDay",
    "DECIMAL": "Edm.Decimal",
    "VARBINARY": "Edm.Binary",
    "BLOB": "Edm.Binary",
}


def _column_type_to_edm(column_type: Any) -> str:
    """Convert an Opteryx column type to its OData EDM type.

    The catalog reports column types as canonical strings such as ``"INTEGER"``,
    ``"DECIMAL(15, 2)"`` or ``"ARRAY<VARCHAR>"``. Only the leading category name
    is significant for EDM mapping, so any parameter/element suffix is stripped
    before lookup. Enum-like inputs exposing a ``.name`` are also supported.

    Args:
        column_type: Canonical type string (or object with ``.name``).

    Returns:
        OData EDM type string; ``"Edm.String"`` for anything unrecognised.
    """
    if column_type is None:
        return "Edm.String"

    name = getattr(column_type, "name", None) or str(column_type)
    # Strip parameters/element suffixes: "DECIMAL(15, 2)" -> "DECIMAL",
    # "ARRAY<VARCHAR>" -> "ARRAY".
    category = name.strip().upper().split("(", 1)[0].split("<", 1)[0].strip()

    return _EDM_TYPE_BY_CATEGORY.get(category, "Edm.String")


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
                # Map the catalog column type to its OData EDM type.
                edm_type = _column_type_to_edm(field.type)
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
