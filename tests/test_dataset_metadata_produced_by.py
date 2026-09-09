"""Per-dataset `$metadata` reports WHAT MADE the snapshot it describes.

`produced-by` on a snapshot document (PROVENANCE_DESIGN.md S2.5) is the link
between the two provenance layers: the receipt says what a commit READ, the
task declaration says what a task reads and writes, and nothing else on the
snapshot says which registered thing produced it.

It belongs to the `Custom.Snapshot.*` family and not `Custom.ReadSources.*`
because it is a fact about the snapshot rather than about the sources it read
-- which also gets it the right behaviour under the `@{label}` selector, where
that family already describes the version asked for.

What is pinned here is the verbatim `kind:name` (the kind decides what the name
means, so the two do not separate), that every caller is owed the name, and
that a hand-run statement -- absent, a STATE and not a defect -- emits no
annotation at all rather than one asserting nothing.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import Response

from app.routes.v4 import interface

EDM = "http://docs.oasis-open.org/odata/ns/edm"

OPS_READER = [{"pattern": "ops.*", "role": "reader"}]

CURRENT_ID = 1788815763764
OLD_ID = 1600000000000


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


class FakeSnapshot:
    """`opteryx_catalog.catalog.metadata.Snapshot` carries `produced_by` as
    `kind:name` or None, the one provenance field where None is a state."""

    def __init__(self, snapshot_id, produced_by=None):
        self.snapshot_id = snapshot_id
        self.timestamp_ms = snapshot_id
        self.summary = {}
        self.read_sources = None
        self.read_sources_truncated = False
        self.produced_by = produced_by


class FakeDataset:
    metadata = FakeMetadata()

    def __init__(self, current, by_id=None, previous=None):
        self._current = current
        self._by_id = by_id or {}
        self._previous = previous

    def schema(self):
        return FakeSchema()

    def describe(self):
        return {}

    def snapshot(self, snapshot_id=None):
        if snapshot_id is None:
            return self._current
        return self._by_id.get(snapshot_id)

    def previous_user_snapshot(self):
        return self._previous


class FakeView:
    metadata = FakeMetadata()
    sql = "SELECT 1"

    def schema(self):
        return FakeSchema()

    def describe(self):
        return {}


class FakeWorkspace:
    def __init__(self, dataset, as_view=False, tag_map=None):
        self._dataset = dataset
        self._as_view = as_view
        self._tag_map = tag_map or {}

    def load_dataset(self, identifier):
        if self._as_view:
            raise KeyError(identifier)
        return self._dataset

    def load_view(self, identifier):
        return FakeView()

    def list_tags(self, identifier):
        return []

    def resolve_tag(self, identifier, name):
        from opteryx_catalog.exceptions import TagNotFound

        if name not in self._tag_map:
            raise TagNotFound(f"Tag not found: {name} on {identifier}")
        return self._tag_map[name]


@pytest.fixture
def catalog(monkeypatch):
    """Install a catalog handle over one snapshot (plus any historical ones),
    with the dataset document -- and so the standing source list -- absent."""

    def _install(current=None, by_id=None, as_view=False, previous=None, tag_map=None):
        dataset = FakeDataset(current, by_id, previous)
        monkeypatch.setattr(
            interface,
            "OpteryxCatalog",
            lambda *a, **kw: FakeWorkspace(dataset, as_view, tag_map),
        )
        monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: None)
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        return dataset

    return _install


def _render(policies=(), dataset="people"):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(policies), response, workstream="public", collection="examples", dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _annotations(content):
    root = ET.fromstring(content)
    return {
        element.get("Term"): (element.get("String") or element.get("Int") or element.get("Bool"))
        for element in root.iter(f"{{{EDM}}}Annotation")
    }


def test_a_task_produced_snapshot_names_the_task(catalog):
    """`task:` names a catalog object, and that name is what lets a reader pull
    the task's declared `reads` and check the receipt against it."""
    catalog(FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"))

    annotations = _annotations(_render(OPS_READER))

    assert annotations["Custom.Snapshot.ProducedBy"] == "task:opteryx.ops.billing_events_ingest"


def test_an_upload_names_the_channel_it_arrived_through(catalog):
    """The segment after the colon is scoped BY the kind: for `upload` there is
    no catalog object to name, so it is the channel -- and it is rendered
    verbatim, kind included, because only the kind says which it is."""
    catalog(FakeSnapshot(CURRENT_ID, produced_by="upload:api"))

    assert _annotations(_render())["Custom.Snapshot.ProducedBy"] == "upload:api"


def test_a_hand_run_statement_claims_no_producer(catalog):
    """Absent is the true answer, not a gap: nothing registered made this
    commit. An annotation asserting null would turn a real state into a claim
    about nothing."""
    catalog(FakeSnapshot(CURRENT_ID, produced_by=None))

    assert "Custom.Snapshot.ProducedBy" not in _annotations(_render())


def test_a_refresh_names_the_view(catalog):
    catalog(FakeSnapshot(CURRENT_ID, produced_by="view:platform.billing.events_daily"))

    assert (
        _annotations(_render())["Custom.Snapshot.ProducedBy"]
        == "view:platform.billing.events_daily"
    )


def test_the_producer_is_not_split_into_two_annotations(catalog):
    """One field, one annotation. A separate kind and name is a second thing
    that can disagree with the first, and clients would have to rejoin them to
    know what the name means anyway."""
    catalog(FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"))

    annotations = _annotations(_render())

    assert "Custom.Snapshot.ProducedByKind" not in annotations
    assert "Custom.Snapshot.ProducedByName" not in annotations


def test_a_dataset_with_no_snapshot_claims_no_producer(catalog):
    """Nothing has been committed, so there is no commit whose producer could
    be reported."""
    catalog(None)

    assert "Custom.Snapshot.ProducedBy" not in _annotations(_render())


def test_a_view_claims_no_producer(catalog):
    catalog(None, as_view=True)

    annotations = _annotations(_render())

    assert "Custom.Snapshot.ProducedBy" not in annotations
    assert annotations["Custom.SourceType"] == "View"


def test_every_caller_is_owed_the_producer_name(catalog, monkeypatch):
    """A name in provenance is a citation, not access (S4.4): the two callers
    differ only in their grants on `ops.*`, which this does not consult -- so
    the cached document serves both, and correctly."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"))

    granted = _annotations(_render(OPS_READER))["Custom.Snapshot.ProducedBy"]
    ungranted = _annotations(_render())["Custom.Snapshot.ProducedBy"]

    assert granted == "task:opteryx.ops.billing_events_ingest"
    assert ungranted == granted


def test_a_version_selector_reports_that_versions_producer(catalog):
    """Custom.Snapshot.* follows the selector, and the producer is a fact about
    the snapshot -- so `dataset@{id}` says what made THAT version."""
    catalog(
        FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"),
        by_id={OLD_ID: FakeSnapshot(OLD_ID, produced_by="upload:web")},
    )

    assert _annotations(_render(dataset=f"people@{OLD_ID}"))["Custom.Snapshot.ProducedBy"] == (
        "upload:web"
    )


def test_a_version_selector_onto_a_hand_run_version_emits_nothing(catalog):
    """The head was produced by a task; the pinned version was not. The
    selected version's state is the one reported."""
    catalog(
        FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"),
        by_id={OLD_ID: FakeSnapshot(OLD_ID, produced_by=None)},
        tag_map={"release_v1": OLD_ID},
    )

    annotations = _annotations(_render(dataset="people@release_v1"))

    assert "Custom.Snapshot.ProducedBy" not in annotations
    assert annotations["Custom.Snapshot.Id"] == str(OLD_ID)


def test_previous_reports_the_earlier_versions_producer(catalog):
    catalog(
        FakeSnapshot(CURRENT_ID, produced_by="view:platform.billing.events_daily"),
        previous=FakeSnapshot(OLD_ID, produced_by="upload:gcs-logs"),
    )

    assert _annotations(_render(dataset="people@previous"))["Custom.Snapshot.ProducedBy"] == (
        "upload:gcs-logs"
    )


def test_a_labeled_document_is_not_served_from_the_cache_to_an_unlabeled_read(catalog, monkeypatch):
    """`@{id}` and the bare name are different documents -- different producer,
    different Custom.Snapshot.* -- under one dataset name."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(
        FakeSnapshot(CURRENT_ID, produced_by="task:opteryx.ops.billing_events_ingest"),
        by_id={OLD_ID: FakeSnapshot(OLD_ID, produced_by="upload:api")},
    )

    current = _annotations(_render())["Custom.Snapshot.ProducedBy"]
    old = _annotations(_render(dataset=f"people@{OLD_ID}"))["Custom.Snapshot.ProducedBy"]

    assert current == "task:opteryx.ops.billing_events_ingest"
    assert old == "upload:api"


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
