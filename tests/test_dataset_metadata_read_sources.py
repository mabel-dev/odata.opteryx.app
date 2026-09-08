"""Per-dataset `$metadata` reports the commit receipt of the snapshot it describes.

The receipt (`read-sources` on a snapshot document, PROVENANCE_DESIGN.md S2.1)
is the second provenance record on this endpoint and answers a different
question from the standing source list beside it: the list names what the
CURRENT content is built from, at no particular version; the receipt names the
exact `(dataset, snapshot-id, resolved-by)` triples ONE commit read.

What is pinned here is that difference -- the receipt follows the `@{label}`
selector to the snapshot the caller asked for -- along with the three-way
reading of `Reported` (nothing on record / read nothing / read these), and that
every entry is named to every caller, as `Custom.Sources` names every source.
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
    """The provenance fields `opteryx_catalog.catalog.metadata.Snapshot` carries:
    a receipt of `None` is a writer that did not report, `[]` one that read no
    catalog relation."""

    def __init__(self, snapshot_id, read_sources=None, truncated=False):
        self.snapshot_id = snapshot_id
        self.timestamp_ms = snapshot_id
        self.summary = {}
        self.read_sources = read_sources
        self.read_sources_truncated = truncated


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


def _receipt_records(content):
    """The Custom.ReadSources Collection, as a list of {property: value} dicts."""
    root = ET.fromstring(content)
    for annotation in root.iter(f"{{{EDM}}}Annotation"):
        if annotation.get("Term") != "Custom.ReadSources":
            continue
        return [
            {
                value.get("Property"): (value.get("String") or value.get("Bool"))
                for value in record.iter(f"{{{EDM}}}PropertyValue")
            }
            for record in annotation.iter(f"{{{EDM}}}Record")
        ]
    return None


def test_a_readable_receipt_reports_each_version_it_read(catalog):
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[
                {"dataset": "ops.ingest.stdout_log", "snapshot-id": 1788815763764, "resolved-by": "current"},
                {"dataset": "public.raw.feed", "snapshot-id": 1788800000000, "resolved-by": "tag"},
            ],
        )
    )

    content = _render(OPS_READER)

    assert _annotations(content)["Custom.ReadSources.Reported"] == "true"
    assert _annotations(content)["Custom.ReadSources.Truncated"] == "false"
    assert _receipt_records(content) == [
        {
            "Dataset": "ops.ingest.stdout_log",
            "SnapshotId": "1788815763764",
            "ResolvedBy": "current",
        },
        {
            "Dataset": "public.raw.feed",
            "SnapshotId": "1788800000000",
            "ResolvedBy": "tag",
        },
    ]


def test_an_entry_the_caller_cannot_read_is_named_with_its_version(catalog):
    """The version and how it was chosen go with the name: they are what
    re-deriving the commit's output needs, and a name without them says only
    that something happened."""
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[
                {"dataset": "ops.ingest.stdout_log", "snapshot-id": 1788815763764, "resolved-by": "current"},
                {"dataset": "public.raw.feed", "snapshot-id": 1788800000000, "resolved-by": "version"},
            ],
        )
    )

    assert _receipt_records(_render()) == [
        {
            "Dataset": "ops.ingest.stdout_log",
            "SnapshotId": "1788815763764",
            "ResolvedBy": "current",
        },
        {
            "Dataset": "public.raw.feed",
            "SnapshotId": "1788800000000",
            "ResolvedBy": "version",
        },
    ]


def test_a_statement_that_read_no_relation_is_not_a_missing_receipt(catalog):
    """`[]` is the honest receipt of an upload or a literal INSERT, and a client
    must not read it as "unknown"."""
    catalog(FakeSnapshot(CURRENT_ID, read_sources=[]))

    content = _render()

    assert _annotations(content)["Custom.ReadSources.Reported"] == "true"
    assert _receipt_records(content) is None


def test_a_commit_that_reported_nothing_is_reported_as_such(catalog):
    catalog(FakeSnapshot(CURRENT_ID, read_sources=None))

    annotations = _annotations(_render())

    assert annotations["Custom.ReadSources.Reported"] == "false"
    assert "Custom.ReadSources.Truncated" not in annotations
    assert _receipt_records(_render()) is None


def test_a_capped_receipt_says_so(catalog):
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 1, "resolved-by": "current"}],
            truncated=True,
        )
    )

    assert _annotations(_render())["Custom.ReadSources.Truncated"] == "true"


def test_a_version_selector_reports_that_versions_receipt(catalog):
    """The whole point of reading the receipt off the RESOLVED snapshot."""
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 9, "resolved-by": "current"}],
        ),
        by_id={
            OLD_ID: FakeSnapshot(
                OLD_ID,
                read_sources=[
                    {"dataset": "public.raw.older", "snapshot-id": 2, "resolved-by": "version"}
                ],
            )
        },
    )

    records = _receipt_records(_render(dataset=f"people@{OLD_ID}"))

    assert records == [
        {"Dataset": "public.raw.older", "SnapshotId": "2", "ResolvedBy": "version"}
    ]


def test_a_dataset_with_no_snapshot_claims_no_receipt(catalog):
    """Absent, not `Reported: false`: nothing has been committed, so there is no
    commit whose reporting could be missing."""
    catalog(None)

    assert "Custom.ReadSources.Reported" not in _annotations(_render())


def test_a_view_claims_no_receipt(catalog):
    catalog(None, as_view=True)

    annotations = _annotations(_render())

    assert "Custom.ReadSources.Reported" not in annotations
    assert annotations["Custom.SourceType"] == "View"


def test_a_malformed_entry_is_dropped_rather_than_rendered_as_an_elision(catalog):
    """Catalog documents are not schema-enforced, and a Record with no Dataset
    already means "a name you may not see"."""
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[
                "public.raw.feed",
                {"snapshot-id": 3, "resolved-by": "current"},
                {"dataset": "public.raw.feed", "snapshot-id": "not-a-number", "resolved-by": "current"},
            ],
        )
    )

    assert _receipt_records(_render()) == [
        {"Dataset": "public.raw.feed", "ResolvedBy": "current"}
    ]


def test_a_labeled_document_is_not_served_from_the_cache_to_an_unlabeled_read(catalog, monkeypatch):
    """`@{id}` and the bare name are different documents -- different receipt,
    different Custom.Snapshot.* -- under one dataset name."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 9, "resolved-by": "current"}],
        ),
        by_id={
            OLD_ID: FakeSnapshot(
                OLD_ID,
                read_sources=[
                    {"dataset": "public.raw.older", "snapshot-id": 2, "resolved-by": "version"}
                ],
            )
        },
    )

    current = _receipt_records(_render())
    old = _receipt_records(_render(dataset=f"people@{OLD_ID}"))

    assert current == [{"Dataset": "public.raw.feed", "SnapshotId": "9", "ResolvedBy": "current"}]
    assert old == [{"Dataset": "public.raw.older", "SnapshotId": "2", "ResolvedBy": "version"}]


