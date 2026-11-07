"""`Custom.Favourites` on the service document.

The annotation is a flat list of workspace names the caller has starred,
beside `value` -- never inside it, because the service document has no
workspace entity and the tasks listing already refuses to emit "an entry a
client has to know to skip". Three things are pinned:

  SHAPE   Present for every authenticated caller, EMPTY INCLUDED (an empty
          list is a real answer and lets a client discard stars it remembered
          from another device). Absent, not empty, for an anonymous caller.
  SET     Looked up over the resolved workspace set plus `personal`, which is
          starrable but never in `workspace_ids`.
  CLOSED  A missing library, a failing library, or no identity is an empty
          list -- the tree falls back to alphabetical -- and never a raise
          that costs the caller their whole service document.
"""

import sys
import types

from fastapi import Response

from app.routes.v4 import service_document as sd


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    headers: dict = {}


def _stub_listing(monkeypatch):
    monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids, **_kw: [])
    monkeypatch.setattr(sd, "_get_tasks", lambda db, ids, **_kw: [])
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids, **_kw: ids)


def _document(monkeypatch, claims):
    req = DummyRequest()
    req.state.claims = claims
    return sd.get_service_document(req, Response())


# ---------------------------------------------------------------------------
# SHAPE
# ---------------------------------------------------------------------------


def test_the_callers_stars_ride_beside_value_not_inside_it(monkeypatch):
    _stub_listing(monkeypatch)
    monkeypatch.setattr(sd, "_starred_workspaces", lambda db, ids, identity: ["ledger", "erp"])

    doc = _document(
        monkeypatch,
        {"identity": "alice", "policies": [{"pattern": "erp.*", "role": "reader"}]},
    )

    assert doc["Custom.Favourites"] == ["ledger", "erp"]
    # Nothing workspace-shaped leaked into the EntitySet list.
    assert all(entry["kind"] == "EntitySet" for entry in doc["value"])


def test_nothing_starred_is_an_empty_list_that_is_still_present(monkeypatch):
    _stub_listing(monkeypatch)
    monkeypatch.setattr(sd, "_starred_workspaces", lambda db, ids, identity: [])

    doc = _document(monkeypatch, {"identity": "alice", "policies": []})

    assert "Custom.Favourites" in doc
    assert doc["Custom.Favourites"] == []


def test_an_anonymous_document_carries_no_annotation_at_all(monkeypatch):
    _stub_listing(monkeypatch)
    called = []
    monkeypatch.setattr(
        sd, "_starred_workspaces", lambda db, ids, identity: called.append(identity) or []
    )

    doc = _document(monkeypatch, {"identity": None, "policies": []})

    assert "Custom.Favourites" not in doc
    assert called == []  # never even asked


# ---------------------------------------------------------------------------
# SET
# ---------------------------------------------------------------------------


def test_the_lookup_set_is_the_resolved_workspaces_plus_personal(monkeypatch):
    _stub_listing(monkeypatch)
    # The soft-delete filter is the arbiter of the set; mimic it dropping one.
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids, **_kw: ids - {"gone"})
    seen = {}
    monkeypatch.setattr(
        sd, "_starred_workspaces", lambda db, ids, identity: seen.update(ids=set(ids), who=identity) or []
    )

    _document(
        monkeypatch,
        {
            "identity": "alice",
            "policies": [
                {"pattern": "erp.*", "role": "reader"},
                {"pattern": "gone.*", "role": "reader"},
            ],
        },
    )

    assert seen["who"] == "alice"
    assert "erp" in seen["ids"] and "public" in seen["ids"] and "personal" in seen["ids"]
    assert "gone" not in seen["ids"]


# ---------------------------------------------------------------------------
# CLOSED -- the helper itself, against a fake library
# ---------------------------------------------------------------------------


def _install_fake_favourites(monkeypatch, starred_workspaces):
    fake = types.ModuleType("opteryx_catalog.favourites")
    fake.starred_workspaces = starred_workspaces
    monkeypatch.setitem(sys.modules, "opteryx_catalog.favourites", fake)
    # `from opteryx_catalog import favourites` resolves the attribute on the
    # package, so it has to be there too.
    import opteryx_catalog

    monkeypatch.setattr(opteryx_catalog, "favourites", fake, raising=False)
    return fake


def test_the_helper_hands_the_library_a_sorted_set_and_returns_its_answer(monkeypatch):
    calls = []
    _install_fake_favourites(
        monkeypatch, lambda db, ids, identity: calls.append((list(ids), identity)) or ["erp"]
    )
    assert sd._starred_workspaces(object(), {"zeta", "erp", "alpha"}, "alice") == ["erp"]
    assert calls == [(["alpha", "erp", "zeta"], "alice")]


def test_a_library_that_raises_costs_the_stars_not_the_document(monkeypatch):
    def _boom(db, ids, identity):
        raise RuntimeError("firestore hiccup")

    _install_fake_favourites(monkeypatch, _boom)
    assert sd._starred_workspaces(object(), {"erp"}, "alice") == []


def test_no_identity_or_no_workspaces_never_touches_the_library(monkeypatch):
    def _never(db, ids, identity):
        raise AssertionError("should not be called")

    _install_fake_favourites(monkeypatch, _never)
    assert sd._starred_workspaces(object(), {"erp"}, None) == []
    assert sd._starred_workspaces(object(), {"erp"}, "") == []
    assert sd._starred_workspaces(object(), set(), "alice") == []


def test_an_installed_catalog_without_the_module_is_an_empty_list(monkeypatch):
    # Simulate opteryx-catalog < 0.4.165: the submodule does not exist.
    import opteryx_catalog

    monkeypatch.delitem(sys.modules, "opteryx_catalog.favourites", raising=False)
    monkeypatch.delattr(opteryx_catalog, "favourites", raising=False)
    real_import = __import__

    def _no_favourites(name, *args, **kwargs):
        if name == "opteryx_catalog" and args and args[2] and "favourites" in args[2]:
            raise ImportError("No module named 'opteryx_catalog.favourites'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _no_favourites)
    assert sd._starred_workspaces(object(), {"erp"}, "alice") == []
