"""Tests for view fallback in dataset metadata endpoint."""
import sys
import types
import importlib.util
from pathlib import Path
from fastapi import Response

# Provide lightweight fake modules required by interface imports
sys.modules.setdefault('opteryx', types.ModuleType('opteryx'))
# opteryx.connectors
_connectors_mod = types.ModuleType('opteryx.connectors')
_connectors_mod.OpteryxConnector = type('OpteryxConnector', (), {})
sys.modules['opteryx.connectors'] = _connectors_mod
# Minimal opteryx.managers.expression with NodeType
_expr_mod = types.ModuleType('opteryx.managers.expression')
_expr_mod.NodeType = type('NodeType', (), {})
sys.modules['opteryx.managers.expression'] = _expr_mod
# Minimal opteryx.models and submodules
_models_mod = types.ModuleType('opteryx.models')
_models_mod.Node = type('Node', (), {})
sys.modules['opteryx.models'] = _models_mod
_logical_col_mod = types.ModuleType('opteryx.models.logical_column')
_logical_col_mod.LogicalColumn = type('LogicalColumn', (), {})
sys.modules['opteryx.models.logical_column'] = _logical_col_mod
# Minimal planner modules
_planner_mod = types.ModuleType('opteryx.planner.logical_planner')
_planner_mod.LogicalPlan = type('LogicalPlan', (), {})
_planner_mod.LogicalPlanNode = type('LogicalPlanNode', (), {})
_planner_mod.LogicalPlanStepType = type('LogicalPlanStepType', (), {})
sys.modules['opteryx.planner.logical_planner'] = _planner_mod
# Minimal opteryx_catalog
_opteryx_catalog_mod = types.ModuleType('opteryx_catalog')
_opteryx_catalog_mod.OpteryxCatalog = type('OpteryxCatalog', (), {'load_dataset': lambda self, path: None})
sys.modules['opteryx_catalog'] = _opteryx_catalog_mod

# Load interface module by file to avoid package import side-effects
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


class DummyDoc:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


class DummyQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, *args, **kwargs):
        return self

    def limit(self, *_):
        return self

    def stream(self):
        for d in self._docs:
            yield d


class DummyDB:
    def __init__(self, docs):
        self._docs = docs

    def collection_group(self, name):
        if name == "views":
            return DummyQuery(self._docs)
        return DummyQuery([])


def test_dataset_metadata_view_fallback(monkeypatch):
    # Make OpteryxCatalog.load_dataset raise to simulate unsupported view
    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            raise RuntimeError("catalog cannot load view")

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    # Fake Firestore DB that has a view doc
    view_doc = DummyDoc({"workspace": "public", "namespace": "examples", "name": "my_view"})
    db = DummyDB([view_doc])
    monkeypatch.setattr(interface, "_get_firestore_client", lambda database=None: db)

    # Prepare request/response
    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(req, resp, workstream="public", collection="examples", dataset="my_view")

    # Ensure XML contains the view annotation and entity type
    content = res.content.decode()
    assert "Custom.SourceType" in content
    assert "View" in content
    assert "EntityType Name=\"examples_my_view\"" in content or "EntityType Name=\'examples_my_view\'" in content
    # Header should indicate source
    assert resp.headers.get("X-Dataset-Source") == "View"
