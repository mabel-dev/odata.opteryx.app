"""The docs exporter must honour `docs_metadata(public=False)`.

It did not: FastAPI wraps an included router in a lazy `_IncludedRouter`, the
per-route filter passed that through whole, and any route marked `public=False` was published in the
public API reference.
"""

import importlib.util
import pathlib

from app.main import service

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "export_api_docs.py"
_spec = importlib.util.spec_from_file_location("export_api_docs", _PATH)
export_api_docs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_api_docs)

from fastapi import APIRouter, FastAPI

from app.docs import docs_metadata

# This service has no non-public route, so the included-router case is built here.


def _app_with_included_internal_route() -> FastAPI:
    router = APIRouter()

    @router.get("/public", **docs_metadata(summary="Public endpoint"))
    def public_endpoint():
        return {}

    @router.get("/internal", **docs_metadata(public=False, summary="Internal endpoint"))
    def internal_endpoint():
        return {}

    app = FastAPI(title="Test API", version="1.0.0")
    app.include_router(router)
    return app


def test_public_export_omits_non_public_routes_of_an_included_router():
    paths = export_api_docs._build_openapi(_app_with_included_internal_route(), "public")["paths"]
    assert "/internal" not in paths
    assert "/public" in paths


def test_all_export_keeps_them():
    paths = export_api_docs._build_openapi(_app_with_included_internal_route(), "all")["paths"]
    assert "/internal" in paths


def test_service_export_never_leaks_the_flag():
    paths = export_api_docs._build_openapi(service, "all")["paths"]
    assert "/api/v4/$metadata" in paths
    for path_item in paths.values():
        for operation in path_item.values():
            assert export_api_docs.DOCS_PUBLIC_FLAG not in operation
