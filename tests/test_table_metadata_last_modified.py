"""Test that tables include snapshot last-modified header and annotation."""
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
_expr_mod = types.ModuleType('opteryx.managers.expression')
_expr_mod.NodeType = type('NodeType', (), {})
sys.modules['opteryx.managers.expression'] = _expr_mod
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
_opteryx_catalog_mod.OpteryxCatalog = type('OpteryxCatalog', (), {'load_dataset': lambda self, p: None})
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


class FakeDatasetWithSnapshot:
    def __init__(self):
        self._schema = type('S', (), {'columns': [type('C', (), {'name': 'id', 'type': 'INTEGER', 'nullable': False})]})
    def schema(self):
        return self._schema
    def describe(self):
        return {'id': {'min': 1, 'max': 10}}
    def snapshot(self):
        return type('Snap', (), {
            'timestamp_ms': 1600000000000,
            'snapshot_id': 'snap-12345',
            'summary': {'total-records': 42, 'total-files-size': 1024},
        })()
    @property
    def metadata(self):
        return type('M', (), {'description': 'table desc', 'describer': 'Llama-xyz'})()


class FakeCatalog:
    def __init__(self, *args, **kwargs):
        pass
    def load_dataset(self, path):
        return FakeDatasetWithSnapshot()
    def load_view(self, path):
        raise RuntimeError("not a view")


def test_table_metadata_last_modified(monkeypatch):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)

    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(req, resp, workstream="public", collection="examples", dataset="my_table")
    content = res.content.decode() if hasattr(res, 'content') else res.body.decode()

    assert 'Custom.SourceType' in content
    assert 'Table' in content
    assert resp.headers.get('X-Dataset-LastModified') == str(1600000000000)
    assert 'Custom.LastModified' in content
    assert '+00:00' in content or 'Z' in content
    # Snapshot annotations
    assert 'Custom.Snapshot.Id' in content
    assert 'Custom.Snapshot.TotalRecords' in content
    assert 'Custom.Snapshot.TotalFileSize' in content
    # Description/LLM annotations
    assert 'Custom.Description' in content
    assert 'Custom.LLMDescribed' in content
