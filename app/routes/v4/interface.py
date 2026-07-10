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
import xml.etree.ElementTree as ET
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
from app.odata.edm_types import column_type_to_edm

logger = get_logger()

FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")
DEFAULT_TOP = int(os.environ.get("ODATA_DEFAULT_TOP", "100"))
MAX_TOP = int(os.environ.get("ODATA_MAX_TOP", "25000"))

opteryx.set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
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


def _materialize_logical_plan_result(session, logical_plan) -> _MaterializedResult:
    """Execute a logical plan on a session and materialize the result as a table-alike.

    ``Session.execute_logical_plan`` returns a ``(results_generator, ResultType)`` tuple
    rather than a materialized table (opteryx-core commit 6094635a). TABULAR results
    are streamed as Draken Morsels; concatenate them into a single in-memory result so
    the rest of this module can keep working with the table-shaped API it expects.
    """
    from opteryx.constants import ResultType

    results_generator, result_type = session.execute_logical_plan(logical_plan, connection=session)

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

    # Strings: return as-is, no heuristic mutation
    if isinstance(value, str):
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
    identity: Optional[str] = None,
    access_policies: Optional[List[dict]] = None,
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
        top: $top limit (1-MAX_TOP, default: DEFAULT_TOP)
        skip: $skip offset
        count_requested: Whether to include total count
        apply_expr: OData $apply expression

    Returns:
        dict with keys:
            - rows: list of dicts containing the result rows
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
        top = DEFAULT_TOP

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

    logger.debug(f"Built logical plan with {len(logical_plan.nodes())} nodes for {table_name}")

    # Build a session carrying the caller's identity/access policies so that
    # Opteryx's permission checks are evaluated against the actual caller,
    # rather than falling back to an empty (deny-all) execution context.
    session = opteryx.session(user=identity, access_policies=access_policies or None)

    # Debug: log validation inputs
    logger.debug(
        f"Validation inputs: apply_expr={apply_expr!r}, count_requested={count_requested!r}"
    )

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
        has_more_rows = False  # No data rows returned, so never truncated

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
            schema_table = _materialize_logical_plan_result(session, schema_plan)
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
            count_table = _materialize_logical_plan_result(session, count_plan)
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
        has_more_rows = False

        try:
            # Execute the logical plan using Opteryx executor
            arrow_table = _materialize_logical_plan_result(session, optimized_plan)

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

            # Detect truncation: if we fetched top+1 and got that many rows,
            # we know there are more rows. Trim to top and set has_more_rows flag.
            if fetch_top > top and len(rows) > top:
                has_more_rows = True
                rows = rows[:top]
                logger.debug(f"Truncated result set to {top} rows; more rows exist")

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
                arrow_table = _materialize_logical_plan_result(session, count_plan)
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
        "has_more_rows": has_more_rows,
        "took_ms": elapsed_ms,
    }




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


