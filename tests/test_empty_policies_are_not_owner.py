"""A caller whose policies come to nothing must not become an owner.

`opteryx.session(access_policies=None)` means "owner on everything" (see
Session.__init__ in opteryx-core), so passing `None` for "this caller holds
nothing" hands them the whole catalog -- the exact opposite of the deny-all
fallback the route intends.

The list is empty rather than absent for an anonymous caller: their claims
carry no policies, and `access_policies_for_query` appends none for a caller
with no identity. Before the engine's permission checks became pluggable this
was masked by the separate anonymous-exemption gate; it should never have been
relying on that.

jobs.opteryx pins the same property for its own session
(`test_a_caller_with_no_policies_is_not_an_owner`).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routes.v4 import interface


class _Stop(Exception):
    """Raised by the spy to end the request once the session is built."""


@pytest.fixture
def captured_session(monkeypatch):
    captured = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        raise _Stop()

    monkeypatch.setattr(interface.opteryx, "session", _spy)
    monkeypatch.setattr(interface, "register_default_connector", lambda: None)
    return captured


def _run(**kwargs):
    with pytest.raises(_Stop):
        interface.execute_odata_query(table_name="testdata.planets", top=1, **kwargs)


def test_no_policies_reaches_the_engine_as_an_empty_list_not_none(captured_session):
    _run()

    assert captured_session["access_policies"] == [], (
        "a caller with no policies was handed None, which opteryx reads as "
        "owner-on-everything"
    )
    assert captured_session["access_policies"] is not None


def test_an_explicitly_empty_list_stays_empty(captured_session):
    _run(access_policies=[])

    assert captured_session["access_policies"] == []


def test_real_policies_are_passed_through_untouched(captured_session):
    policies = [{"pattern": "testdata.*", "role": "reader"}]
    _run(identity="alice", access_policies=policies)

    assert captured_session["access_policies"] == policies
    assert captured_session["user"] == "alice"
