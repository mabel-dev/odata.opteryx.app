import opteryx
from brotli_asgi import BrotliMiddleware
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from orso.logging import get_logger
from orso.logging import set_log_name

from app.middleware.audit import AuditMiddleware
from app.middleware.cors import configure_cors
from app.routes import router as routes_router

set_log_name("opteryx.odata")
logger = get_logger()
logger.setLevel(5)

service = FastAPI(title="Opteryx OData Service", default_response_class=ORJSONResponse)

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
    from fastapi.responses import ORJSONResponse

    # Advertise the example `planets` EntitySet so clients can discover it
    body = {
        "@odata.context": "/$metadata",
        "value": [{"name": "planets", "url": "api/v1/opteryx/test/planets"}],
    }
    return ORJSONResponse(content=body, headers={"OData-Version": "4.0"})


@service.get("/$metadata", include_in_schema=False)
def root_metadata():
    """Return a minimal EDMX service-level $metadata document.

    This is intentionally minimal: it provides the EDMX envelope and an empty
    EntityContainer so OData-aware clients can detect the service. More
    complete metadata can be implemented later.
    """
    # Emit an EDMX that exposes the example `planets` EntitySet at the
    # service root so clients (like Excel) can resolve entity types.
    table_name = "opteryx.test.planets"
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

    schema_ns = "test"
    entity_name = "test_planets"
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
            f'        <EntitySet Name="planets" EntityType="{schema_ns}.{entity_name}" />',
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
    from fastapi.responses import ORJSONResponse

    # Provide a service document listing available EntitySets. For now we
    # expose the example dataset `opteryx.test.planets` so clients can
    # discover it.
    body = {
        "@odata.context": "/api/v1/$metadata",
        "value": [{"name": "planets", "kind": "EntitySet", "url": "opteryx/test/planets"}],
    }
    return ORJSONResponse(content=body, headers={"OData-Version": "4.0"})


@service.get("/api/v1/$metadata", include_in_schema=False)
def api_v1_metadata():
    # Build a minimal EDMX that exposes the example EntitySet for
    # `opteryx.test.planets`. We inspect the dataset (LIMIT 1) to learn
    # its column names and types.
    table_name = "opteryx.test.planets"
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

    # Compose EDMX
    schema_ns = "test"
    entity_name = "test_planets"
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
            f'        <EntitySet Name="planets" EntityType="{schema_ns}.{entity_name}" />',
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
