"""Per-dataset `$metadata` reports the snapshot tags a dataset holds.

The Studio needs to badge a dataset that has tags, and a tag lives in a `tags`
subcollection under the dataset document -- not on the document itself, and so
not in anything the listing queries already read. This endpoint is the one that
can afford the lookup: it holds a catalog handle and already pays two
concurrent catalog calls, so a third rides along inside the same window.

What is pinned here is mostly the *absence* rules. `Custom.Tags.Count` is
emitted for a catalog-backed table even when it is 0, so a client can tell "no
tags" from "older service"; it is emitted for nothing else, so a view or an
external projection -- which cannot hold a tag at all -- never reports a
misleading 0.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import Response

from app.routes.v4 import interface

EDM = "http://docs.oasis-open.org/odata/ns/edm"


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


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
    """Enough of a loaded catalog object for the renderer to walk."""

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
    """A catalog handle whose only interesting behaviour is list_tags."""

    def __init__(self, tags, as_view=False):
        self._tags = tags
        self._as_view = as_view
        self.list_tags_calls = []

    def load_dataset(self, identifier):
        if self._as_view:
            raise KeyError(identifier)
        return FakeDataset()

    def load_view(self, identifier):
        return FakeView()

    def list_tags(self, identifier):
        self.list_tags_calls.append(identifier)
        if isinstance(self._tags, Exception):
            raise self._tags
        return self._tags


@pytest.fixture
def catalog(monkeypatch):
    """Install a catalog handle and hand the test back the instance."""

    def _install(tags, as_view=False):
        workspace = FakeWorkspace(tags, as_view=as_view)
        monkeypatch.setattr(interface, "OpteryxCatalog", lambda *a, **kw: workspace)
        # No external-catalog projection: this dataset is really ours.
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        return workspace

    return _install


def _render(collection="examples", dataset="people"):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream="public", collection=collection, dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _annotations(content):
    root = ET.fromstring(content)
    return {
        element.get("Term"): (element.get("String") or element.get("Int") or element.get("Bool"))
        for element in root.iter(f"{{{EDM}}}Annotation")
    }


def _tag(name):
    return {"name": name, "snapshot-id": 1787261893891, "created-by": "tester"}


def test_a_tagged_table_reports_its_tag_names_and_count(catalog):
    catalog([_tag("nightly"), _tag("release_v2")])

    annotations = _annotations(_render())

    assert annotations["Custom.Tags.Count"] == "2"
    # Comma-joined, in the order list_tags returned (which is by name).
    assert annotations["Custom.Tags"] == "nightly,release_v2"


def test_an_untagged_table_reports_zero_rather_than_going_quiet(catalog):
    """0 and absent must not be the same signal -- absent means older service."""
    catalog([])

    annotations = _annotations(_render())

    assert annotations["Custom.Tags.Count"] == "0"
    assert "Custom.Tags" not in annotations


def test_the_tag_lookup_asks_for_the_dataset_it_is_rendering(catalog):
    workspace = catalog([_tag("nightly")])

    _render(collection="examples", dataset="people")

    assert workspace.list_tags_calls == ["examples.people"]


def test_a_view_is_not_asked_for_tags_and_claims_no_count(catalog):
    """A view has no snapshots, so it can hold no pin -- and 0 would imply it could."""
    workspace = catalog([], as_view=True)

    annotations = _annotations(_render())

    assert workspace.list_tags_calls == []
    assert "Custom.Tags.Count" not in annotations
    assert annotations["Custom.SourceType"] == "View"


def test_an_unreadable_tag_subcollection_costs_a_badge_not_the_document(catalog):
    """An older catalog wheel has no list_tags(); the rest of the document stands."""
    catalog(AttributeError("list_tags"))

    annotations = _annotations(_render())

    assert annotations["Custom.Tags.Count"] == "0"
    assert annotations["Custom.SourceType"] == "Table"
