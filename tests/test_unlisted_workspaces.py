"""A workspace can ask not to appear in listings, without changing who may read it.

`listed: false` on a workspace's `$properties` keeps it out of the service
document and `$metadata`, and so out of every catalog tree drawn from them. It
exists for library namespaces - `samples` holds five scale factors of TPC-H,
forty datasets meant to be forked once, which would otherwise sit in the
catalog of every account on the platform forever.

IT IS NOT A PERMISSION, and that is the property most worth protecting here:
nothing about this changes who may read a dataset. An unlisted workspace is
still queryable by name, still in `information_schema`, still named in
provenance. Access is decided by grants, in opteryx-access, and nowhere else.

The default direction is the other thing worth holding. Listing is what every
workspace has always done, and the failure modes are not symmetric: an unlisted
workspace that shows up is clutter, while a listed one that vanishes is
somebody's data disappearing out of their own catalog. So unset reads as
listed, an unrecognised value reads as listed, and an unreadable Firestore
reads as listed.
"""

import app.routes.v4.service_document as sd


class _Doc:
    def __init__(self, workspace, data, exists=True):
        self.exists = exists
        self._data = data
        self.reference = type(
            "Ref", (), {"parent": type("Parent", (), {"id": workspace})()}
        )()

    def to_dict(self):
        return self._data


class _FakeDb:
    """Answers `$properties` for each workspace from a dict."""

    def __init__(self, properties, raises=False):
        self._properties = properties
        self._raises = raises

    def collection(self, name):
        if self._raises:
            raise RuntimeError("firestore unavailable")
        return type("C", (), {"document": lambda _self, _d: name})()

    def get_all(self, refs):
        return [
            _Doc(ws, self._properties.get(ws), exists=ws in self._properties)
            for ws in refs
        ]


def _drop(properties, workspaces, raises=False, **kwargs):
    return sd._drop_unlistable_workspaces(_FakeDb(properties, raises), set(workspaces), **kwargs)


# --------------------------------------------------------------------------
# 1. The flag
# --------------------------------------------------------------------------


def test_an_unlisted_workspace_is_dropped():
    kept = _drop({"samples": {"listed": False}, "acme": {}}, ["samples", "acme"])
    assert kept == {"acme"}


def test_a_workspace_with_no_opinion_is_listed():
    assert _drop({"acme": {}}, ["acme"]) == {"acme"}


def test_listed_true_is_listed():
    assert _drop({"acme": {"listed": True}}, ["acme"]) == {"acme"}


def test_an_unrecognised_value_reads_as_listed():
    # A hand-written "false" is a string, which is truthy. Reading it as
    # unlisted would hide a workspace on the strength of a typo; the safe
    # direction for this flag is the status quo.
    assert _drop({"acme": {"listed": "off"}}, ["acme"]) == {"acme"}


def test_listed_none_is_listed():
    assert _drop({"acme": {"listed": None}}, ["acme"]) == {"acme"}


# --------------------------------------------------------------------------
# 2. It composes with deletion, and does not replace it
# --------------------------------------------------------------------------


def test_a_deleted_workspace_is_still_dropped():
    assert _drop({"acme": {"deleted-at-ms": 1}}, ["acme"]) == set()


def test_a_missing_properties_doc_is_still_dropped():
    assert _drop({}, ["acme"]) == set()


def test_public_and_personal_survive_a_missing_doc():
    # Neither is provisioned through the normal create flow, so neither ever
    # had a `$properties` document to read.
    assert _drop({}, ["public", "personal"]) == {"public", "personal"}


# --------------------------------------------------------------------------
# 3. Fail open
# --------------------------------------------------------------------------


def test_an_unreachable_firestore_lists_everything():
    # The whole set comes back rather than none of it: emptying every caller's
    # catalog on a transient error is far worse than briefly showing a
    # workspace that asked to be left out.
    assert _drop({}, ["acme", "beta"], raises=True) == {"acme", "beta"}


def test_nothing_to_check_is_answered_without_a_read():
    assert _drop({}, [], raises=True) == set()


# --------------------------------------------------------------------------
# The anonymous listing: `demo` is unlisted, and is all an anonymous caller has
# --------------------------------------------------------------------------


def test_include_unlisted_keeps_an_unlisted_workspace():
    # `demo` is unlisted so signed-in catalogs never show it, but it is the
    # whole of the anonymous one; that listing passes include_unlisted.
    assert _drop({"demo": {"listed": False}}, ["demo"], include_unlisted=True) == {"demo"}


def test_include_unlisted_still_drops_a_deleted_workspace():
    gone = {"demo": {"listed": False, "deleted-at-ms": 1}, "acme": {}}
    assert _drop(gone, ["demo", "acme"], include_unlisted=True) == {"acme"}


def test_include_unlisted_still_drops_a_missing_workspace():
    assert _drop({}, ["ghost"], include_unlisted=True) == set()
