"""`Custom.Consumers` -- who READ the version this `$metadata` document describes.

The third provenance record on this endpoint, and the only one that does not
look upwards: `Custom.Sources` says what the current content was built from,
`Custom.ReadSources` says what THIS version's commit read, and this says whose
commits have read THIS version. That last one is the taint question -- not "who
uses this table" but "who consumed the bad version" -- which is why it lives on
the version's own document and follows the same `@{label}` selector.

It is on the metadata document rather than behind a route of its own because
this document is Studio's: opened a handful of times a session rather than
polled, and carrying no validator for a cross-dataset answer to churn.

What is pinned here: it follows the selected version; consumers are named in
full whatever the caller's grants with `Visible` saying who can follow them; an
answer that could not be looked up is OMITTED, never sent as an empty list.
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
    def __init__(self, field_id, name, type_):
        self.id = field_id
        self.name = name
        self.type = type_
        self.nullable = True
        self.element_type = None


class FakeSchema:
    columns = [FakeField(1, "id", "BIGINT")]


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
        self.timestamp_ms = snapshot_id
        self.summary = {}
        self.read_sources = None
        self.read_sources_truncated = False


class FakeDataset:
    metadata = FakeMetadata()

    def __init__(self, current, by_id=None):
        self._current = current
        self._by_id = by_id or {}

    def schema(self):
        return FakeSchema()

    def describe(self):
        return {}

    def snapshot(self, snapshot_id=None):
        if snapshot_id is None:
            return self._current
        return self._by_id.get(snapshot_id)

    def previous_user_snapshot(self):
        return None


class FakeWorkspace:
    def __init__(self, dataset):
        self._dataset = dataset

    def load_dataset(self, identifier):
        return self._dataset

    def load_view(self, identifier):
        raise RuntimeError("not a view")

    def list_tags(self, identifier):
        return []


def _row(name, commits=1, latest=999, produced_by=None):
    return {
        "dataset": name,
        "workspace": name.split(".")[0],
        "commits": commits,
        "latest_snapshot_id": latest,
        "latest_committed_at_ms": latest,
        "produced_by": produced_by,
        "versions_read": [CURRENT_ID],
    }


@pytest.fixture
def catalog(monkeypatch):
    """A dataset with one current snapshot, plus a fake `find_readers` whose
    calls are recorded so the version it was asked about can be asserted."""
    calls = []

    def _install(answer=None, by_id=None, fail=False):
        current = FakeSnapshot(CURRENT_ID)
        dataset = FakeDataset(current, by_id)
        monkeypatch.setattr(interface, "OpteryxCatalog", lambda *a, **kw: FakeWorkspace(dataset))
        monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: object())
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        monkeypatch.setattr(interface, "_standing_sources", lambda *a, **kw: ([], False))

        import opteryx_catalog.impact as impact_module

        def _find_readers(client, target, snapshot_id=None, limit=None):
            calls.append((target, snapshot_id, limit))
            if fail:
                raise RuntimeError("firestore is having a day")
            return answer or {"dataset": target, "consumers": [], "truncated": False}

        monkeypatch.setattr(impact_module, "find_readers", _find_readers)
        return calls

    return _install


def _render(policies=(), dataset="people"):
    result = interface.dataset_metadata(
        DummyRequest(policies),
        Response(),
        workstream="public",
        collection="examples",
        dataset=dataset,
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode()


def _terms(content):
    root = ET.fromstring(content)
    return {
        a.get("Term"): (a.get("String") or a.get("Bool"))
        for a in root.iter(f"{{{EDM}}}Annotation")
    }


def _consumer_records(content):
    root = ET.fromstring(content)
    for annotation in root.iter(f"{{{EDM}}}Annotation"):
        if annotation.get("Term") != "Custom.Consumers":
            continue
        return [
            {
                value.get("Property"): (
                    value.get("String") or value.get("Bool") or value.get("Int")
                )
                for value in record.iter(f"{{{EDM}}}PropertyValue")
            }
            for record in annotation.iter(f"{{{EDM}}}Record")
        ]
    return None


def test_the_consumers_of_the_version_on_the_document(catalog):
    calls = catalog(
        {
            "dataset": "public.examples.people",
            "truncated": False,
            "consumers": [_row("ops.marts.daily", commits=3, latest=42)],
        }
    )

    content = _render(OPS_READER)

    # Asked about the version this document describes, not the dataset.
    assert calls == [("public.examples.people", CURRENT_ID, 64)]
    assert _consumer_records(content) == [
        {
            "Dataset": "ops.marts.daily",
            "Visible": "true",
            "SnapshotId": "42",
            "Commits": "3",
        }
    ]
    assert _terms(content)["Custom.Consumers.Truncated"] == "false"


def test_a_consumer_the_caller_cannot_read_is_named_anyway(catalog):
    """The reason this record exists at all: an impact answer that will not say
    who is affected does not answer the question. The flag marks it; it does
    not remove it."""
    catalog(
        {
            "dataset": "public.examples.people",
            "truncated": False,
            "consumers": [_row("ops.marts.daily"), _row("sales.marts.revenue")],
        }
    )

    records = _consumer_records(_render(OPS_READER))

    assert [(r["Dataset"], r["Visible"]) for r in records] == [
        ("ops.marts.daily", "true"),
        ("sales.marts.revenue", "false"),
    ]


def test_the_producer_rides_along_when_there_is_one(catalog):
    catalog(
        {
            "dataset": "public.examples.people",
            "truncated": False,
            "consumers": [_row("ops.marts.daily", produced_by="view:ops.marts.daily")],
        }
    )

    assert _consumer_records(_render(OPS_READER))[0]["ProducedBy"] == "view:ops.marts.daily"


def test_an_older_version_is_asked_about_itself(catalog):
    """`dataset@{id}` reports who read THAT version. Without this the taint
    question cannot be asked at all -- the head's consumers are not the bad
    version's consumers."""
    calls = catalog(by_id={OLD_ID: FakeSnapshot(OLD_ID)})

    _render(OPS_READER, dataset=f"people@{OLD_ID}")

    assert calls == [("public.examples.people", OLD_ID, 64)]


