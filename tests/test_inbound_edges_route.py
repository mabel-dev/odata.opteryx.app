"""`$inbound-edges`: the trigger graph read backwards, and what it may say.

The catalog answers this one without regard to who is asking - it is the whole
database's answer (see opteryx_catalog.inbound_edges, "NOTHING HERE IS
AUTHORIZED"), so every authorization decision about it is made here and these
tests are mostly about that: what the caller must hold to ask, and what a row is
allowed to say about a workspace they cannot see.
"""

import json

import pytest
from fastapi import HTTPException, Response

from opteryx_catalog import inbound_edges as catalog_inbound_edges

from app.routes.v4 import interface

TARGET = "platform.billing.events"


def _request(policies, identity="test-user"):
    request = type("R", (), {})()
    request.state = type("S", (), {})()
    request.state.claims = {"identity": identity, "policies": policies}
    request.headers = {}
    return request


def _edge(**overrides):
    row = {
        "target": TARGET,
        "source": "ops.ingest.stdout_log",
        "workspace": "ops",
        "source_kind": "dataset",
        "kind": "materialized_view_refresh",
        "trigger": "mvrefresh_events_7c29",
        "runs_as": "federator",
        "last_fired_at_ms": 1788516990288,
        "last_fired_status": "enqueued",
        "suspended_at_ms": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def catalog(monkeypatch):
    """The catalog's answer, fixed per test. Firestore is stubbed to a sentinel:
    the route only passes the client through."""
    rows = []
    monkeypatch.setattr(interface, "_get_firestore_client", lambda *a, **kw: object())
    monkeypatch.setattr(
        catalog_inbound_edges, "find_inbound_edges", lambda client, target: list(rows)
    )
    return rows


def _call(policies, workstream="platform", target=TARGET, identity="test-user"):
    result = interface.inbound_edges(
        _request(policies, identity), Response(), workstream=workstream, target=target
    )
    return json.loads(result.body.decode())


READER = [{"pattern": "platform.*", "role": "reader"}]
READER_BOTH = [
    {"pattern": "platform.*", "role": "reader"},
    {"pattern": "ops.*", "role": "reader"},
]


def test_an_edge_from_a_readable_workspace_is_named(catalog):
    catalog.append(_edge())

    body = _call(READER_BOTH)

    assert body["target"] == TARGET
    assert body["value"] == [
        {
            "target": TARGET,
            "source": "ops.ingest.stdout_log",
            "workspace": "ops",
            "source_kind": "dataset",
            "visible": True,
            "kind": "materialized_view_refresh",
            "trigger": "mvrefresh_events_7c29",
            "runs_as": "federator",
            "last_fired_at": "2026-09-04T10:16:30.288000Z",
            "last_fired_status": "enqueued",
            "suspended": False,
        }
    ]


def test_an_edge_the_caller_cannot_read_still_comes_back_unnamed(catalog):
    """That something upstream exists is not the secret; its name is. Dropping
    the row would tell the reader the dataset has no upstream, which is a
    different - and false - answer."""
    catalog.append(_edge())

    row = _call(READER)["value"][0]

    assert row["visible"] is False
    assert row["source"] is None
    assert row["workspace"] is None
    assert row["trigger"] is None
    assert row["runs_as"] is None
    assert row["last_fired_status"] is None
    # The KIND of edge survives: it describes the target's own dependency, not
    # the workspace the row came from.
    assert row["kind"] == "materialized_view_refresh"


def test_a_task_that_writes_the_target_is_reported_as_a_writes_edge(catalog):
    catalog.append(
        _edge(
            source="ops.ingest.billing_events_ingest",
            source_kind="task",
            kind="writes",
            trigger=None,
            runs_as=None,
            last_fired_at_ms=None,
            last_fired_status=None,
        )
    )

    row = _call(READER_BOTH)["value"][0]

    assert row["kind"] == "writes"
    assert row["source_kind"] == "task"
    assert row["last_fired_at"] is None


def test_a_caller_without_read_on_the_target_is_refused(catalog):
    catalog.append(_edge())

    with pytest.raises(HTTPException) as raised:
        _call([{"pattern": "ops.*", "role": "reader"}])

    assert raised.value.status_code == 403


@pytest.mark.parametrize("target", ["", "billing.events", "events"])
def test_a_target_that_is_not_fully_qualified_is_refused(catalog, target):
    with pytest.raises(HTTPException) as raised:
        _call(READER, target=target)

    assert raised.value.status_code == 400


def test_a_target_in_another_workspace_than_the_path_is_refused(catalog):
    """The path is what the read check was made against, so answering about a
    dataset elsewhere would guard one relation with a check on another."""
    with pytest.raises(HTTPException) as raised:
        _call(READER_BOTH, workstream="platform", target="ops.ingest.stdout_log")

    assert raised.value.status_code == 400


def test_a_suspended_trigger_says_so(catalog):
    catalog.append(_edge(suspended_at_ms=1788516990288))

    assert _call(READER_BOTH)["value"][0]["suspended"] is True


def test_public_is_readable_by_anyone_and_personal_only_by_its_owner(catalog):
    catalog.append(_edge(source="public.open.feed", workspace="public"))

    assert _call(READER + [{"pattern": "public.*", "role": "reader"}])["value"][0][
        "visible"
    ] is True

    catalog[:] = [_edge(source="personal.someone_else.scratch", workspace="personal")]
    assert _call(READER)["value"][0]["visible"] is False

    catalog[:] = [_edge(source="personal.test-user.scratch", workspace="personal")]
    assert _call(READER)["value"][0]["visible"] is True


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
