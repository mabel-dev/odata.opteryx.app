import os
import sys

import opteryx
from brotli_asgi import BrotliMiddleware
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import ORJSONResponse
from orso.logging import get_logger
from orso.logging import set_log_name

from app.middleware.audit import AuditMiddleware
from app.middleware.cors import configure_cors
from app.routes import router as routes_router
from app.routes.v4.errors import ODataError
from app.routes.v4.errors import generic_exception_handler
from app.routes.v4.errors import http_exception_handler
from app.routes.v4.errors import odata_error_handler

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../opteryx-core")))
sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../pyiceberg-firestore-gcs")),
)


set_log_name("opteryx.odata")
logger = get_logger()
logger.setLevel(5)

service = FastAPI(title="Opteryx OData Service", default_response_class=ORJSONResponse)

# Register exception handlers for OData v4 error format
service.add_exception_handler(ODataError, odata_error_handler)
service.add_exception_handler(HTTPException, http_exception_handler)
service.add_exception_handler(Exception, generic_exception_handler)

configure_cors(service)

service.add_middleware(BrotliMiddleware, mode="generic", quality=4, minimum_size=1024)
service.add_middleware(AuditMiddleware)

# include routes
service.include_router(routes_router)


@service.get("/", include_in_schema=False)
def root():
    """Return a minimal OData Service Document so clients can discover the
    service and metadata.
    """
    # Dynamically discover available datasets from the catalog
    datasets = []
    try:
        # Query the catalog for available tables
        result = opteryx.query("SHOW TABLES")
        for row in result:
            # Parse table name (format: workstream.collection.dataset)
            if hasattr(row, "_asdict"):
                table_name = dict(row._asdict()).get("table_name") or dict(row._asdict()).get(
                    "name"
                )
            elif isinstance(row, dict):
                table_name = row.get("table_name") or row.get("name")
            else:
                table_name = row[0] if len(row) > 0 else None

            if table_name:
                # Split into parts for URL construction
                parts = table_name.split(".")
                if len(parts) >= 3:
                    workstream, collection, dataset = parts[0], parts[1], ".".join(parts[2:])
                    datasets.append(
                        {"name": dataset, "url": f"api/v1/{workstream}/{collection}/{dataset}"}
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list datasets: %s", exc)

    body = {
        "@odata.context": "/$metadata",
        "value": datasets,
    }
    return ORJSONResponse(content=body, headers={"OData-Version": "4.0"})


@service.get("/$metadata", include_in_schema=False)
def root_metadata():
    """Return a minimal EDMX service-level $metadata document.

    This is intentionally minimal: it provides the EDMX envelope and an empty
    EntityContainer so OData-aware clients can detect the service. More
    complete metadata can be implemented later.
    """
    # Try to get the first available dataset for metadata generation
    table_name = None
    try:
        result = opteryx.query("SHOW TABLES")
        for row in result:
            if hasattr(row, "_asdict"):
                table_name = dict(row._asdict()).get("table_name") or dict(row._asdict()).get(
                    "name"
                )
            elif isinstance(row, dict):
                table_name = row.get("table_name") or row.get("name")
            else:
                table_name = row[0] if len(row) > 0 else None
            if table_name:
                break
    except Exception:  # noqa: BLE001
        pass

    if not table_name:
        # Fallback to empty metadata if no datasets available
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">\n'
            "  <edmx:DataServices>\n"
            '    <Schema Namespace="Default" xmlns="http://docs.oasis-open.org/odata/ns/edm">\n'
            '      <EntityContainer Name="Container">\n'
            "      </EntityContainer>\n"
            "    </Schema>\n"
            "  </edmx:DataServices>\n"
            "</edmx:Edmx>"
        )
        from fastapi.responses import Response

        return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})

    sql = f'SELECT * FROM "{table_name}" LIMIT 1'
    col_names = []
    col_types = []
    try:
        arrow_table = opteryx.query_to_arrow(sql)
        col_names = arrow_table.column_names

        # Get first row to infer types
        if arrow_table.num_rows > 0:
            columns_dict = arrow_table.to_pydict()

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

            for col_name in col_names:
                val = columns_dict[col_name][0]
                if val is not None:
                    col_types.append(_map_py_type_to_edm(type(val)))
                else:
                    col_types.append("Edm.String")
        else:
            col_types = ["Edm.String"] * len(col_names)
    except Exception:
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">\n'
            "  <edmx:DataServices>\n"
            '    <Schema Namespace="Default" xmlns="http://docs.oasis-open.org/odata/ns/edm">\n'
            '      <EntityContainer Name="Container">\n'
            "      </EntityContainer>\n"
            "    </Schema>\n"
            "  </edmx:DataServices>\n"
            "</edmx:Edmx>"
        )
        from fastapi.responses import Response

        return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})

    # Parse table name to build schema namespace and entity name
    parts = table_name.split(".")
    if len(parts) >= 3:
        schema_ns = parts[1]  # collection
        dataset_name = ".".join(parts[2:])  # dataset
        entity_name = f"{parts[1]}_{parts[2]}"
    else:
        schema_ns = "Default"
        dataset_name = table_name
        entity_name = table_name.replace(".", "_")

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">',
        "  <edmx:DataServices>",
        f'    <Schema Namespace="{schema_ns}" xmlns="http://docs.oasis-open.org/odata/ns/edm">',
        f'      <EntityType Name="{entity_name}">',
    ]

    # Add Key element (use first column)
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
            f'        <EntitySet Name="{dataset_name}" EntityType="{schema_ns}.{entity_name}" />',
            "      </EntityContainer>",
            "    </Schema>",
            "  </edmx:DataServices>",
            "</edmx:Edmx>",
        ]
    )

    from fastapi.responses import Response

    xml = "\n".join(lines)
    return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})


