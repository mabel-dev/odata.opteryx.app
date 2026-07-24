"""Test that views without snapshot do not set X-Dataset-LastModified."""

import sys
import types
import importlib.util
from pathlib import Path
from fastapi import Response

# Provide lightweight fakes
sys.modules.setdefault("opteryx", types.ModuleType("opteryx"))
opteryx_mod = sys.modules["opteryx"]
opteryx_mod.set_default_connector = lambda *args, **kwargs: None
_connectors_mod = types.ModuleType("opteryx.connectors")
_connectors_mod.OpteryxConnector = type("OpteryxConnector", (), {})
sys.modules["opteryx.connectors"] = _connectors_mod
# Minimal opteryx.managers.expression
_expr_mod = types.ModuleType("opteryx.managers.expression")
_expr_mod.NodeType = type("NodeType", (), {})
sys.modules["opteryx.managers.expression"] = _expr_mod
# Minimal planner modules
_planner_mod = types.ModuleType("opteryx.planner.logical_planner")
_planner_mod.LogicalPlan = type("LogicalPlan", (), {})
_planner_mod.LogicalPlanNode = type("LogicalPlanNode", (), {})
_planner_mod.LogicalPlanStepType = type("LogicalPlanStepType", (), {})
sys.modules["opteryx.planner.logical_planner"] = _planner_mod
# Minimal models
_models_mod = types.ModuleType("opteryx.models")
_models_mod.Node = type("Node", (), {})
sys.modules["opteryx.models"] = _models_mod
_logical_col_mod = types.ModuleType("opteryx.models.logical_column")
_logical_col_mod.LogicalColumn = type("LogicalColumn", (), {})
sys.modules["opteryx.models.logical_column"] = _logical_col_mod
# Minimal opteryx_catalog
_opteryx_catalog_mod = types.ModuleType("opteryx_catalog")
_opteryx_catalog_mod.OpteryxCatalog = type(
    "OpteryxCatalog", (), {"load_view": lambda self, p: None}
)
sys.modules["opteryx_catalog"] = _opteryx_catalog_mod

# Load interface module by file
spec = importlib.util.spec_from_file_location(
    "interface_mod",
    str(Path(__file__).resolve().parents[1] / "app" / "routes" / "v4" / "interface.py"),
)
interface = importlib.util.module_from_spec(spec)
loader = spec.loader
assert loader is not None
loader.exec_module(interface)


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class FakeViewNoSnapshot:
    # Has schema to avoid placeholder but no snapshot method
    def schema(self):
        return type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def describe(self):
        return {"id": {"min": 1, "max": 10}}


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        raise RuntimeError("not a dataset")

    def load_view(self, path):
        return FakeViewNoSnapshot()


def test_view_without_snapshot_no_last_modified(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="v_no_snapshot"
    )

    content = res.content.decode() if hasattr(res, "content") else res.body.decode()
    assert "Custom.SourceType" in content
    assert "View" in content
    # Ensure last-modified header is not set for views without snapshot
    assert resp.headers.get("X-Dataset-LastModified") is None
