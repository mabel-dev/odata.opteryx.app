"""Tasks in the OData service document.

A task is a catalog object in the same `workspace.collection.<object>` namespace
as tables, views and materialized views -- a name in that namespace identifies
exactly one of them. Leaving tasks out of the service document did not merely
hide a feature: it made `CREATE TABLE ws.ops.foo` fail with "already exists as a
task" for an object the user could find nowhere in the UI.

The listing that reaches them is not the one that reaches datasets, and the tests
here are mostly about why. A task document carries neither a `workspace` nor a
`collection` field (see OpteryxCatalog.create_task), so the dataset listing's
`where("workspace", ...)` filter matches none of them -- reusing it would have
reported "no tasks" rather than failing, which is why the derived-from-path
behaviour is pinned here rather than left to the route to get right twice.
"""

import logging

import pytest
from fastapi import Response
from google.api_core.exceptions import FailedPrecondition

from app.routes.v4 import service_document as sd


class FakeCollectionRef:
    """A Firestore CollectionReference: `.parent` is the document above it, or
    None at the root."""

    def __init__(self, id, parent=None):
        self.id = id
        self.parent = parent


class FakeDocRef:
    def __init__(self, id, parent):
        self.id = id
        self.parent = parent


class FakeDoc:
    def __init__(self, ref, data):
        self.reference = ref
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


def task_doc(workspace, collection, name, data=None):
    """A task document at `{workspace}/{collection}/tasks/{name}`.

    The stored fields deliberately omit `workspace` and `collection`, exactly as
    the catalog writes them -- both names exist only in the path.
    """
    workspace_ref = FakeCollectionRef(workspace)
    collection_doc = FakeDocRef(collection, parent=workspace_ref)
    tasks_ref = FakeCollectionRef("tasks", parent=collection_doc)
    return FakeDoc(
        FakeDocRef(name, parent=tasks_ref),
        {"name": name, "statement-id": "1700000000000", "runs-as": "svc"}
        if data is None
        else data,
    )


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, count):
        return FakeQuery(self._docs[:count])

    def stream(self):
        return iter(self._docs)


class FakeFirestore:
    def __init__(self, task_docs=()):
        self._task_docs = list(task_docs)

    def collection_group(self, name):
        # Only `tasks` is asked for here; anything else legitimately has nothing.
        return FakeQuery(self._task_docs if name == "tasks" else [])


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    headers: dict = {}


@pytest.fixture
def no_soft_delete_check(monkeypatch):
    """Skip the `$properties` batched read; every workspace here is live."""
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids: ids)


# ---------------------------------------------------------------- _get_tasks


def test_workspace_and_collection_come_from_the_path(no_soft_delete_check):
    """The whole reason tasks need their own query: neither name is in the
    document, so a listing that reads them off the document gets None.None.name
    and a `where("workspace", ...)` filter matches nothing at all."""
    db = FakeFirestore([task_doc("opteryx", "ops", "nightly_rollup")])

    tasks = sd._get_tasks(db, {"opteryx"})

    assert len(tasks) == 1
    assert tasks[0].resource == "opteryx.ops.nightly_rollup"
    assert tasks[0].workspace == "opteryx"
    assert tasks[0].collection == "ops"
    assert tasks[0].name == "nightly_rollup"
    assert tasks[0].source == "Task"


def test_a_task_outside_the_requested_workspaces_is_not_returned(no_soft_delete_check):
    """The workspace restriction is applied in Python, not pushed into
    Firestore, so it is this filter alone standing between one workspace's
    caller and another workspace's task names."""
    db = FakeFirestore(
        [
            task_doc("opteryx", "ops", "mine"),
            task_doc("someone_else", "ops", "theirs"),
        ]
    )

    tasks = sd._get_tasks(db, {"opteryx"})

    assert [task.resource for task in tasks] == ["opteryx.ops.mine"]


def test_documents_under_a_dollar_collection_are_skipped(no_soft_delete_check):
    """`$dropped` and `$properties` are collection documents too, and a bare
    collection-group query reaches whatever is filed beneath them."""
    db = FakeFirestore(
        [
            task_doc("opteryx", "$dropped", "tombstoned"),
            task_doc("opteryx", "ops", "real"),
        ]
    )

    assert [task.resource for task in sd._get_tasks(db, {"opteryx"})] == ["opteryx.ops.real"]


def test_a_task_reports_no_sort_order(no_soft_delete_check):
    """Nothing is stored behind a task, so there is no physical order it could
    be in -- distinct from a table that happens to be unsorted."""
    db = FakeFirestore([task_doc("opteryx", "ops", "nightly_rollup")])

    task = sd._get_tasks(db, {"opteryx"})[0]

    assert task.ordered is False
    assert task.order_by is None


def test_no_workspaces_means_no_query_at_all(no_soft_delete_check):
    class ExplodingFirestore:
        def collection_group(self, name):  # pragma: no cover - must not be called
            raise AssertionError("queried Firestore for an empty workspace set")

    assert sd._get_tasks(ExplodingFirestore(), set()) == []


def test_a_missing_index_is_reported_as_an_error_not_swallowed_quietly(
    caplog, no_soft_delete_check
):
    """The failure mode is 'tasks never appear', which is indistinguishable from
    a workspace that has none -- so it has to be loud in the logs, and it must
    not take the rest of the service document down with it."""

    class NoIndex:
        def collection_group(self, name):
            raise FailedPrecondition("The query requires a COLLECTION_GROUP_ASC index")

    with caplog.at_level(logging.ERROR):
        assert sd._get_tasks(NoIndex(), {"opteryx"}) == []

    errors = [record for record in caplog.records if record.levelno >= logging.ERROR]
    assert errors, "a missing index must be logged at ERROR, not swallowed"
    # getMessage(), not .message: the latter is unpopulated until a formatter has
    # run, so asserting on it passes for the wrong reason.
    assert any("collection-group index" in record.getMessage() for record in errors)


