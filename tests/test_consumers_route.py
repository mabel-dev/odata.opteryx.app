"""`$consumers`: who read a dataset, and what a row may say about them.

The catalog's answer is the whole database's (see opteryx_catalog.consumers,
"NOTHING HERE IS AUTHORIZED"), so the route owns every authorization decision
about it: READ on the target to ask at all, and a read check per consumer
that elides the name and keeps the row - that something downstream exists is
not the secret, its name is.
"""

import json

import pytest
from fastapi import HTTPException, Response

from opteryx_catalog import consumers as catalog_consumers

from app.routes.v4 import interface

TARGET = "ops.ingest.stdout_log"

READER = [{"pattern": "ops.*", "role": "reader"}]
READER_BOTH = [
    {"pattern": "ops.*", "role": "reader"},
    {"pattern": "platform.*", "role": "reader"},
]


def _request(policies, identity="test-user"):
    request = type("R", (), {})()
    request.state = type("S", (), {})()
    request.state.claims = {"identity": identity, "policies": policies}
    request.headers = {}
    return request


def _consumer(**overrides):
    row = {
        "source": TARGET,
        "dataset": "platform.billing.events",
        "workspace": "platform",
        "snapshot_id": 1788901112000,
        "committed_at_ms": 1788901112000,
        "produced_by": "task:platform.billing.ingest",
        "source_snapshot_ids": [1788815763764],
        "resolved_by": ["current"],
    }
    row.update(overrides)
    return row


@pytest.fixture
def catalog(monkeypatch):
    """The catalog's answer, fixed per test, and the question it was asked.
    Firestore is stubbed to a sentinel: the route only passes the client through."""
    state = {"rows": [], "asked": []}
    monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: object())

    def _find(client, source, snapshot_id=None):
        state["asked"].append((source, snapshot_id))
        return list(state["rows"])

    monkeypatch.setattr(catalog_consumers, "find_consumers", _find)
    return state


def _call(policies, workstream="ops", target=TARGET, snapshot=None):
    result = interface.consumers(
        _request(policies), Response(), workstream=workstream, target=target, snapshot=snapshot
    )
    return json.loads(result.body.decode())


def test_a_consumer_in_a_readable_workspace_is_named(catalog):
    catalog["rows"].append(_consumer())

    body = _call(READER_BOTH)

    assert body["target"] == TARGET
    assert body["snapshot"] is None
    assert body["value"] == [
        {
            "dataset": "platform.billing.events",
            "workspace": "platform",
            "visible": True,
            "snapshot_id": 1788901112000,
            "committed_at": "2026-09-08T20:58:32Z",
            "produced_by": "task:platform.billing.ingest",
            "source_snapshot_ids": [1788815763764],
            "resolved_by": ["current"],
        }
    ]


def test_a_consumer_the_caller_cannot_read_still_comes_back_unnamed(catalog):
    """Dropping the row would say nobody reads the dataset, which is false. What
    stays is about the TARGET - which versions of it were read, and how."""
    catalog["rows"].append(_consumer())

    row = _call(READER)["value"][0]

    assert row["visible"] is False
    assert row["dataset"] is None
    assert row["workspace"] is None
    assert row["produced_by"] is None
    assert row["source_snapshot_ids"] == [1788815763764]
    assert row["resolved_by"] == ["current"]


def test_a_producer_the_caller_cannot_read_is_elided_on_its_own_name(catalog):
    """The consumer being readable does not make the task that wrote it so."""
    catalog["rows"].append(_consumer(produced_by="task:secret.pipeline.ingest"))

    row = _call(READER_BOTH)["value"][0]

    assert row["dataset"] == "platform.billing.events"
    assert row["produced_by"] is None


def test_the_snapshot_filter_is_passed_through_and_echoed(catalog):
    catalog["rows"].append(_consumer(source_snapshot_ids=[1788815763764], resolved_by=["version"]))

    body = _call(READER_BOTH, snapshot=1788815763764)

    assert catalog["asked"] == [(TARGET, 1788815763764)]
    assert body["snapshot"] == 1788815763764
    assert body["value"][0]["resolved_by"] == ["version"]


def test_without_a_snapshot_the_whole_dataset_is_asked_about(catalog):
    _call(READER)

    assert catalog["asked"] == [(TARGET, None)]


def test_a_caller_without_read_on_the_target_is_refused(catalog):
    catalog["rows"].append(_consumer())

    with pytest.raises(HTTPException) as raised:
        _call([{"pattern": "platform.*", "role": "reader"}])

    assert raised.value.status_code == 403
    assert catalog["asked"] == []


@pytest.mark.parametrize("target", ["", "ingest.stdout_log", "stdout_log"])
def test_a_target_that_is_not_fully_qualified_is_refused(catalog, target):
    with pytest.raises(HTTPException) as raised:
        _call(READER, target=target)

    assert raised.value.status_code == 400


def test_a_target_in_another_workspace_than_the_path_is_refused(catalog):
    with pytest.raises(HTTPException) as raised:
        _call(READER_BOTH, workstream="ops", target="platform.billing.events")

    assert raised.value.status_code == 400


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