def test_nobody_read_it_is_a_real_answer(catalog):
    """An empty list WITH the flag beside it: nothing read this version. Said,
    not omitted -- it is the answer somebody about to drop a table wants."""
    catalog({"dataset": "public.examples.people", "truncated": False, "consumers": []})

    content = _render(OPS_READER)

    assert _terms(content)["Custom.Consumers.Truncated"] == "false"
    assert _consumer_records(content) is None


def test_a_failed_lookup_is_omitted_not_reported_as_empty(catalog):
    """"We could not look" and "nothing read this" are different facts, and
    only one of them is safe to act on. The flag's ABSENCE is what separates
    them, so a failure must take the flag with it."""
    catalog(fail=True)

    terms = _terms(_render(OPS_READER))

    assert "Custom.Consumers.Truncated" not in terms
    assert "Custom.Consumers" not in terms


def test_the_cap_is_reported(catalog):
    catalog(
        {
            "dataset": "public.examples.people",
            "truncated": True,
            "consumers": [_row("ops.marts.daily")],
        }
    )

    assert _terms(_render(OPS_READER))["Custom.Consumers.Truncated"] == "true"


def test_two_callers_do_not_share_each_others_flags(catalog, monkeypatch):
    """The document is caller-dependent now, and the response cache must know
    it: `_policy_fingerprint` is in the key for exactly this."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    catalog(
        {
            "dataset": "public.examples.people",
            "truncated": False,
            "consumers": [_row("ops.marts.daily")],
        }
    )

    granted = _consumer_records(_render(OPS_READER))
    stranger = _consumer_records(_render())

    assert granted[0]["Dataset"] == stranger[0]["Dataset"] == "ops.marts.daily"
    assert granted[0]["Visible"] == "true"
    assert stranger[0]["Visible"] == "false"
