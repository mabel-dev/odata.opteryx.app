"""Per-dataset `$metadata` for a workspace bound to an external catalog.

There are TWO `$metadata` implementations, and they resolve a dataset's columns
by different routes:

  metadata.py   service-wide `/$metadata`, built straight from the catalog
                documents -- reads a stub's inline `schema`.
  interface.py  per-dataset `/{ws}/{collection}/{dataset}/$metadata`, which the
                Studio's column view calls -- went through
                `OpteryxCatalog.load_dataset()` and asked the loaded dataset for
                its schema, resolved via `current-schema-id` and a `schemas`
                subcollection.

A stub has neither of those, so bound workspaces listed fine and then 404ed
here: the dataset tree showed the table and its column view was empty. These
tests pin the fix, and the last one pins the two implementations against the
same document so they cannot drift apart again.
"""

import xml.etree.ElementTree as ET

from fastapi import Response

import pytest

EDM = "http://docs.oasis-open.org/odata/ns/edm"

from app.routes.v4 import interface
from app.routes.v4 import metadata as md


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


STUB = {
    "workspace": "public",
    "collection": "interop_ns",
    "name": "people",
    "external-catalog": True,
    "schema": [
        {"id": 1, "name": "id", "type": "BIGINT", "nullable": False},
        {"id": 2, "name": "name", "type": "VARCHAR", "nullable": True},
        {"id": 3, "name": "score", "type": "DOUBLE", "nullable": True},
    ],
    "statistics": {"row-count": 5, "total-file-size-bytes": 1742},
    "timestamp-ms": 1787261893891,
}


class FakeSnapshot:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data else None


class FakeDocument:
    def __init__(self, data):
        self._data = data

    def collection(self, _name):
        return FakeCollection(self._data)

    def get(self):
        return FakeSnapshot(self._data)


class FakeCollection:
    def __init__(self, data):
        self._data = data

    def document(self, _name):
        return FakeDocument(self._data)


class FakeFirestore:
    def __init__(self, data):
        self._data = data

    def collection(self, _name):
        return FakeCollection(self._data)


@pytest.fixture
def stubbed(monkeypatch):
    """Serve one document, and fail loudly if the catalog path is taken."""

    def _install(data):
        monkeypatch.setattr(
            interface, "_get_firestore_client", lambda database=None, **kw: FakeFirestore(data)
        )

        class Forbidden:
            def __init__(self, *args, **kwargs):
                raise AssertionError(
                    "a stub must be answered from its document, without a catalog handle"
                )

        monkeypatch.setattr(interface, "OpteryxCatalog", Forbidden)

    return _install


def _render(workstream="public", collection="interop_ns", dataset="people"):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream=workstream, collection=collection, dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode(), response


def _properties(content):
    """name -> (edm type, nullable) for every Property in the document.

    Parsed rather than string-matched: the renderer emits namespace-prefixed
    tags, so a literal `<Property ...>` assertion passes or fails for reasons
    that have nothing to do with the columns.
    """
    root = ET.fromstring(content)
    return {
        element.get("Name"): (element.get("Type"), element.get("Nullable"))
        for element in root.iter(f"{{{EDM}}}Property")
    }


def _annotations(content):
    root = ET.fromstring(content)
    return {
        element.get("Term"): (element.get("String") or element.get("Int") or element.get("Bool"))
        for element in root.iter(f"{{{EDM}}}Annotation")
    }


def test_stub_columns_are_served_from_the_document(stubbed):
    stubbed(STUB)
    content, response = _render()

    properties = _properties(content)
    assert properties == {
        "id": ("Edm.Int64", "false"),
        "name": ("Edm.String", "true"),
        "score": ("Edm.Double", "true"),
    }
    assert response.headers.get("X-Dataset-Source") == "ExternalTable"


