"""Per-dataset `$metadata` names the vector indexes a dataset carries.

The Studio marks an indexed column in its details panel. An index definition
lives in the dataset's `indexes` subcollection, so -- like tags -- this
endpoint reads it concurrently with the schema and describe calls. Only the
definition is sent; coverage of the head snapshot needs the manifest and is
SHOW INDEXES FROM's job.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import Response

from app.routes.v4 import interface
from tests.test_dataset_metadata_snapshot_tags import DummyRequest, FakeDataset, FakeView

EDM = "http://docs.oasis-open.org/odata/ns/edm"


class FakeWorkspace:
    def __init__(self, indexes, as_view=False, has_lister=True):
        self._indexes = indexes
        self._as_view = as_view
        self.list_calls = []
        if not has_lister:
            self.list_vector_indexes = None

    def load_dataset(self, identifier):
        if self._as_view:
            raise KeyError(identifier)
        return FakeDataset()

    def load_view(self, identifier):
        return FakeView()

    def list_tags(self, identifier):
        return []

    def list_vector_indexes(self, identifier):
        self.list_calls.append(identifier)
        if isinstance(self._indexes, Exception):
            raise self._indexes
        return self._indexes


@pytest.fixture
def catalog(monkeypatch):
    def _install(indexes, **kwargs):
        workspace = FakeWorkspace(indexes, **kwargs)
        monkeypatch.setattr(interface, "OpteryxCatalog", lambda *a, **kw: workspace)
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        return workspace

    return _install


def _render():
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream="public", collection="examples", dataset="people"
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _index_records(content):
    root = ET.fromstring(content)
    for annotation in root.iter(f"{{{EDM}}}Annotation"):
        if annotation.get("Term") != "Custom.VectorIndexes":
            continue
        return [
            {value.get("Property"): value.get("String") for value in record.iter(f"{{{EDM}}}PropertyValue")}
            for record in annotation.iter(f"{{{EDM}}}Record")
        ]
    return None


def _index(name, column, build="async"):
    return {
        "index-id": "0" * 32,
        "name": name,
        "column": column,
        "method": "ivf",
        "metric": "cosine",
        "build": build,
        "clusters": 0,
    }


def test_an_indexed_table_reports_a_record_per_index(catalog):
    workspace = catalog([_index("name_idx", "name", build="sync")])

    assert _index_records(_render()) == [
        {"Name": "name_idx", "Column": "name", "Method": "ivf", "Build": "sync"}
    ]
    assert workspace.list_calls == ["examples.people"]


def test_no_indexes_means_no_annotation(catalog):
    catalog([])

    assert _index_records(_render()) is None


def test_a_definition_without_a_column_is_dropped(catalog):
    catalog([{"name": "broken"}, _index("name_idx", "name")])

    assert [r["Name"] for r in _index_records(_render())] == ["name_idx"]


def test_a_view_is_not_asked_for_indexes(catalog):
    workspace = catalog([_index("name_idx", "name")], as_view=True)

    assert _index_records(_render()) is None
    assert workspace.list_calls == []


def test_an_older_catalog_wheel_costs_the_annotation_not_the_document(catalog):
    catalog([], has_lister=False)

    assert _index_records(_render()) is None


def test_a_failed_read_costs_the_annotation_not_the_document(catalog):
    catalog(RuntimeError("firestore down"))

    content = _render()

    assert _index_records(content) is None
    assert "Custom.SourceType" in content
