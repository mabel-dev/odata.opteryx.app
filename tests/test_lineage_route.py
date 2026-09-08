"""`$lineage`: the receipts of one dataset, and what each row may say.

The catalog hands back every live snapshot with its receipt whole (see
PROVENANCE_DESIGN.md S2.1 and S4.4 - stored whole, elided at every read
surface), so what is pinned here is the read surface's own work: the three
states a snapshot can be in, which are three different rows and never one;
the elision of a source name the caller holds no grant on, with the row kept;
and `source_exists`, which is the one thing a receipt cannot say about itself.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response

from app.routes.v4 import interface

TARGET = "platform.billing.events"

READER = [{"pattern": "platform.*", "role": "reader"}]
READER_BOTH = [
    {"pattern": "platform.*", "role": "reader"},
    {"pattern": "ops.*", "role": "reader"},
]


def _request(policies, identity="test-user"):
    request = type("R", (), {})()
    request.state = type("S", (), {})()
    request.state.claims = {"identity": identity, "policies": policies}
    request.headers = {}
    return request


def _snapshot(snapshot_id, read_sources=None, produced_by=None):
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        timestamp_ms=snapshot_id,
        read_sources=read_sources,
        produced_by=produced_by,
    )


def _entry(dataset, snapshot_id, resolved_by="current"):
    return {"dataset": dataset, "snapshot-id": snapshot_id, "resolved-by": resolved_by}


class DatasetNotFound(Exception):
    """The catalog's own, matched by name in the route."""


class FakeDataset:
    def __init__(self, snapshots, current_snapshot_id=None, sources=(), sources_complete=True):
        self._snapshots = list(snapshots)
        self.metadata = SimpleNamespace(
            current_snapshot_id=current_snapshot_id,
            sources=list(sources),
            sources_complete=sources_complete,
        )

    def snapshots(self):
        return list(self._snapshots)

    def snapshot(self, snapshot_id=None):
        head = self.metadata.current_snapshot_id
        if head is None:
            return self._snapshots[-1] if self._snapshots else None
        return next((s for s in self._snapshots if s.snapshot_id == head), None)


class FakeWorkspace:
    def __init__(self, datasets):
        self._datasets = datasets
        self.loads = []

    def load_dataset(self, identifier, load_history=False):
        self.loads.append((identifier, load_history))
        dataset = self._datasets.get(identifier)
        if dataset is None:
            raise DatasetNotFound(identifier)
        if isinstance(dataset, Exception):
            raise dataset
        return dataset


@pytest.fixture
def catalog(monkeypatch):
    """One fake workspace per name; a workspace nobody registered fails to open,
    which is what a bad name does against the real catalog."""
    workspaces = {}

    def _open(workstream, **kwargs):
        try:
            return workspaces[workstream]
        except KeyError as exc:
            raise RuntimeError(f"no such workspace {workstream}") from exc

    monkeypatch.setattr(interface, "OpteryxCatalog", _open)

    def _install(workstream, datasets):
        workspaces[workstream] = FakeWorkspace(datasets)
        return workspaces[workstream]

    return _install


def _call(policies, workstream="platform", target=TARGET):
    result = interface.lineage(_request(policies), Response(), workstream=workstream, target=target)
    return json.loads(result.body.decode())


def _target(catalog, *snapshots, current_snapshot_id=None, sources=(), sources_complete=True):
    return catalog(
        "platform",
        {
            "billing.events": FakeDataset(
                snapshots,
                current_snapshot_id=current_snapshot_id,
                sources=sources,
                sources_complete=sources_complete,
            )
        },
    )


