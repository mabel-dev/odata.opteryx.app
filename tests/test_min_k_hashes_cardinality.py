import sys
import types
import importlib.util
from pathlib import Path
from fastapi import Response

# Provide lightweight fakes (same approach as existing tests)
sys.modules.setdefault("opteryx", types.ModuleType("opteryx"))
opteryx_mod = sys.modules["opteryx"]
opteryx_mod.set_default_connector = lambda *args, **kwargs: None
_connectors_mod = types.ModuleType("opteryx.connectors")
_connectors_mod.OpteryxConnector = type("OpteryxConnector", (), {})
sys.modules["opteryx.connectors"] = _connectors_mod
_expr_mod = types.ModuleType("opteryx.managers.expression")
_expr_mod.NodeType = type("NodeType", (), {})
sys.modules["opteryx.managers.expression"] = _expr_mod
_models_mod = types.ModuleType("opteryx.models")
_models_mod.Node = type("Node", (), {})
sys.modules["opteryx.models"] = _models_mod
_planner_mod = types.ModuleType("opteryx.planner.logical_planner")
_planner_mod.LogicalPlan = type("LogicalPlan", (), {})
_planner_mod.LogicalPlanNode = type("LogicalPlanNode", (), {})
_planner_mod.LogicalPlanStepType = type("LogicalPlanStepType", (), {})
sys.modules["opteryx.planner.logical_planner"] = _planner_mod
_logical_col_mod = types.ModuleType("opteryx.models.logical_column")
_logical_col_mod.LogicalColumn = type("LogicalColumn", (), {})
sys.modules["opteryx.models.logical_column"] = _logical_col_mod
# Minimal opteryx_catalog
_opteryx_catalog_mod = types.ModuleType("opteryx_catalog")
_opteryx_catalog_mod.OpteryxCatalog = type(
    "OpteryxCatalog", (), {"load_dataset": lambda self, p: None}
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
    pass


class DummyRequest:
    state = DummyState()


class FakeDatasetBase:
    def __init__(self, min_k_hashes=None, approx=None):
        self._schema = type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )
        self._min_k_hashes = min_k_hashes
        self._approx = approx

    def schema(self):
        return self._schema

    def describe(self):
        desc = {}
        if self._min_k_hashes is not None:
            desc["id"] = {"min_k_hashes": self._min_k_hashes}
            if self._approx is not None:
                desc["id"]["approx_cardinality"] = self._approx
        return desc


class FakeCatalogFactory:
    def __init__(self, min_k_hashes=None, approx=None):
        self.min_k_hashes = min_k_hashes
        self.approx = approx

    def load_dataset(self, path):
        return FakeDatasetBase(min_k_hashes=self.min_k_hashes, approx=self.approx)

    def load_view(self, path):
        raise RuntimeError("not a view")


def get_xml_for(min_k_hashes=None, approx=None):
    # monkeypatch via manual assignment
    interface.OpteryxCatalog = lambda *args, **kwargs: FakeCatalogFactory(min_k_hashes, approx)
    req = DummyRequest()
    resp = Response()
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_table"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()
    return content


def test_min_k_hashes_small_sets():
    content = get_xml_for(min_k_hashes=[10, 20])
    assert "Custom.Statistics.DistinctValueCountEstimate" in content
    assert 'Int="2"' in content


def test_min_k_hashes_threshold_exceeded():
    # 32 unique hashes -> do not infer (threshold is 30)
    mk = list(range(32))
    content = get_xml_for(min_k_hashes=mk)
    assert "Custom.Statistics.DistinctValueCountEstimate" not in content


def test_min_k_hashes_overrides_larger_approx():
    # approx_cardinality present but larger than actual uniq -> should be replaced
    content = get_xml_for(min_k_hashes=[1, 2, 2], approx=100)
    assert "Custom.Statistics.DistinctValueCountEstimate" in content
    assert 'Int="2"' in content