# API v1 service document and metadata so clients can discover the v1 endpoint
@service.get("/api/v1", include_in_schema=False)
def api_v1_root():
    # Dynamically discover available datasets from the catalog
    datasets = []
    try:
        result = opteryx.query("SHOW TABLES")
        for row in result:
            if hasattr(row, "_asdict"):
                table_name = dict(row._asdict()).get("table_name") or dict(row._asdict()).get(
                    "name"
                )
            elif isinstance(row, dict):
                table_name = row.get("table_name") or row.get("name")
            else:
                table_name = row[0] if len(row) > 0 else None

            if table_name:
                parts = table_name.split(".")
                if len(parts) >= 3:
                    workstream, collection, dataset = parts[0], parts[1], ".".join(parts[2:])
                    datasets.append(
                        {
                            "name": dataset,
                            "kind": "EntitySet",
                            "url": f"{workstream}/{collection}/{dataset}",
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list datasets: %s", exc)

    body = {
        "@odata.context": "/api/v1/$metadata",
        "value": datasets,
    }
    return ORJSONResponse(content=body, headers={"OData-Version": "4.0"})


@service.get("/api/v1/$metadata", include_in_schema=False)
def api_v1_metadata():
    # Build a minimal EDMX that discovers available datasets dynamically
    # Try to get the first available dataset for metadata generation
    table_name = None
    try:
        result = opteryx.query("SHOW TABLES")
        for row in result:
            if hasattr(row, "_asdict"):
                table_name = dict(row._asdict()).get("table_name") or dict(row._asdict()).get(
                    "name"
                )
            elif isinstance(row, dict):
                table_name = row.get("table_name") or row.get("name")
            else:
                table_name = row[0] if len(row) > 0 else None
            if table_name:
                break
    except Exception:  # noqa: BLE001
        pass

    if not table_name:
        # Fallback to empty metadata if no datasets available
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">\n'
            "  <edmx:DataServices>\n"
            '    <Schema Namespace="ApiV1" xmlns="http://docs.oasis-open.org/odata/ns/edm">\n'
            '      <EntityContainer Name="Container">\n'
            "      </EntityContainer>\n"
            "    </Schema>\n"
            "  </edmx:DataServices>\n"
            "</edmx:Edmx>"
        )
        from fastapi.responses import Response

        return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})

    sql = f'SELECT * FROM "{table_name}" LIMIT 1'
    col_names = []
    col_types = []
    try:
        arrow_table = opteryx.query_to_arrow(sql)
        col_names = arrow_table.column_names

        # Get first row to infer types
        if arrow_table.num_rows > 0:
            columns_dict = arrow_table.to_pydict()

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

            for col_name in col_names:
                val = columns_dict[col_name][0]
                if val is not None:
                    col_types.append(_map_py_type_to_edm(type(val)))
                else:
                    col_types.append("Edm.String")
        else:
            col_types = ["Edm.String"] * len(col_names)
    except Exception:
        # Fallback: emit an empty container
        xml = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<edmx:Edmx Version="4.0" xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx">\n'
            "  <edmx:DataServices>\n"
            '    <Schema Namespace="ApiV1" xmlns="http://docs.oasis-open.org/odata/ns/edm">\n'
            '      <EntityContainer Name="Container">\n'
            "      </EntityContainer>\n"
            "    </Schema>\n"
            "  </edmx:DataServices>\n"
            "</edmx:Edmx>"
        )
        from fastapi.responses import Response

        return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})

    # Compose EDMX with dynamic dataset name
    parts = table_name.split(".")
    if len(parts) >= 3:
        schema_ns = parts[1]  # collection
        dataset_name = ".".join(parts[2:])  # dataset
        entity_name = f"{parts[1]}_{parts[2]}"
    else:
        schema_ns = "ApiV1"
        dataset_name = table_name
        entity_name = table_name.replace(".", "_")

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
            f'        <EntitySet Name="{dataset_name}" EntityType="{schema_ns}.{entity_name}" />',
            "      </EntityContainer>",
            "    </Schema>",
            "  </edmx:DataServices>",
            "</edmx:Edmx>",
        ]
    )

    from fastapi.responses import Response

    xml = "\n".join(lines)
    return Response(content=xml, media_type="application/xml", headers={"OData-Version": "4.0"})


__all__ = ["service"]