def test_every_caller_is_owed_the_same_receipt(catalog, monkeypatch):
    """The two callers differ only in their grants on `ops.*`, which the receipt
    does not consult -- so the cached document serves both, and correctly."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[
                {"dataset": "ops.ingest.stdout_log", "snapshot-id": 7, "resolved-by": "current"}
            ],
        )
    )

    granted = _receipt_records(_render(OPS_READER))
    ungranted = _receipt_records(_render())

    assert granted == [{"Dataset": "ops.ingest.stdout_log", "SnapshotId": "7", "ResolvedBy": "current"}]
    assert ungranted == granted


def test_a_tag_reports_the_receipt_of_the_snapshot_it_pins(catalog):
    """`@{tag}` resolves through the tag to a snapshot id and then reads that
    snapshot's document, so the receipt is the pinned version's -- the whole
    point of asking for the tag rather than the dataset."""
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 9, "resolved-by": "current"}],
        ),
        by_id={OLD_ID: FakeSnapshot(OLD_ID, read_sources=[
            {"dataset": "public.raw.older", "snapshot-id": 2, "resolved-by": "tag"}
        ])},
        tag_map={"release_v2": OLD_ID},
    )

    assert _receipt_records(_render(dataset="people@release_v2")) == [
        {"Dataset": "public.raw.older", "SnapshotId": "2", "ResolvedBy": "tag"}
    ]


def test_previous_reports_the_receipt_of_the_earlier_version(catalog):
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 9, "resolved-by": "current"}],
        ),
        previous=FakeSnapshot(OLD_ID, read_sources=[
            {"dataset": "public.raw.older", "snapshot-id": 2, "resolved-by": "version"}
        ]),
    )

    assert _receipt_records(_render(dataset="people@previous")) == [
        {"Dataset": "public.raw.older", "SnapshotId": "2", "ResolvedBy": "version"}
    ]


def test_current_reports_the_head_receipt_explicitly(catalog):
    catalog(
        FakeSnapshot(
            CURRENT_ID,
            read_sources=[{"dataset": "public.raw.feed", "snapshot-id": 9, "resolved-by": "current"}],
        )
    )

    assert _receipt_records(_render(dataset="people@current")) == _receipt_records(_render())


def test_two_tags_on_one_dataset_report_their_own_receipts(catalog, monkeypatch):
    """One dataset, two pinned versions, two different upstream versions -- and
    with the response cache on, neither is served the other's document."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(
        FakeSnapshot(CURRENT_ID, read_sources=[]),
        by_id={
            OLD_ID: FakeSnapshot(OLD_ID, read_sources=[
                {"dataset": "public.raw.feed", "snapshot-id": 111, "resolved-by": "current"}
            ]),
            CURRENT_ID - 5: FakeSnapshot(CURRENT_ID - 5, read_sources=[
                {"dataset": "public.raw.feed", "snapshot-id": 222, "resolved-by": "current"}
            ]),
        },
        tag_map={"release_v1": OLD_ID, "release_v2": CURRENT_ID - 5},
    )

    v1 = _receipt_records(_render(dataset="people@release_v1"))
    v2 = _receipt_records(_render(dataset="people@release_v2"))

    assert v1 == [{"Dataset": "public.raw.feed", "SnapshotId": "111", "ResolvedBy": "current"}]
    assert v2 == [{"Dataset": "public.raw.feed", "SnapshotId": "222", "ResolvedBy": "current"}]


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
