"""Tests for source type annotations in EDMX metadata."""

import sys
import types
from fastapi import Response

# Provide lightweight fake modules required by imports to avoid heavy dependencies
sys.modules.setdefault("opteryx", types.ModuleType("opteryx"))
# Create a fake opteryx.connectors module with the symbol expected by imports
_connectors_mod = types.ModuleType("opteryx.connectors")
_connectors_mod.OpteryxConnector = type("OpteryxConnector", (), {})
sys.modules["opteryx.connectors"] = _connectors_mod
# Minimal fake for opteryx_catalog used by other modules
_opteryx_catalog_mod = types.ModuleType("opteryx_catalog")
# Provide a minimal OpteryxCatalog class so imports succeed; tests will monkeypatch behavior as needed
_opteryx_catalog_mod.OpteryxCatalog = type(
    "OpteryxCatalog", (), {"load_dataset": lambda self, path: None}
)
sys.modules["opteryx_catalog"] = _opteryx_catalog_mod

# Import the target module directly to avoid importing package __init__ modules
import importlib.util
from pathlib import Path

_metadata_path = Path(__file__).parents[1] / "app" / "routes" / "v4" / "metadata.py"
spec = importlib.util.spec_from_file_location("metadata_mod", str(_metadata_path))
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


class DummyState:
    pass


class DummyRequest:
    state = DummyState()


def test_edmx_metadata_includes_source_annotation(monkeypatch):
    # Create a fake DatasetInfo with explicit source
    ds = metadata.DatasetInfo(
        resource="public.ns.table",
        workspace="public",
        collection="ns",
        name="table",
        source="Table",
    )

    # Patch dataset retrieval functions to return our dataset
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [ds])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    # Ensure dataset schema lookup returns a minimal column so EntityType is created
    monkeypatch.setattr(metadata, "_get_dataset_schema", lambda dataset_info: [("id", "Edm.Int64")])

    # Prepare a fake request with claims allowing access to public workspace
    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    resp = Response()
    res = metadata.get_edmx_metadata(req, resp)

    # Response content should include our custom annotation term
    content = res.body.decode() if hasattr(res, "body") else res.content.decode()
    assert "Custom.SourceType" in content
    assert "Table" in content