def test_a_receipt_is_one_row_per_source_and_newest_snapshot_first(catalog):
    catalog("ops", {"ingest.stdout_log": FakeDataset([_snapshot(100), _snapshot(200)])})
    _target(
        catalog,
        _snapshot(1000, [_entry("ops.ingest.stdout_log", 100)]),
        _snapshot(2000, [_entry("ops.ingest.stdout_log", 200, "version")], produced_by="task:platform.billing.ingest"),
        current_snapshot_id=2000,
        sources=["ops.ingest.stdout_log"],
    )

    body = _call(READER_BOTH)

    assert body["target"] == TARGET
    assert body["sources"] == [{"dataset": "ops.ingest.stdout_log", "visible": True}]
    assert body["sources_complete"] is True
    assert body["value"] == [
        {
            "snapshot_id": 2000,
            "committed_at": "1970-01-01T00:00:02Z",
            "is_current": True,
            "produced_by": "task:platform.billing.ingest",
            "producer_visible": True,
            "source_dataset": "ops.ingest.stdout_log",
            "source_snapshot_id": 200,
            "resolved_by": "version",
            "source_exists": True,
            "recorded": True,
            "visible": True,
        },
        {
            "snapshot_id": 1000,
            "committed_at": "1970-01-01T00:00:01Z",
            "is_current": False,
            "produced_by": None,
            "producer_visible": None,
            "source_dataset": "ops.ingest.stdout_log",
            "source_snapshot_id": 100,
            "resolved_by": "current",
            "source_exists": True,
            "recorded": True,
            "visible": True,
        },
    ]


def test_the_three_states_are_three_different_rows(catalog):
    """No receipt, an empty receipt and a receipt are told apart by `recorded`
    and `visible` together - and a marker row's `visible` is null, because
    false is what an ELIDED source looks like."""
    catalog("ops", {"ingest.stdout_log": FakeDataset([_snapshot(100)])})
    _target(
        catalog,
        _snapshot(1000, None),
        _snapshot(2000, []),
        _snapshot(3000, [_entry("ops.ingest.stdout_log", 100)]),
        current_snapshot_id=3000,
    )

    rows = _call(READER_BOTH)["value"]

    assert [(r["snapshot_id"], r["recorded"], r["source_dataset"], r["visible"]) for r in rows] == [
        (3000, True, "ops.ingest.stdout_log", True),
        (2000, True, None, None),
        (1000, False, None, None),
    ]
    # A marker row has nothing to say about a source, in either state.
    for row in rows[1:]:
        assert row["source_snapshot_id"] is None
        assert row["resolved_by"] is None
        assert row["source_exists"] is None


def test_a_source_the_caller_cannot_read_is_elided_and_the_row_kept(catalog):
    """The row survives - the reader is owed "built from something you may not
    see" - but neither the name nor whether it still exists is said, since a
    true or false beside a null name still describes a dataset they hold no
    grant on. The version read and how it was chosen are the target's own facts."""
    ops = catalog("ops", {"ingest.stdout_log": FakeDataset([_snapshot(100)])})
    receipt = [_entry("ops.ingest.stdout_log", 100), _entry("platform.billing.raw", 50, "tag")]
    catalog(
        "platform",
        {
            "billing.events": FakeDataset(
                [_snapshot(1000, receipt)],
                current_snapshot_id=1000,
                sources=["ops.ingest.stdout_log", "platform.billing.raw"],
            ),
            "billing.raw": FakeDataset([_snapshot(50)]),
        },
    )

    body = _call(READER)

    assert body["sources"] == [
        {"dataset": None, "visible": False},
        {"dataset": "platform.billing.raw", "visible": True},
    ]
    elided, named = body["value"]
    assert elided["visible"] is False
    assert elided["source_dataset"] is None
    assert elided["source_exists"] is None
    assert elided["source_snapshot_id"] == 100
    assert elided["resolved_by"] == "current"
    assert elided["recorded"] is True
    assert named["source_dataset"] == "platform.billing.raw"
    assert named["visible"] is True
    assert named["source_exists"] is True
    assert named["resolved_by"] == "tag"
    # An elided source is never looked up: the caller's request must not cause
    # a read of a history they hold no grant on.
    assert ops.loads == []


def test_source_exists_is_false_for_an_expired_snapshot_and_a_dropped_source(catalog):
    """A receipt does not pin what it names (S2.1): the version can expire or
    the dataset be dropped, and both are definite answers, not failures."""
    catalog("ops", {"ingest.stdout_log": FakeDataset([_snapshot(200)])})
    _target(
        catalog,
        _snapshot(
            1000,
            [_entry("ops.ingest.stdout_log", 100), _entry("ops.ingest.gone", 300)],
        ),
        current_snapshot_id=1000,
    )

    rows = _call(READER_BOTH)["value"]

    assert [(r["source_dataset"], r["source_exists"]) for r in rows] == [
        ("ops.ingest.stdout_log", False),
        ("ops.ingest.gone", False),
    ]


