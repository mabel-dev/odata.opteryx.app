"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
"""

from __future__ import annotations

import os
import sys
from typing import Any
from typing import List
from typing import Optional

import opteryx
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from opteryx.connectors import OpteryxConnector
from opteryx_catalog import OpteryxCatalog
from orso.logging import get_logger

from app.models.query import Query as SQLQuery

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../opteryx-core"))
)
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../pyiceberg-firestore-gcs")),
)


logger = get_logger()


FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

opteryx.set_default_connector(
    OpteryxConnector,
    catalog=OpteryxCatalog,
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)


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


router = APIRouter(
    prefix="/api/v1",
    tags=["OData"],  # , dependencies=[Depends(require_bearer_token)]
)


@router.get("/{workstream}/{collection}/{dataset}")
def read_dataset(
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
    """

    table_name = f"{workstream}.{collection}.{dataset}"
    # Apply a safe default $top when clients don't provide one to avoid
    # returning a 400 for discovery clients (Excel). Default can be
    # overridden via the ODATA_DEFAULT_TOP env var.
    DEFAULT_TOP = int(os.environ.get("ODATA_DEFAULT_TOP", "100"))
    try:
        if top is None:
            top_val = DEFAULT_TOP
        else:
            top_val = int(top)
    except Exception:
        raise HTTPException(status_code=400, detail="$top must be an integer between 1 and 10000")
    if top_val <= 0 or top_val > 10000:
        raise HTTPException(status_code=400, detail="$top must be between 1 and 10000")
    top = top_val
    qb = SQLQuery()
    qb.SELECT("*")
    qb.FROM(f'"{table_name}"')

    # Prefer doing paging in SQL. When $top is provided use LIMIT (and OFFSET
    # when present). When only $skip is provided append an `OFFSET` clause to
    # the SQL so the engine can avoid materializing skipped rows.
    used_sql_paging = False
    append_offset = False
    if top is not None:
        if skip is not None:
            qb.LIMIT(f"{top} OFFSET {skip}")
        else:
            qb.LIMIT(str(top))
        used_sql_paging = True
    elif skip is not None:
        # No top provided, but we have a skip — we'll append OFFSET to the SQL
        # string below so the engine can apply it.
        append_offset = True
        used_sql_paging = True

    sql = str(qb)
    if append_offset:
        sql = sql + f" OFFSET {skip}"

    try:
        # Use query_to_arrow() which returns a PyArrow table
        arrow_table = opteryx.query_to_arrow(sql)

        # Get column names from the Arrow table
        col_names = arrow_table.column_names
        logger.info(f"Arrow table columns: {col_names}")
        logger.info(f"Arrow table num_rows: {arrow_table.num_rows}")

        # Convert Arrow table to list of dicts
        # Use to_pydict() which gives {column: [values]}, then transpose to rows
        columns_dict = arrow_table.to_pydict()
        logger.info(
            f"First row sample: {[(k, columns_dict[k][0] if columns_dict[k] else None) for k in list(col_names)[:5]]}"
        )

        rows = []
        for i in range(arrow_table.num_rows):
            row_dict = {col: columns_dict[col][i] for col in col_names}
            rows.append(row_dict)

        logger.info(f"Created {len(rows)} rows with {len(rows[0].keys()) if rows else 0} keys")

    except Exception as exc:
        logger.exception(f"failed to query dataset {table_name}")
        exc_name = exc.__class__.__name__
        if exc_name == "DatasetNotFoundError":
            raise HTTPException(status_code=404, detail="dataset not found")
        raise HTTPException(status_code=500, detail="failed to read dataset")

    # If the client requested total count, run a separate COUNT(*) query
    want_count = False
    if count is not None:
        try:
            if isinstance(count, str):
                want_count = count.lower() in ("true", "1", "yes")
            else:
                want_count = bool(count)
        except Exception:
            want_count = False

    total_count: Optional[int] = None
    if want_count:
        count_sql = f'SELECT COUNT(*) AS "__count__" FROM "{table_name}"'
        try:
            count_res = opteryx.query(count_sql)
            for crow in count_res:
                try:
                    # crow may be mapping-like or positional
                    if hasattr(crow, "_asdict"):
                        total_count = int(dict(crow._asdict()).get("__count__", 0))
                    elif isinstance(crow, dict):
                        total_count = int(crow.get("__count__", 0))
                    else:
                        total_count = int(crow[0])
                except Exception:
                    total_count = None
                break
        except Exception:
            total_count = None

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
            # try to read type from result.columns objects if they expose it
            try:
                col_obj = result.columns[idx]
                if hasattr(col_obj, "py_type"):
                    edm_type = _map_py_type_to_edm(col_obj.py_type)
                elif hasattr(col_obj, "type"):
                    edm_type = _map_py_type_to_edm(col_obj.type)
                elif hasattr(col_obj, "dtype"):
                    edm_type = _map_py_type_to_edm(col_obj.dtype)
            except Exception:
                edm_type = None

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

    response = {
        "@odata.context": f"/api/v1/$metadata#{dataset}",
        "value": enriched_rows,
    }
    if total_count is not None:
        response["@odata.count"] = total_count
    return response


@router.get("/{workstream}/{collection}/{dataset}/$metadata")
def dataset_metadata(workstream: str, collection: str, dataset: str) -> Any:
    """Return a minimal OData $metadata (EDMX) for the given dataset.

    The endpoint inspects the dataset column metadata (without scanning rows)
    and emits an EDMX Schema describing an EntityType and EntitySet for the
    dataset. This is intentionally minimal — a full EDMX generator can be
    implemented later.
    """

    table_name = f"{workstream}.{collection}.{dataset}"
    sql = f'SELECT * FROM "{table_name}" LIMIT 1'

    try:
        arrow_table = opteryx.query_to_arrow(sql)
        col_names = arrow_table.column_names
    except Exception as exc:
        logger.exception(f"failed to query dataset for metadata {table_name}")
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
