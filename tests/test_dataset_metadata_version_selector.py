"""Per-dataset `$metadata` honours a `@{label}` version selector on the dataset segment.

`dataset_metadata` already loads the catalog dataset object directly and checks
the caller's permission before touching it (unlike `read_dataset`, which
delegates entirely to the query engine), so it resolves `@current` / `@previous`
/ `@{tag}` / `@{snapshot_id}` itself, via `_resolve_labeled_snapshot`, right
after that check and before the unconditional `ds.snapshot()` call the bare
(unlabeled) path still uses unchanged.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import HTTPException
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


class FakeSnapshot:
    def __init__(self, snapshot_id):
        self.snapshot_id = snapshot_id


class FakeDataset:
    """A loaded catalog object whose `snapshot`/`previous_user_snapshot` behave
    like `opteryx_catalog.catalog.dataset.SimpleDataset`'s real ones."""

    metadata = FakeMetadata()

    def __init__(self, current_id=1700000000000, snapshots=None, previous=None):
        self._current_id = current_id
        # snapshot_id -> FakeSnapshot, for the @{snapshot_id} path
        self._snapshots = snapshots or {}
        self._previous = previous

    def schema(self):
        return FakeSchema()

    def describe(self):
        return {}

    def snapshot(self, snapshot_id=None):
        if snapshot_id is None:
            return FakeSnapshot(self._current_id)
        return self._snapshots.get(snapshot_id)

    def previous_user_snapshot(self):
        return self._previous


class FakeWorkspace:
    """A catalog handle backing both load_dataset and tag resolution."""

    def __init__(self, tags=None, tag_map=None):
        self._tags = tags or []
        self._tag_map = tag_map or {}  # tag name -> snapshot_id
        self.resolve_tag_calls = []

    def load_dataset(self, identifier):
        return FakeDataset(
            snapshots={1600000000000: FakeSnapshot(1600000000000)},
            previous=FakeSnapshot(1600000000000),
        )

    def load_view(self, identifier):
        raise KeyError(identifier)

    def list_tags(self, identifier):
        return self._tags

    def resolve_tag(self, identifier, name):
        self.resolve_tag_calls.append((identifier, name))
        from opteryx_catalog.exceptions import TagNotFound

        if name not in self._tag_map:
            raise TagNotFound(f"Tag not found: {name} on {identifier}")
        return self._tag_map[name]


@pytest.fixture
def catalog(monkeypatch):
    def _install(tags=None, tag_map=None):
        workspace = FakeWorkspace(tags=tags, tag_map=tag_map)
        monkeypatch.setattr(interface, "OpteryxCatalog", lambda *a, **kw: workspace)
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        return workspace

    return _install


def _render(dataset):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream="public", collection="examples", dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _snapshot_id_annotation(content):
    root = ET.fromstring(content)
    for element in root.iter(f"{{{EDM}}}Annotation"):
        if element.get("Term") == "Custom.Snapshot.Id":
            return element.get("String") or element.get("Int")
    return None


def test_bare_dataset_reads_current_unchanged(catalog):
    catalog()

    content = _render("people")

    assert _snapshot_id_annotation(content) == "1700000000000"


def test_explicit_current_label(catalog):
    catalog()

    content = _render("people@current")

    assert _snapshot_id_annotation(content) == "1700000000000"


def test_previous_label_resolves_to_previous_snapshot(catalog):
    catalog()

    content = _render("people@previous")

    assert _snapshot_id_annotation(content) == "1600000000000"


def test_previous_label_404s_when_there_is_no_previous_version(catalog, monkeypatch):
    workspace = catalog()
    monkeypatch.setattr(
        workspace, "load_dataset", lambda identifier: FakeDataset(previous=None)
    )

    with pytest.raises(HTTPException) as exc_info:
        _render("people@previous")

    assert exc_info.value.status_code == 404


def test_numeric_label_resolves_to_that_snapshot(catalog):
    catalog()

    content = _render("people@1600000000000")

    assert _snapshot_id_annotation(content) == "1600000000000"


def test_numeric_label_404s_when_snapshot_is_unknown(catalog):
    catalog()

    with pytest.raises(HTTPException) as exc_info:
        _render("people@999")

    assert exc_info.value.status_code == 404


def test_tag_label_resolves_via_resolve_tag(catalog):
    workspace = catalog(tag_map={"release_v2": 1600000000000})

    content = _render("people@release_v2")

    assert _snapshot_id_annotation(content) == "1600000000000"
    assert workspace.resolve_tag_calls == [("examples.people", "release_v2")]


def test_unknown_tag_label_404s(catalog):
    catalog(tag_map={})

    with pytest.raises(HTTPException) as exc_info:
        _render("people@bogus_tag")

    assert exc_info.value.status_code == 404


def test_malformed_label_with_no_dataset_name_400s(catalog):
    catalog()

    with pytest.raises(HTTPException) as exc_info:
        _render("@current")

    assert exc_info.value.status_code == 400


def test_malformed_label_with_no_label_400s(catalog):
    catalog()

    with pytest.raises(HTTPException) as exc_info:
        _render("people@")

    assert exc_info.value.status_code == 400