def test_stub_statistics_and_last_modified_are_annotated(stubbed):
    stubbed(STUB)
    content, response = _render()

    annotations = _annotations(content)
    assert annotations["Custom.Snapshot.TotalRecords"] == "5"
    assert annotations["Custom.SourceType"] == "ExternalTable"
    assert response.headers.get("X-Dataset-LastModified") == str(STUB["timestamp-ms"])


def test_stub_sort_order_is_annotated_when_the_catalog_had_one(stubbed):
    sorted_stub = dict(
        STUB,
        **{
            "sort-orders": [
                {"order-id": 1, "fields": [{"source-id": 3, "name": "score", "direction": "desc"}]}
            ]
        },
    )
    stubbed(sorted_stub)
    content, _ = _render()
    annotations = _annotations(content)
    assert annotations["Custom.SortOrder.Column"] == "score"
    assert annotations["Custom.SortOrder.Direction"] == "desc"


def test_a_stub_with_no_projected_columns_says_so(stubbed):
    # a table that could not be opened when the list was last refreshed
    stubbed({k: v for k, v in STUB.items() if k != "schema"})
    with pytest.raises(Exception) as excinfo:
        _render()
    detail = getattr(excinfo.value, "detail", str(excinfo.value))
    assert getattr(excinfo.value, "status_code", None) == 404
    # a listing that needs refreshing is not a dataset that does not exist
    assert "refresh" in detail.lower()


def test_a_real_dataset_still_goes_through_the_catalog(monkeypatch):
    # the marker is what selects the branch; a document without it must not be
    # answered from Firestore fields
    monkeypatch.setattr(
        interface,
        "_get_firestore_client",
        lambda database=None, **kw: FakeFirestore({"name": "people", "collection": "interop_ns"}),
    )
    taken = []

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            taken.append(True)

        def load_dataset(self, path):
            raise RuntimeError("not a dataset")

        def load_view(self, path):
            raise RuntimeError("not a view")

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
    with pytest.raises(Exception):
        _render()
    assert taken, "a non-stub document must take the catalog path"


def test_an_unreadable_document_falls_through_rather_than_guessing(monkeypatch):
    class Broken:
        def collection(self, _name):
            raise RuntimeError("firestore is unavailable")

    monkeypatch.setattr(interface, "_get_firestore_client", lambda database=None, **kw: Broken())
    taken = []

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            taken.append(True)

        def load_dataset(self, path):
            raise RuntimeError("not a dataset")

        def load_view(self, path):
            raise RuntimeError("not a view")

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
    with pytest.raises(Exception):
        _render()
    # "we could not look" is not evidence of a stub
    assert taken


# ---------------------------------------------------------------------------
# The drift guard
# ---------------------------------------------------------------------------


class _MetadataDocRef:
    def __init__(self, path):
        self.path = path
        self.parent = type("P", (), {"parent": type("G", (), {"id": "interop_ns"})})

    def collection(self, _name):
        raise AssertionError("an inline schema must cost no extra read")


class _MetadataDoc:
    def __init__(self, data):
        self.id = "people"
        self._data = data
        self.reference = _MetadataDocRef("public/interop_ns/datasets/people")

    def to_dict(self):
        return dict(self._data)


class _MetadataQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, **_):
        return self

    def limit(self, _):
        return self

    def stream(self):
        return list(self._docs)


class _MetadataDB:
    def collection_group(self, name):
        return _MetadataQuery([_MetadataDoc(STUB)] if name == "datasets" else [])


def test_both_metadata_implementations_agree_on_the_same_stub(stubbed):
    """The service-wide document and the per-dataset one must describe the
    same table the same way. They resolve columns by different routes, and
    that divergence is exactly what shipped a stub visible in one and absent
    from the other."""
    stubbed(STUB)
    per_dataset, _ = _render()

    datasets = md._query_collection_group(_MetadataDB(), "datasets", workspace_filter="public")
    facts = md._facts_for(None, datasets)
    service_wide = [column.name for column in facts["public.interop_ns.people"].columns]

    assert service_wide == ["id", "name", "score"]
    assert list(_properties(per_dataset)) == service_wide

    # ...and on what KIND of thing it is. Three implementations decide this
    # from the same marker (service document, service-wide $metadata, this
    # endpoint); two of them agreeing is not enough.
    assert md._source_type_from_doc(STUB, "datasets") == "ExternalTable"
    assert _annotations(per_dataset)["Custom.SourceType"] == "ExternalTable"


