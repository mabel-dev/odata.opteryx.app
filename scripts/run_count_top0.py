#!/usr/bin/env python3
"""Run read_dataset for $planets with $count=true&$top=0 to verify COUNT-only path."""

import sys

# Provide a dummy OpteryxConnector if the real one isn't available so imports succeed
import types
from pathlib import Path
from unittest.mock import Mock

import opteryx

from app.routes.v4.interface import read_dataset

# Ensure repo root and app are on sys.path
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "app"))

if not hasattr(opteryx, "connectors"):
    opteryx.connectors = types.ModuleType("opteryx.connectors")
if not hasattr(opteryx.connectors, "OpteryxConnector"):

    class DummyConnector:
        pass

    opteryx.connectors.OpteryxConnector = DummyConnector

# Provide a dummy OpteryxCatalog if not available so the module import succeeds
if "opteryx_catalog" not in sys.modules:
    mod = types.ModuleType("opteryx_catalog")

    class OpteryxCatalog:
        def __init__(self, *args, **kwargs):
            pass

    mod.OpteryxCatalog = OpteryxCatalog
    sys.modules["opteryx_catalog"] = mod

# Provide a no-op set_default_connector if opteryx lacks it in this environment
if not hasattr(opteryx, "set_default_connector"):

    def set_default_connector(*args, **kwargs):
        return None

    opteryx.set_default_connector = set_default_connector


# Monkeypatch opteryx.execute_logical_plan with a lightweight fake executor
# so we can test the COUNT-only path without invoking the full Opteryx runtime.


def _fake_execute_logical_plan(plan):
    # Simple heuristics based on node types
    for _, node in plan.nodes(True):
        nt = str(getattr(node, "node_type", ""))
        if "Aggregate" in nt:

            class Table:
                column_names = ["count"]
                num_rows = 1

                def to_pydict(self):
                    return {"count": [9]}

            return Table()
        if "Limit" in nt:

            class Table:
                column_names = ["id", "name"]
                num_rows = 0

                def to_pydict(self):
                    return {}

            return Table()

    class Table:
        column_names = []
        num_rows = 0

        def to_pydict(self):
            return {}

    return Table()


opteryx.execute_logical_plan = _fake_execute_logical_plan

request = Mock()
request.state = Mock()
request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}
# Simulate a minimal URL object with an empty query string
request.url = Mock()
request.url.query = ""

response = Mock()
response.headers = {}

try:
    res = read_dataset(
        request=request,
        response=response,
        workstream="public",
        collection="examples",
        dataset="planets",
        filter=None,
        top=0,
        skip=None,
        orderby=None,
        count="true",
        select=None,
        search=None,
        apply=None,
    )
    print("Result keys:", res.keys())
    print("total_count:", res.get("total_count"))
    print("rows:", res.get("rows"))
except Exception as e:
    print("Error calling read_dataset:", e)
    import traceback

    traceback.print_exc()
