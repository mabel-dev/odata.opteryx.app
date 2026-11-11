"""The route inventory is FROZEN. Adding a route to this service is not a
refactor, a tidy-up, or an implementation detail of a feature. It is a public
API change on a deployed service, and it is the service owner's decision.

This test exists because an agent added `$consumers` to this service without
being asked, on the strength of a design document that described routes which
had already been deliberately removed. It cost hours. It will not happen twice
quietly.

If this test is failing, you added or removed a route. That is not, by itself,
wrong -- it is unapproved. The fix is to get approval and then edit the list
below in the same commit as the route, so the diff shows a human decided.
"""

import pytest

from app.main import service

# Every (method, path) this service serves. Sorted, exact, exhaustive.
# DO NOT edit this list to make a test pass. Edit it because the owner of this
# service asked for the route on the other side of the diff.
FROZEN_ROUTES = [
    ("GET", "/api/v4/"),
    ("GET", "/api/v4/$metadata"),
    ("GET", "/api/v4/{workstream}/{collection}/{dataset}"),
    ("GET", "/api/v4/{workstream}/{collection}/{dataset}/$metadata"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/health"),
    ("GET", "/health/live"),
    ("GET", "/internal/clickbench/run"),
    ("GET", "/openapi.json"),
    ("GET", "/redoc"),
    ("HEAD", "/docs"),
    ("HEAD", "/docs/oauth2-redirect"),
    ("HEAD", "/openapi.json"),
    ("HEAD", "/redoc"),
]


def _flattened(routes):
    """Every route the app serves, with included routers expanded.

    FastAPI 0.162 stopped copying an included router's routes into
    `service.routes`; it keeps one entry per `include_router`, which yields the
    routes it serves (full path, methods) from `effective_route_contexts()`.
    Older FastAPI flattened them, and those entries are plain routes.
    """
    for route in routes:
        expand = getattr(route, "effective_route_contexts", None)
        if callable(expand):
            yield from expand()
        else:
            yield route


def _live_routes():
    rows = set()
    for route in _flattened(service.routes):
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in sorted(getattr(route, "methods", None) or ["*"]):
            rows.add((method, path))
    return rows


def test_no_route_was_added_or_removed():
    live = _live_routes()
    frozen = set(FROZEN_ROUTES)

    added = sorted(live - frozen)
    removed = sorted(frozen - live)
    if not added and not removed:
        return

    lines = ["", "=" * 72]
    if added:
        lines.append("YOU ADDED A ROUTE TO ODATA. NOBODY ASKED YOU TO.")
        lines.append("")
        lines += [f"    ADDED:   {method:6} {path}" for method, path in added]
    if removed:
        if added:
            lines.append("")
        lines.append("YOU REMOVED A ROUTE FROM ODATA. NOBODY ASKED YOU TO.")
        lines.append("")
        lines += [f"    REMOVED: {method:6} {path}" for method, path in removed]
    lines += [
        "",
        "This service is DEPLOYED. Its routes are a public contract with every",
        "client, including ones you cannot see. Adding one is not a detail of",
        "the feature you were asked for, and it is not yours to decide.",
        "",
        "A design document is NOT approval. Several of them in this codebase",
        "describe routes that were built and then deliberately deleted. If a",
        "document tells you to add a route, the document is stale and the",
        "absence of the route is the decision.",
        "",
        "STOP. Ask the owner. If they say yes, edit FROZEN_ROUTES in",
        "tests/test_route_inventory.py in the SAME COMMIT as the route, so the",
        "diff records that a human agreed.",
        "=" * 72,
        "",
    ]
    pytest.fail("\n".join(lines), pytrace=False)