@router.get(
    "/{workstream}/{collection}/{dataset}",
    summary="Query dataset rows",
    description="Retrieve data from a dataset with OData v4 query parameters ($filter, $select, $orderby, $top, $skip, $apply, $count). Returns paginated results with total count and nextLink for server-driven paging.",
    responses={
        200: {"description": "Query succeeded; returns rows and pagination metadata"},
        400: {"description": f"Invalid query: malformed $filter, unsupported $top value (not 0-{MAX_TOP}), invalid $count value, or invalid $apply expression"},
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
        description="OData $filter expression for row filtering. Operators: eq (equal), ne (not equal), lt/le/gt/ge (comparison), and/or/not (logical), contains/startswith/endswith (string). Case-sensitive. Example: vendor eq 'Oracle' and price gt 100"
    ),
    top: Optional[int] = Query(
        None,
        alias="$top",
        description=f"Limit result rows (0-{MAX_TOP}, default {DEFAULT_TOP}). Value 0 with $count=true returns count only. Returns @odata.nextLink if result is truncated."
    ),
    skip: Optional[int] = Query(
        None,
        alias="$skip",
        description="Skip N rows for pagination (server-driven). Example: &$skip=100 to fetch rows 101+. Combine with $top for paging."
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
        description="Data aggregation: groupby((col), aggregate(sum(amount) as Total, $count as Count)). Supports min, max, average, sum, countdistinct."
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
    - GET /ws/coll/data?$apply=groupby((vendor),aggregate(sum(amount) as Total))
    - GET /ws/coll/data?$count=true&$top=0 - get row count only

    Requires authentication via bearer token or basic auth.
    """
    # Set OData-Version header
    response.headers["OData-Version"] = "4.0"

    table_name = f"{workstream}.{collection}.{dataset}"

    # Parse and validate $top parameter
    # Detect duplicate OData query parameters in the raw query string and disallow them
    from urllib.parse import parse_qs

    parsed_qs = parse_qs(request.url.query)

    # Reject unsupported query options
    if search and search.strip():
        raise HTTPException(
            status_code=501,
            detail="$search is not supported by this service"
        )

    if "$expand" in parsed_qs and parsed_qs["$expand"]:
        expand_value = parsed_qs.get("$expand", [None])[0]
        raise HTTPException(
            status_code=501,
            detail=f"$expand (navigation property expansion) is not supported by this service"
        )

    # Parameters for which duplicates are disallowed
    duplicate_sensitive = ["$top", "$apply", "$count", "$select", "$orderby", "$filter", "$skip"]
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

    # Validate $top range (allow 0 unconditionally per OData spec)
    if not (0 <= top <= MAX_TOP):
        raise HTTPException(
            status_code=400,
            detail=f"$top must be between 0 and {MAX_TOP}",
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
    from app.auth.permissions import get_access_policies_from_claims, is_anonymous_exempt

    claims = getattr(request.state, "claims", None) or {}
    identity = claims.get("identity")
    access_policies = get_access_policies_from_claims(claims)

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
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except opteryx.exceptions.PermissionsError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except (opteryx.exceptions.SqlError, opteryx.exceptions.IncompatibleTypesError) as e:
        # "The query as given is invalid" — client-fixable (bad/uncast filter
        # literal, unknown column, unsupported syntax, etc.), not a server fault.
        # IncompatibleTypesError is listed separately because it doesn't actually
        # inherit from SqlError despite opteryx-core's own docs implying it does.
        raise HTTPException(status_code=400, detail=f"Invalid query: {str(e)}")
    except Exception as e:
        logger.error(f"Error executing OData query for {table_name}: {e}")
        raise

    rows = result["rows"] or []
    col_names = result["col_names"] or []
    total_count = result["total_count"]
    has_more_rows = result.get("has_more_rows", False)

    # All paging (LIMIT/OFFSET) should have been pushed down to SQL when
    # possible; no Python-side slicing is necessary.

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
                        edm_type = column_type_to_edm(type(v))
                        break

            if edm_type is None:
                edm_type = "Edm.String"

            columns.append({"name": name, "type": edm_type})

    # Return OData-compliant response with required @odata.context annotation
    # The fragment must match the EntitySet name declared in the metadata
    # Per OData spec, top-level resource set can only have: @odata.context, value, @odata.count, @odata.nextLink
    enriched_rows = rows

    # Use underscore form for context URI to match EntitySet name in metadata (CSDL SimpleIdentifier compliance)
    full_resource_fragment = f"{workstream}_{collection}_{dataset}"
    response = {
        "@odata.context": f"/api/v4/$metadata#{full_resource_fragment}",
        "value": enriched_rows,
    }
    if total_count is not None:
        response["@odata.count"] = total_count

    # Build @odata.nextLink if result was truncated
    if has_more_rows:
        from urllib.parse import parse_qs, urlencode, urlparse

        # Parse the current query string to build the next link
        parsed_url = urlparse(str(request.url))
        query_params = parse_qs(parsed_url.query, keep_blank_values=True)

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
        response["@odata.nextLink"] = f"/api/v4/{workstream}/{collection}/{dataset}?{next_query}"
        logger.debug(f"Added @odata.nextLink: {response['@odata.nextLink']}")

    logger.debug(
        f"OData response: {len(enriched_rows)} rows, total_count={total_count}, col_names={col_names}, has_more_rows={has_more_rows}"
    )
    return response


@router.get(
    "/{workstream}/{collection}/{dataset}/$metadata",
    summary="Per-dataset OData EDMX metadata",
    description="Returns OData $metadata (EDMX) for a single dataset, including column types, nullability, cardinality, and min/max values as custom annotations.",
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

    Also includes access-control annotations:
    - Custom.Role: user's assigned role (reader, writer, admin, owner)
    - Custom.Policy: policy that granted access

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
                # Map the catalog column type to its OData EDM type (unified mapper).
                edm_type = column_type_to_edm(field.type)
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
        edm_type = meta.get("type", "Edm.String")

        # First property is key, must be non-nullable
        is_key = col_name == col_names[0]
        prop_attrib = {"Name": col_name, "Type": edm_type}
        if not is_key:
            prop_attrib["Nullable"] = "true"
        else:
            prop_attrib["Nullable"] = "false"

        prop = ET.SubElement(entity_type, f"{{{NS_EDM}}}Property", attrib=prop_attrib)

        # Add annotations if we have statistics
        if "min" in meta and meta["min"] is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.Min", "String": str(meta["min"])},
            )

        # Emit original source type when we mapped to a different EDM type
        if "original_type" in meta and meta["original_type"]:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.OriginalType", "String": str(meta["original_type"])},
            )

        if "max" in meta and meta["max"] is not None:
            ET.SubElement(
                prop,
                f"{{{NS_EDM}}}Annotation",
                attrib={"Term": "Custom.Statistics.Max", "String": str(meta["max"])},
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

    # Add source type annotation
    ET.SubElement(
        entity_set,
        f"{{{NS_EDM}}}Annotation",
        attrib={"Term": "Custom.SourceType", "String": source_type},
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
            logger.debug("Failed to extract view metadata for annotations", exc_info=True)

    # If this is a table, add snapshot and table-level metadata annotations
    if source_type == "Table":
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
    return Response(content=xml, media_type="application/xml")


__all__ = ["router"]
