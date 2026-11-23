"""Property graphs in the service document and both `$metadata` documents.

A property graph (SQL/PGQ) shares the `workspace.collection.<object>` namespace
with tables, views and tasks, and like a task it holds no rows of its own. What
its name answers a read with is its STRUCTURE -- one row per vertex or edge
table, served by the engine -- which is what a client needs to draw the graph,
and never the contents of the tables it names. So it is an EntitySet like any
other, with fixed columns (PROPERTY_GRAPH_STRUCTURE_COLUMNS), and its `$metadata`
carries its canonical statement as `Custom.Definition`.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import HTTPException, Response

from app.odata.virtual_datasets import PROPERTY_GRAPH_DESCRIPTION, PROPERTY_GRAPH_STRUCTURE_COLUMNS
from app.routes.v4 import interface, metadata
from app.routes.v4 import service_document as sd

EDM = "http://docs.oasis-open.org/odata/ns/edm"

STATEMENT = (
    "CREATE PROPERTY GRAPH public.sky.orbits\n  VERTEX TABLES (\n"
    "    public.sky.planets AS planets KEY (id) LABEL Planet PROPERTIES (id, name)\n  );"
)

# What OpteryxCatalog.load_property_graph() returns, in shape.
GRAPH = {
    "name": "orbits",
    "collection": "sky",
    "workspace": "public",
    "statement": STATEMENT,
    "author": "justin",
    "description": "Moons and the planets they orbit.",
    "timestamp-ms": 1787261893891,
    "vertex-tables": [],
    "edge-tables": [],
}


# ------------------------------------------------------------------ the listings


@pytest.mark.parametrize("module", [sd, metadata])
def test_a_graph_document_is_labelled_a_property_graph(module):
    assert module._source_type_from_doc(GRAPH, "property_graphs") == "PropertyGraph"


def test_the_service_document_lists_graphs_beside_datasets_and_views(monkeypatch):
    """A graph document carries `workspace` and `collection`, so it is reached by
    the same collection-group query as datasets and views - not a listing of its
    own, as a task needs."""
    asked = []
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids, **_kw: ids)
    monkeypatch.setattr(
        sd,
        "_query_collection_group",
        lambda db, coll, workspace_filter=None, limit=1000: asked.append(coll) or [],
    )
    sd._get_workspace_datasets(object(), {"public"})
    assert sorted(asked) == ["datasets", "property_graphs", "views"]


def test_service_wide_metadata_lists_graphs_too(monkeypatch):
    """Its EntityType has to exist wherever the service document names its
    EntitySet, or the two documents disagree about what is there."""
    asked = []
    monkeypatch.setattr(
        metadata,
        "_query_collection_group",
        lambda db, coll, workspace_filter=None, limit=1000: asked.append(coll) or [],
    )
    metadata._get_workspace_datasets(object(), {"public"})
    assert sorted(asked) == ["datasets", "property_graphs", "views"]


def test_service_wide_metadata_describes_a_graph_without_a_read():
    """No schema document backs a graph: its columns are the fixed structure
    columns, and its last-modified is the definition's own timestamp."""

    class NoReads:
        def get_all(self, *args, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("a property graph must cost no schema read")

    graph = metadata.DatasetInfo(
        resource="public.sky.orbits",
        workspace="public",
        collection="sky",
        name="orbits",
        source="PropertyGraph",
        last_modified_ms=GRAPH["timestamp-ms"],
    )
    facts = metadata._facts_for(NoReads(), [graph])["public.sky.orbits"]
    assert facts.columns == PROPERTY_GRAPH_STRUCTURE_COLUMNS
    assert facts.last_modified_ms == GRAPH["timestamp-ms"]


# ------------------------------------------------------- per-dataset $metadata


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


class TaskNotFound(Exception):
    """Named as the catalog's own: `_task_record` tells "not a task" from a
    broken lookup by the class name."""


class PropertyGraphNotFound(Exception):
    """Likewise for `_property_graph_record`."""


@pytest.fixture
def catalog(monkeypatch):
    calls = []

    def _install(*, graph=None):
        class FakeCatalog:
            def __init__(self, *args, **kwargs):
                pass

            def load_dataset(self, identifier):
                calls.append("load_dataset")
                raise Exception("no such dataset")

            def load_view(self, identifier):
                calls.append("load_view")
                raise Exception("no such view")

            def get_task(self, identifier):
                calls.append("get_task")
                raise TaskNotFound(identifier)

            def load_property_graph(self, identifier):
                calls.append("load_property_graph")
                if graph is None:
                    raise PropertyGraphNotFound(identifier)
                return dict(graph)

        monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        monkeypatch.setattr(interface, "_declared_relationships", lambda *a, **kw: [])
        monkeypatch.setattr(interface, "_inbound_relationships", lambda *a, **kw: [])
        monkeypatch.setattr(interface, "clear_metadata_cache", lambda: None)
        interface.clear_metadata_cache()
        return calls

    return _install


def _render():
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream="public", collection="sky", dataset="orbits"
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode(), response


def _properties(content):
    return [e.get("Name") for e in ET.fromstring(content).iter(f"{{{EDM}}}Property")]


def _annotations(content):
    return {
        e.get("Term"): (e.get("String") or e.get("Int") or e.get("Bool"))
        for e in ET.fromstring(content).iter(f"{{{EDM}}}Annotation")
    }


def test_a_graph_is_found_only_after_everything_else_misses(catalog):
    """The last rung: a table, view or task never pays for the graph lookup."""
    calls = catalog(graph=GRAPH)
    _render()
    assert calls == ["load_dataset", "load_view", "get_task", "load_property_graph"]


def test_the_columns_are_the_graph_structure(catalog):
    catalog(graph=GRAPH)
    content, response = _render()
    assert _properties(content) == [c.name for c in PROPERTY_GRAPH_STRUCTURE_COLUMNS]
    assert response.headers["X-Dataset-Source"] == "PropertyGraph"


def test_the_statement_and_description_are_annotated(catalog):
    catalog(graph=GRAPH)
    annotations = _annotations(_render()[0])
    assert annotations["Custom.Definition"] == STATEMENT
    assert annotations["Custom.Description"] == GRAPH["description"]
    assert annotations["Custom.PropertyGraph.Author"] == "justin"
    assert annotations["Custom.SourceType"] == "PropertyGraph"


def test_a_graph_with_no_description_still_gets_one(catalog):
    catalog(graph={**GRAPH, "description": None})
    assert _annotations(_render()[0])["Custom.Description"] == PROPERTY_GRAPH_DESCRIPTION


def test_a_graph_is_not_dressed_as_a_table(catalog):
    catalog(graph=GRAPH)
    annotations = _annotations(_render()[0])
    for term in (
        "Custom.Snapshot.TotalRecords",
        "Custom.SortOrder.Column",
        "Custom.Tags.Count",
        "Custom.MaterializedView.Materialized",
    ):
        assert term not in annotations, f"{term} must not be emitted for a property graph"


def test_a_name_that_is_nothing_still_404s(catalog):
    catalog(graph=None)
    with pytest.raises(HTTPException) as raised:
        _render()
    assert raised.value.status_code == 404
