"""Per-dataset `$metadata` reports the standing source list a dataset was built from.

`sources` lives on the dataset document (PROVENANCE_DESIGN.md S2.2) and names
datasets in OTHER workspaces. Every one of them is named, to every caller: the
list is a citation list, and a citation the reader holds no subscription to is
still a citation. What is pinned here, beside the shape, is that -- the NAMES
do not depend on the caller's grants.

Beside each name is `Visible`, which does: it says whether this caller can
follow the citation. That is the whole of the caller-dependence, and it is
additive -- a client that ignores the flag still renders a complete and
truthful list.
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


def test_the_source_list_is_reported_in_order_with_its_completeness(catalog):
    catalog({"sources": ["ops.ingest.stdout_log", "sales.raw.feed"], "sources-complete": True})

    content = _render(OPS_READER)

    assert _annotations(content)["Custom.Sources.Complete"] == "true"
    assert _source_records(content) == [
        {"Dataset": "ops.ingest.stdout_log", "Visible": "true"},
        {"Dataset": "sales.raw.feed", "Visible": "false"},
    ]


def test_a_source_the_caller_cannot_read_is_named_anyway(catalog):
    """A citation, not an access grant: the caller holds nothing on `ops.*` and
    is still told what this data came from. Reading that dataset needs a grant
    like anything else -- the name carries no schema, no statistics, no rows."""
    catalog({"sources": ["ops.ingest.stdout_log", "sales.raw.feed"], "sources-complete": True})

    records = _source_records(_render())

    assert [record["Dataset"] for record in records] == [
        "ops.ingest.stdout_log",
        "sales.raw.feed",
    ]
    # Named, and marked as a name this caller cannot follow. Both of them:
    # the second is in a workspace they hold nothing on either.
    assert [record["Visible"] for record in records] == ["false", "false"]


def test_a_public_source_is_readable_without_a_policy(catalog):
    """Visibility is the same ladder the route applies to the dataset it was
    asked about (`_can_read_relation`), not a bare pattern match -- so `public`
    is readable to a caller holding no policy at all, and saying otherwise
    marks a link that works as a dead end."""
    catalog({"sources": ["public.open.reference"], "sources-complete": True})

    assert _source_records(_render()) == [
        {"Dataset": "public.open.reference", "Visible": "true"}
    ]


def test_a_personal_source_is_readable_only_by_its_owner(catalog):
    """The other rung a pattern match would miss: your own personal workspace
    is yours, and somebody else's is not."""
    catalog(
        {
            "sources": ["personal.test-user.scratch", "personal.someone-else.scratch"],
            "sources-complete": True,
        }
    )

    assert [r["Visible"] for r in _source_records(_render())] == ["true", "false"]


def test_every_caller_is_owed_the_same_names(catalog):
    """What the data was built from is a fact about the data, so the NAMES are
    the same for a caller who can read the source and one who cannot. Only the
    flag beside them differs."""
    catalog({"sources": ["ops.ingest.stdout_log"], "sources-complete": True})

    reader = _source_records(_render(OPS_READER))
    stranger = _source_records(_render())

    assert [r["Dataset"] for r in reader] == [r["Dataset"] for r in stranger]
    assert reader[0]["Visible"] == "true"
    assert stranger[0]["Visible"] == "false"


def test_visibility_follows_the_pattern_not_the_workspace(catalog):
    """The grant is matched against the whole fully-qualified name, the way the
    query path matches it. A grant on one collection does not light up a
    sibling collection in the same workspace."""
    catalog(
        {
            "sources": ["ops.ingest.stdout_log", "ops.secret.ledger"],
            "sources-complete": True,
        }
    )

    records = _source_records(_render([{"pattern": "ops.ingest.*", "role": "reader"}]))

    assert [r["Visible"] for r in records] == ["true", "false"]


def test_a_name_that_is_not_fully_qualified_is_not_claimed_readable(catalog):
    """A pattern is always three parts, so a two-part name cannot be shown to
    match one. Reported unreadable rather than assumed either way."""
    catalog({"sources": ["ingest.stdout_log"], "sources-complete": True})

    records = _source_records(_render([{"pattern": "*", "role": "reader"}]))

    assert records == [{"Dataset": "ingest.stdout_log", "Visible": "false"}]


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
