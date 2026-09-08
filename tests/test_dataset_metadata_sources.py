"""Per-dataset `$metadata` reports the standing source list a dataset was built from.

`sources` lives on the dataset document (PROVENANCE_DESIGN.md S2.2) and names
datasets in OTHER workspaces, so the annotation is the first thing on this
document whose content depends on the caller's grants beyond this dataset.
What is pinned here, beside the shape, is that: a name the caller cannot read
is elided and its entry kept, and a document rendered for one caller is never
served from the cache to another who may see less.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import Response

from app.routes.v4 import interface

EDM = "http://docs.oasis-open.org/odata/ns/edm"

OPS_READER = [{"pattern": "ops.*", "role": "reader"}]


class DummyRequest:
    def __init__(self, policies=()):
        self.state = type("S", (), {})()
        self.state.claims = {"identity": "test-user", "policies": list(policies)}
        self.headers = {}


class FakeField:
    def __init__(self, field_id, name, type_, nullable=True):
        self.id = field_id
        self.name = name
        self.type = type_
        self.nullable = nullable
        self.element_type = None


class FakeSchema:
    columns = [FakeField(1, "id", "BIGINT", nullable=False), FakeField(2, "name", "VARCHAR")]


class FakeMetadata:
    dataset_type = None
    current_schema_id = 7
    sort_orders = None
    author = None
    description = None
    describer = None


class FakeDataset:
    metadata = FakeMetadata()

    def schema(self):
        return FakeSchema()

    def describe(self):
        return {}

    def snapshot(self):
        return None


class FakeView(FakeDataset):
    sql = "SELECT 1"


class FakeWorkspace:
    def __init__(self, as_view=False):
        self._as_view = as_view

    def load_dataset(self, identifier):
        if self._as_view:
            raise KeyError(identifier)
        return FakeDataset()

    def load_view(self, identifier):
        return FakeView()

    def list_tags(self, identifier):
        return []


class FakeDocument:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data or {})


class FakeFirestore:
    """The dataset document and nothing else: relationship reads stream empty,
    and the route reads `sources` off the document the way the real catalog
    stores it. Every path segment is accepted, and the one that is recorded is
    the dataset's."""

    def __init__(self, document):
        self._document = document
        self.paths = []
        self._path = []

    def collection(self, name):
        self._path.append(name)
        return self

    def collection_group(self, name):
        return self

    def document(self, name):
        self._path.append(name)
        return self

    def where(self, *args, **kwargs):
        return self

    def stream(self):
        self._path = []
        return iter([])

    def get(self):
        self.paths.append("/".join(self._path))
        self._path = []
        return FakeDocument(self._document)


@pytest.fixture
def catalog(monkeypatch):
    """Install the catalog handle and the dataset document, returning the Firestore fake."""

    def _install(document, as_view=False):
        firestore = FakeFirestore(document)
        monkeypatch.setattr(interface, "OpteryxCatalog", lambda *a, **kw: FakeWorkspace(as_view))
        monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: firestore)
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        return firestore

    return _install


def _render(policies=(), collection="examples", dataset="people"):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(policies), response, workstream="public", collection=collection, dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _annotations(content):
    root = ET.fromstring(content)
    return {
        element.get("Term"): (element.get("String") or element.get("Int") or element.get("Bool"))
        for element in root.iter(f"{{{EDM}}}Annotation")
    }


def _source_records(content):
    """The Custom.Sources Collection, as a list of {property: value} dicts."""
    root = ET.fromstring(content)
    for annotation in root.iter(f"{{{EDM}}}Annotation"):
        if annotation.get("Term") != "Custom.Sources":
            continue
        return [
            {
                value.get("Property"): (value.get("String") or value.get("Bool"))
                for value in record.iter(f"{{{EDM}}}PropertyValue")
            }
            for record in annotation.iter(f"{{{EDM}}}Record")
        ]
    return None


def test_a_readable_source_list_is_reported_in_order_with_its_completeness(catalog):
    catalog({"sources": ["ops.ingest.stdout_log", "public.raw.feed"], "sources-complete": True})

    content = _render(OPS_READER)

    assert _annotations(content)["Custom.Sources.Complete"] == "true"
    assert _source_records(content) == [
        {"Dataset": "ops.ingest.stdout_log", "Visible": "true"},
        {"Dataset": "public.raw.feed", "Visible": "true"},
    ]


def test_a_source_the_caller_cannot_read_keeps_its_place_unnamed(catalog):
    """The list's length is a fact about the dataset; the name is what is withheld."""
    catalog({"sources": ["ops.ingest.stdout_log", "public.raw.feed"], "sources-complete": True})

    records = _source_records(_render())

    assert records == [
        {"Visible": "false"},
        {"Dataset": "public.raw.feed", "Visible": "true"},
    ]


def test_a_document_rendered_for_one_caller_is_not_served_to_one_who_sees_less(catalog):
    """Both callers hold the same public-read on THIS dataset, which is all the
    cache key used to carry; the first's document would have named the second
    a source they hold no grant on."""
    catalog({"sources": ["ops.ingest.stdout_log"], "sources-complete": True})

    named = _source_records(_render(OPS_READER))
    elided = _source_records(_render())

    assert named == [{"Dataset": "ops.ingest.stdout_log", "Visible": "true"}]
    assert elided == [{"Visible": "false"}]


def test_an_empty_list_reports_completeness_and_no_collection(catalog):
    """Complete over nothing is a real state - a table that was truncated, or
    loaded from VALUES - and it is distinct from "older service", which emits
    neither annotation."""
    catalog({"sources": [], "sources-complete": True})

    content = _render()

    assert _annotations(content)["Custom.Sources.Complete"] == "true"
    assert _source_records(content) is None


def test_a_pre_feature_document_is_an_incomplete_empty_list(catalog):
    """A document without the field is history nobody recorded, which is the
    catalog's own reading of it."""
    catalog({"format-version": 2})

    annotations = _annotations(_render())

    assert annotations["Custom.Sources.Complete"] == "false"
    assert _source_records(_render()) is None


def test_the_lookup_reads_the_dataset_document_it_is_rendering(catalog):
    firestore = catalog({"sources": [], "sources-complete": True})

    _render(collection="examples", dataset="people")

    assert "public/examples/datasets/people" in firestore.paths


def test_a_view_claims_no_source_list(catalog):
    """A view holds SQL and no snapshots: nothing builds it, so it has no list,
    and an annotation would claim it could."""
    catalog(None, as_view=True)

    annotations = _annotations(_render())

    assert "Custom.Sources.Complete" not in annotations
    assert annotations["Custom.SourceType"] == "View"


def test_an_unreadable_document_costs_the_list_not_the_document(catalog, monkeypatch):
    catalog({"sources": ["public.raw.feed"], "sources-complete": True})

    class Broken:
        def collection(self, name):
            raise RuntimeError("firestore unavailable")

        def collection_group(self, name):
            raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: Broken())

    annotations = _annotations(_render())

    assert annotations["Custom.Sources.Complete"] == "false"
    assert annotations["Custom.SourceType"] == "Table"


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