def test_a_firestore_failure_does_not_fail_the_document(no_soft_delete_check):
    """Unlike the dataset listing, which raises 504: a service document without
    tasks is incomplete, one that fails lists nothing at all."""

    class Broken:
        def collection_group(self, name):
            raise RuntimeError("firestore is having a day")

    assert sd._get_tasks(Broken(), {"opteryx"}) == []


# -------------------------------------------------------- get_service_document


def _serve(monkeypatch, task_docs, claims, workspace_datasets=()):
    db = FakeFirestore(task_docs)
    monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: db)
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: list(workspace_datasets))
    monkeypatch.setattr(sd, "_drop_unlistable_workspaces", lambda db, ids: ids)
    monkeypatch.setattr(sd, "_resolve_pending_sort_orders", lambda db, datasets: None)

    request = DummyRequest()
    request.state.claims = claims
    return sd.get_service_document(request, Response())


def test_a_task_is_listed_beside_the_tables(monkeypatch):
    """One namespace, one list: a task is an EntitySet with a real url, not an
    entry a client has to know to skip."""
    table = sd.DatasetInfo(
        resource="opteryx.ops.orders",
        workspace="opteryx",
        collection="ops",
        name="orders",
        source="Table",
    )
    result = _serve(
        monkeypatch,
        [task_doc("opteryx", "ops", "nightly_rollup")],
        {"identity": "tester", "policies": [{"pattern": "opteryx.ops.*", "role": "writer"}]},
        workspace_datasets=[table],
    )

    entries = {entry["name"]: entry for entry in result["value"]}
    assert "opteryx.ops.orders" in entries
    assert "opteryx.ops.nightly_rollup" in entries

    task = entries["opteryx.ops.nightly_rollup"]
    assert task["source"] == "Task"
    # A task holds no rows of its own, but it is not therefore unreadable: it
    # reads as the single row of its own definition, so it carries the same kind
    # and the same shape of url as everything else in the list.
    assert task["kind"] == "EntitySet"
    assert task["url"] == "opteryx/ops/nightly_rollup"


def test_a_task_the_caller_cannot_read_is_not_listed(monkeypatch):
    """Mirrors the dataset rule exactly: the grant has to name THIS relation.
    A grant on one object must not disclose the name of the one beside it."""
    result = _serve(
        monkeypatch,
        [
            task_doc("opteryx", "ops", "visible"),
            task_doc("opteryx", "secrets", "hidden"),
        ],
        {"identity": "tester", "policies": [{"pattern": "opteryx.ops.*", "role": "reader"}]},
    )

    names = {entry["name"] for entry in result["value"]}
    assert "opteryx.ops.visible" in names
    assert "opteryx.secrets.hidden" not in names


def test_a_public_task_is_listed_read_only(monkeypatch):
    result = _serve(
        monkeypatch,
        [task_doc("public", "examples", "demo")],
        {"identity": "tester", "policies": []},
    )

    entries = {entry["name"]: entry for entry in result["value"]}
    assert entries["public.examples.demo"]["role"] == "reader"
    assert entries["public.examples.demo"]["policy"] == "always-allow-public-read"


def test_a_personal_task_is_the_owners_alone(monkeypatch):
    """Personal tasks arrive from the identity-agnostic tasks collection group
    rather than from `_get_personal_datasets`, so both halves need pinning: the
    caller's own is stamped owner, and everyone else's is dropped."""
    result = _serve(
        monkeypatch,
        [
            task_doc("personal", "tester", "my_task"),
            task_doc("personal", "someone_else", "their_task"),
        ],
        {"identity": "tester", "policies": []},
    )

    entries = {entry["name"]: entry for entry in result["value"]}
    assert "personal.someone_else.their_task" not in entries

    mine = entries["personal.tester.my_task"]
    assert mine["role"] == "owner"
    assert mine["policy"] == "always-allow-personal"


def test_an_anonymous_caller_sees_only_exempt_tasks(monkeypatch):
    from app.auth.permissions import ANONYMOUS_EXEMPT_PAIRS, ANONYMOUS_EXEMPT_WORKSTREAM

    workstream, collection = sorted(ANONYMOUS_EXEMPT_PAIRS)[0]
    assert workstream == ANONYMOUS_EXEMPT_WORKSTREAM

    result = _serve(
        monkeypatch,
        [
            task_doc(workstream, collection, "open"),
            task_doc(workstream, "not_exempt", "closed"),
        ],
        {"identity": None},
    )

    names = {entry["name"] for entry in result["value"]}
    assert f"{workstream}.{collection}.open" in names
    assert f"{workstream}.not_exempt.closed" not in names


def test_triggers_are_not_listed(no_soft_delete_check):
    """A trigger is named per-dataset in a subcollection, NOT in the
    `workspace.collection.<object>` namespace, so it belongs on a dataset's
    detail view and never in the object list. Only `tasks` is queried."""
    asked = []

    class RecordingFirestore(FakeFirestore):
        def collection_group(self, name):
            asked.append(name)
            return super().collection_group(name)

    db = RecordingFirestore([task_doc("opteryx", "ops", "nightly_rollup")])
    sd._get_tasks(db, {"opteryx"})

    assert asked == ["tasks"]
