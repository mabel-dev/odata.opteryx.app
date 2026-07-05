#!/usr/bin/env python3
"""Export the OpenAPI schema of a FastAPI application to a JSON file.

The output file is named `api-{project_name}.json` where `project_name` comes from
`pyproject.toml` [project].name.

This script is designed to be reusable in different FastAPI-based APIs. You can
point it at any Python module that exposes a FastAPI/Starlette instance and it
will emit a spec file suitable for documentation sites.

Usage:
    python scripts/export_api_docs.py
    python scripts/export_api_docs.py --app mypkg.web:app
    python scripts/export_api_docs.py --pyproject /path/to/pyproject.toml
    python scripts/export_api_docs.py --out docs/api

If --out-dir is not specified, the file is written to the current working
directory.

Example output (for this repo):
    api-opteryx-odata.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

DOCS_PUBLIC_FLAG = "x-docs-public"


try:
    import tomllib  # type: ignore
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _load_project_name(pyproject_path: Path) -> str:
    with pyproject_path.open("rb") as f:
        data = tomllib.load(f)

    project = data.get("project") or {}
    name = project.get("name")
    if not name:
        raise SystemExit("Could not determine project name from pyproject.toml")
    return name


def _load_app(import_path: str):
    if ":" in import_path:
        module_name, attr = import_path.split(":", 1)
    else:
        module_name, attr = import_path, "app"

    module = importlib.import_module(module_name)
    app = getattr(module, attr, None)
    if app is None and attr == "app":
        # Some projects use `service` as the FastAPI instance name
        app = getattr(module, "service", None)

    if app is None:
        raise SystemExit(f"No attribute '{attr}' found in module '{module_name}'")

    if not hasattr(app, "openapi"):
        raise SystemExit("Loaded object does not appear to be a FastAPI/Starlette app")

    return app


def _include_route_for_audience(route: object, audience: str) -> bool:
    if audience == "all" or not isinstance(route, APIRoute):
        return True

    # FastAPI can hide routes from OpenAPI via include_in_schema=False.
    if getattr(route, "include_in_schema", True) is False:
        return False

    openapi_extra = route.openapi_extra or {}
    return openapi_extra.get(DOCS_PUBLIC_FLAG, True) is not False


def _build_openapi(app, audience: str) -> dict:
    routes = [route for route in app.routes if _include_route_for_audience(route, audience)]
    schema = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=getattr(app, "summary", None),
        description=app.description,
        routes=routes,
        tags=getattr(app, "openapi_tags", None),
        servers=getattr(app, "servers", None),
    )

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                operation.pop(DOCS_PUBLIC_FLAG, None)

    return schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export FastAPI OpenAPI schema to a JSON file named api-<project>.json"
    )
    parser.add_argument(
        "--app",
        default="app.main:service",
        help="Python import path to the FastAPI app (module:attribute).",
    )
    parser.add_argument(
        "--pyproject",
        default="pyproject.toml",
        help="Path to pyproject.toml to read the project name from.",
    )
    parser.add_argument(
        "--out-dir",
        default=".",
        help="Output directory for the generated JSON file.",
    )
    parser.add_argument(
        "--audience",
        choices=["public", "all"],
        default="public",
        help="Export only public endpoints or all endpoints.",
    )
    args = parser.parse_args(argv)

    # Ensure the repo root is on sys.path so `app` (and other local packages)
    # can be imported regardless of the current working directory.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    pyproject_path = Path(args.pyproject)
    if not pyproject_path.exists():
        raise SystemExit(f"pyproject.toml not found at {pyproject_path}")

    project_name = _load_project_name(pyproject_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    app = _load_app(args.app)
    openapi = _build_openapi(app, args.audience)

    output_path = out_dir / f"api-{project_name}.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(openapi, f, ensure_ascii=False, indent=2)

    print(f"Wrote OpenAPI schema to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
