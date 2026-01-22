"""Test that metadata endpoint includes view metadata (definition, author, description, LLM flag)."""
import sys
import types
import importlib.util
from pathlib import Path
from fastapi import Response

# Provide lightweight fakes
sys.modules.setdefault('opteryx', types.ModuleType('opteryx'))
opteryx_mod = sys.modules['opteryx']
opteryx_mod.set_default_connector = lambda *args, **kwargs: None
_connectors_mod = types.ModuleType('opteryx.connectors')
_connectors_mod.OpteryxConnector = type('OpteryxConnector', (), {})
sys.modules['opteryx.connectors'] = _connectors_mod
# Minimal opteryx.managers.expression
_expr_mod = types.ModuleType('opteryx.managers.expression')
_expr_mod.NodeType = type('NodeType', (), {})
sys.modules['opteryx.managers.expression'] = _expr_mod
# Minimal models and planner
_models_mod = types.ModuleType('opteryx.models')
_models_mod.Node = type('Node', (), {})
sys.modules['opteryx.models'] = _models_mod
_planner_mod = types.ModuleType('opteryx.planner.logical_planner')
_planner_mod.LogicalPlan = type('LogicalPlan', (), {})
_planner_mod.LogicalPlanNode = type('LogicalPlanNode', (), {})
_planner_mod.LogicalPlanStepType = type('LogicalPlanStepType', (), {})
sys.modules['opteryx.planner.logical_planner'] = _planner_mod
_logical_col_mod = types.ModuleType('opteryx.models.logical_column')
_logical_col_mod.LogicalColumn = type('LogicalColumn', (), {})
sys.modules['opteryx.models.logical_column'] = _logical_col_mod
# Minimal opteryx_catalog
_opteryx_catalog_mod = types.ModuleType('opteryx_catalog')
_opteryx_catalog_mod.OpteryxCatalog = type('OpteryxCatalog', (), {'load_view': lambda self, p: None})
sys.modules['opteryx_catalog'] = _opteryx_catalog_mod

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
    pass


class DummyRequest:
    state = DummyState()


class FakeMeta:
    def __init__(self, ts, desc, author, describer):
        self.timestamp_ms = ts
        self.description = desc
        self.author = author
        self.describer = describer


class FakeView:
    def __init__(self, sql, meta):
        self.sql = sql
        self.metadata = meta


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass
    def load_dataset(self, path):
        raise RuntimeError("not a dataset")
    def load_view(self, path):
        return FakeView("select * from foo where id > 0", FakeMeta(1700000000000, "desc", "authorX", "Gemini:desc"))


def test_view_metadata_includes_annotations(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(req, resp, workstream="public", collection="examples", dataset="my_view")
    content = res.content.decode() if hasattr(res, 'content') else res.body.decode()

    assert 'Custom.SourceType' in content
    assert 'View' in content
    assert 'Custom.Definition' in content
    assert 'Custom.Author' in content
    assert 'Custom.Description' in content
    assert 'Custom.LLMDescribed' in content
    # header from metadata timestamp
    assert resp.headers.get('X-Dataset-LastModified') == str(1700000000000)
    # LastModified annotation present in metadata and in ISO format
    assert 'Custom.LastModified' in content
    assert '+00:00' in content or 'Z' in content
