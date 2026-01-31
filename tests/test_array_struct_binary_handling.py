import sys
import types
import importlib.util
from pathlib import Path
from fastapi import Response
import base64
import json

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


class FakeDataset:
    def __init__(self, col_type_str, rows=None):
        self._schema = type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "col", "type": col_type_str, "nullable": True})]},
        )
        self._rows = rows or [{"col": [1, 2]}, {"col": {"a": 1}}, {"col": b"bytes"}]

    def schema(self):
        return self._schema

    def describe(self):
        return {}

    def snapshot(self):
        return None


class FakeCatalog:
    def __init__(self, col_type_str, rows=None):
        self.col_type_str = col_type_str
        self.rows = rows

    def load_dataset(self, path):
        return FakeDataset(self.col_type_str, self.rows)

    def load_view(self, path):
        raise RuntimeError("not a view")


def get_metadata_and_rows(col_type_str):
    interface.OpteryxCatalog = lambda *args, **kwargs: FakeCatalog(col_type_str)
    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_table"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    # Return metadata content only; row serialization is tested via sanitizer
    return content, None


def test_array_emits_collection_type_and_array_rows():
    # Column type represented as object with __str__ returning 'ARRAY<INTEGER>'
    class T:
        name = "ARRAY"

        def __str__(self):
            return "ARRAY<INTEGER>"

    content, _ = get_metadata_and_rows(T())
    assert "Collection(Edm.Int64)" in content
    # Ensure sanitize returns arrays not string
    assert interface._sanitize_value_for_odata([1, 2]) == [1, 2]


def test_struct_jsonb_emits_binary_and_base64_rows():
    class T2:
        name = "JSONB"

        def __str__(self):
            return "JSONB"

    content, _ = get_metadata_and_rows(T2())
    # Should declare property as Edm.Binary
    assert "Edm.Binary" in content
    assert "Custom.OriginalType" in content

    # serialization: dict -> base64 string via sanitizer
    encoded = interface._sanitize_value_for_odata({"a": 1})
    assert isinstance(encoded, str)
    decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    assert json.loads(decoded) == {"a": 1}


def test_blob_varbinary_mapped_to_binary():
    class TBLOB:
        name = "BLOB"

        def __str__(self):
            return "BLOB"

    content, _ = get_metadata_and_rows(TBLOB())
    assert "Edm.Binary" in content
    # bytes -> base64 string via sanitizer
    v = interface._sanitize_value_for_odata(b"hello")
    assert isinstance(v, str)
    assert base64.b64decode(v.encode("ascii")) == b"hello"