# --- projected statistics -----------------------------------------------------
# This branch used to annotate no statistics at all, on the reasoning that "the
# projection deliberately does not read the external catalog's manifests". That
# became untrue when control.opteryx's workspace refresh started WRITING one --
# row counts, null counts, sizes and bounds collected from the source -- into
# the workspace's GCS metadata/ prefix and recording its path on the stub as
# `manifest-list`. Nothing read it, so a PostgreSQL-bound workspace's columns
# showed their types in the Studio and nothing else.


STUB_WITH_MANIFEST = dict(STUB, **{"manifest-list": "gs://bucket/metadata/people-stats.parquet"})

DESCRIBED = {
    "id": {"min": 1, "max": 9, "null_count": 0, "cardinality": 9, "cardinality_is_exact": True},
    # Text bounds are ordinal PREFIXES, carried in their own channel -- see
    # test_text_bound_prefix_annotations.
    "name": {"min": None, "max": None, "min_display": "Earth", "max_display": "Venus"},
    "score": {"min": 0.5, "max": 99.5, "null_count": 2},
}


def _catalog_serving(description, seen):
    """An OpteryxCatalog whose one dataset describes itself as `description`."""

    class FakeDataset:
        def describe(self):
            return description

    class FakeCatalog:
        def __init__(self, workstream, **kwargs):
            seen.append(workstream)

        def load_dataset(self, path):
            seen.append(path)
            return FakeDataset()

    return FakeCatalog


def test_a_stub_with_no_manifest_still_costs_no_catalog_handle(stubbed):
    """The promise this branch is built on. `stubbed` fails the test if a
    catalog handle is constructed, so this asserts it by passing at all."""
    stubbed(STUB)
    content, _ = _render()
    annotations = _annotations(content)
    assert "Custom.Statistics.Min" not in annotations
    assert list(_properties(content)) == ["id", "name", "score"]


def test_projected_statistics_are_annotated(stubbed, monkeypatch):
    stubbed(STUB_WITH_MANIFEST)
    seen: list = []
    monkeypatch.setattr(interface, "OpteryxCatalog", _catalog_serving(DESCRIBED, seen))

    content, _ = _render()
    root = ET.fromstring(content)
    by_column = {
        element.get("Name"): {
            annotation.get("Term"): annotation.get("String") or annotation.get("Int")
            for annotation in element.iter(f"{{{EDM}}}Annotation")
        }
        for element in root.iter(f"{{{EDM}}}Property")
    }

    assert by_column["id"]["Custom.Statistics.Min"] == "1"
    assert by_column["id"]["Custom.Statistics.Max"] == "9"
    assert by_column["score"]["Custom.Statistics.NullCount"] == "2"
    assert by_column["name"]["Custom.Statistics.MinPrefix"] == "Earth"
    assert by_column["name"]["Custom.Statistics.MaxPrefix"] == "Venus"
    # The manifest is read for THIS dataset, once.
    assert seen == ["public", "interop_ns.people"]


def test_an_unreadable_manifest_costs_the_statistics_and_nothing_else(stubbed, monkeypatch):
    """Best effort, like every other enrichment here. A refresh that wrote a
    pointer to a manifest that has since gone must not 500 the column view."""
    stubbed(STUB_WITH_MANIFEST)

    class Exploding:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(interface, "OpteryxCatalog", Exploding)

    content, _ = _render()
    assert list(_properties(content)) == ["id", "name", "score"]
    assert "Custom.Statistics.Min" not in _annotations(content)
