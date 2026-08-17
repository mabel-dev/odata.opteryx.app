"""Tests for _drop_soft_deleted_workspaces: soft-deleted workspaces must not
leak their datasets into the OData service document listing (see
_get_workspace_datasets), even though their Firestore documents are still
there for the restore grace period.
"""

from app.routes.v4 import service_document as sd


class FakeDocRef:
    def __init__(self, workspace, doc_id):
        self.workspace = workspace
        self.doc_id = doc_id

    @property
    def parent(self):
        return FakeCollectionRef(self.workspace)


class FakeCollectionRef:
    def __init__(self, workspace):
        self.workspace = workspace
        self.id = workspace

    def document(self, doc_id):
        return FakeDocRef(self.workspace, doc_id)


class FakeSnapshot:
    def __init__(self, ref, data):
        self.reference = ref
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeDb:
    """`get_all` returns one FakeSnapshot per ref, keyed by the workspace the
    ref was built for -- deleted/missing/live per WORKSPACE_STATE below."""

    def __init__(self, state):
        self.state = state

    def collection(self, workspace):
        return FakeCollectionRef(workspace)

    def get_all(self, refs):
        return [FakeSnapshot(ref, self.state.get(ref.workspace)) for ref in refs]


class RaisingDb(FakeDb):
    def get_all(self, refs):
        raise RuntimeError("firestore unavailable")


def test_drops_workspace_with_deleted_at_ms_set():
    db = FakeDb({"mitre": {"deleted-at-ms": 1755000000000}, "opteryx": {"deleted-at-ms": None}})
    result = sd._drop_soft_deleted_workspaces(db, {"mitre", "opteryx"})
    assert result == {"opteryx"}


def test_keeps_workspace_with_no_properties_doc():
    # public/personal aren't provisioned through the normal create flow and
    # may have no $properties doc at all -- absence must not read as deleted.
    db = FakeDb({"public": None})
    result = sd._drop_soft_deleted_workspaces(db, {"public"})
    assert result == {"public"}


def test_keeps_workspace_with_deleted_at_ms_none():
    db = FakeDb({"opteryx": {"deleted-at-ms": None, "owner": "justin"}})
    result = sd._drop_soft_deleted_workspaces(db, {"opteryx"})
    assert result == {"opteryx"}


def test_empty_input_short_circuits_without_a_firestore_call():
    db = RaisingDb({})
    result = sd._drop_soft_deleted_workspaces(db, set())
    assert result == set()


def test_fails_open_on_firestore_error():
    db = RaisingDb({})
    result = sd._drop_soft_deleted_workspaces(db, {"mitre", "opteryx"})
    assert result == {"mitre", "opteryx"}