def test_source_exists_is_null_when_the_source_cannot_be_read_or_had_no_commits(catalog):
    """A history load that fails is not evidence either way. A source that had
    no commits when it was read has no snapshot whose liveness could be asked."""
    catalog("ops", {"ingest.broken": RuntimeError("firestore unavailable"), "ingest.empty": FakeDataset([])})
    _target(
        catalog,
        _snapshot(
            1000,
            [_entry("ops.ingest.broken", 100), _entry("ops.ingest.empty", None)],
        ),
        current_snapshot_id=1000,
    )

    rows = _call(READER_BOTH)["value"]

    assert [(r["source_dataset"], r["source_snapshot_id"], r["source_exists"]) for r in rows] == [
        ("ops.ingest.broken", 100, None),
        ("ops.ingest.empty", None, None),
    ]


def test_a_source_history_is_loaded_once_per_answer(catalog):
    """Every snapshot of a table refreshed from the same source names it, and a
    history load per row would be a history load per snapshot."""
    ops = catalog("ops", {"ingest.stdout_log": FakeDataset([_snapshot(100), _snapshot(200)])})
    _target(
        catalog,
        _snapshot(1000, [_entry("ops.ingest.stdout_log", 100)]),
        _snapshot(2000, [_entry("ops.ingest.stdout_log", 200)]),
        _snapshot(3000, [_entry("ops.ingest.stdout_log", 200)]),
        current_snapshot_id=3000,
    )

    _call(READER_BOTH)

    assert ops.loads == [("ingest.stdout_log", True)]


def test_is_current_follows_the_head_pointer_not_the_newest_id(catalog):
    """A rollback moves the head backwards past snapshots that stay live."""
    _target(catalog, _snapshot(1000, []), _snapshot(2000, []), current_snapshot_id=1000)

    rows = _call(READER)["value"]

    assert [(r["snapshot_id"], r["is_current"]) for r in rows] == [(2000, False), (1000, True)]


def test_a_producer_the_caller_cannot_read_is_elided(catalog):
    _target(
        catalog,
        _snapshot(1000, [], produced_by="task:ops.ingest.billing_events_ingest"),
        _snapshot(2000, [], produced_by="view:platform.billing.rollup"),
        current_snapshot_id=2000,
    )

    rows = _call(READER)["value"]

    assert [(r["produced_by"], r["producer_visible"]) for r in rows] == [
        ("view:platform.billing.rollup", True),
        (None, False),
    ]


def test_an_incomplete_standing_list_says_so(catalog):
    _target(catalog, _snapshot(1000, None), current_snapshot_id=1000, sources=[], sources_complete=False)

    body = _call(READER)

    assert body["sources"] == []
    assert body["sources_complete"] is False


def test_a_caller_without_read_on_the_target_is_refused(catalog):
    _target(catalog, _snapshot(1000, []), current_snapshot_id=1000)

    with pytest.raises(HTTPException) as raised:
        _call([{"pattern": "ops.*", "role": "reader"}])

    assert raised.value.status_code == 403


@pytest.mark.parametrize("target", ["", "billing.events", "events"])
def test_a_target_that_is_not_fully_qualified_is_refused(catalog, target):
    with pytest.raises(HTTPException) as raised:
        _call(READER, target=target)

    assert raised.value.status_code == 400


def test_a_target_in_another_workspace_than_the_path_is_refused(catalog):
    with pytest.raises(HTTPException) as raised:
        _call(READER_BOTH, workstream="platform", target="ops.ingest.stdout_log")

    assert raised.value.status_code == 400


def test_a_target_the_catalog_does_not_hold_is_not_found(catalog):
    catalog("platform", {})

    with pytest.raises(HTTPException) as raised:
        _call(READER)

    assert raised.value.status_code == 404


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
