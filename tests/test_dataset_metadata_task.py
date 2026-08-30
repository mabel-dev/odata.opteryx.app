"""Per-dataset `$metadata` for a task.

A task is a statement the platform runs on its own. It holds no rows, which is
not the same as being unreadable: it occupies a name in the one
`workspace.collection.<object>` namespace, so this endpoint owes that name an
answer, and what it answers with is the single row of its own definition.

Two things are pinned here beyond "it renders".

The first is COST. The task lookup is the third rung of a ladder --
`load_dataset`, then `load_view`, then `get_task` -- so a table, a view and a
materialized view never pay for it. The read happens exactly where the
alternative was a 404, which is why adding it to a hot endpoint is free.

The second is that a task must not be dressed as a table. It has no snapshot,
no row count, no sort order and no tags, and reporting any of those as zero
would say something false about an object that cannot have them at all.
"""

import xml.etree.ElementTree as ET

import pytest
from fastapi import HTTPException, Response

from app.routes.v4 import interface

EDM = "http://docs.oasis-open.org/odata/ns/edm"


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


# What OpteryxCatalog.get_task() returns, verbatim in shape.
TASK = {
    "identifier": "public.ops.compaction_log_ingest",
    "name": "compaction_log_ingest",
    "collection": "ops",
    "workspace": "public",
    "sql": "INSERT INTO ops.compaction_log SELECT * FROM ops.compaction_log_raw",
    "statement-id": "1787261893891",
    "runs-as": "svc-compaction@opteryx",
    "description": "Fold the raw compaction log into the reporting table.",
    "created-by": "justin",
    "created-at-ms": 1787261893891,
    "last-updated-by": "alex",
    "last-updated-at-ms": 1787261999999,
    "suspended-at-ms": None,
    "suspended-by": None,
    "last-fired-at-ms": 1787262000000,
    "last-fired-status": "ok",
    "last-window-to": 41,
}


class TaskNotFound(Exception):
    """Named to match the catalog's own exception: `_task_record` tells a
    missing task apart from a broken one by the exception's CLASS NAME, so a
    stand-in that is not called this would be logged as a real failure."""


@pytest.fixture
def catalog(monkeypatch):
    """Install a catalog whose three lookups can each be made to hit or miss,
    and record which of them were actually called."""

    calls = []

    def _install(*, dataset=False, view=False, task=None):
        class FakeCatalog:
            def __init__(self, *args, **kwargs):
                pass

            def load_dataset(self, identifier):
                calls.append("load_dataset")
                if not dataset:
                    raise Exception("no such dataset")
                raise AssertionError("this test does not exercise the dataset path")

            def load_view(self, identifier):
                calls.append("load_view")
                if not view:
                    raise Exception("no such view")
                raise AssertionError("this test does not exercise the view path")

            def get_task(self, identifier):
                calls.append("get_task")
                if task is None:
                    raise TaskNotFound(f"Task not found: {identifier}")
                return dict(task)

        monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
        # No Firestore: the external-catalog stub lookup runs before the catalog
        # path and must simply find nothing.
        monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
        monkeypatch.setattr(interface, "_declared_relationships", lambda *a, **kw: [])
        monkeypatch.setattr(interface, "_inbound_relationships", lambda *a, **kw: [])
        monkeypatch.setattr(interface, "clear_metadata_cache", lambda: None)
        interface.clear_metadata_cache()
        return calls

    return _install


def _render(workstream="public", collection="ops", dataset="compaction_log_ingest"):
    response = Response()
    result = interface.dataset_metadata(
        DummyRequest(), response, workstream=workstream, collection=collection, dataset=dataset
    )
    body = result.content if hasattr(result, "content") else result.body
    return body.decode(), response


def _properties(content):
    root = ET.fromstring(content)
    return [element.get("Name") for element in root.iter(f"{{{EDM}}}Property")]


def _annotations(content):
    root = ET.fromstring(content)
    return {
        element.get("Term"): (element.get("String") or element.get("Int") or element.get("Bool"))
        for element in root.iter(f"{{{EDM}}}Annotation")
    }


# ------------------------------------------------------------------ the ladder


def test_a_task_is_found_after_the_dataset_and_view_both_miss(catalog):
    calls = catalog(task=TASK)
    content, response = _render()

    assert calls == ["load_dataset", "load_view", "get_task"]
    assert response.headers.get("X-Dataset-Source") == "Task"
    assert content


def test_a_name_that_is_nothing_at_all_is_still_a_404(catalog):
    catalog(task=None)

    with pytest.raises(HTTPException) as raised:
        _render(dataset="not_anything")
    assert raised.value.status_code == 404


def test_an_older_catalog_without_get_task_still_404s_rather_than_erroring(monkeypatch):
    """`get_task` is newer than this endpoint. Its absence has to read as
    'not a task' -- the 404 the request was already going to get -- and not as
    an AttributeError turning a missing dataset into a 500."""

    class OldCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, identifier):
            raise Exception("no such dataset")

        def load_view(self, identifier):
            raise Exception("no such view")

    monkeypatch.setattr(interface, "OpteryxCatalog", OldCatalog)
    monkeypatch.setattr(interface, "_external_catalog_stub", lambda *a, **kw: None)
    monkeypatch.setattr(interface, "_declared_relationships", lambda *a, **kw: [])
    monkeypatch.setattr(interface, "_inbound_relationships", lambda *a, **kw: [])
    interface.clear_metadata_cache()

    with pytest.raises(HTTPException) as raised:
        _render(dataset="anything")
    assert raised.value.status_code == 404


# ------------------------------------------------------------------- the shape


def test_the_columns_are_the_task_definition(catalog):
    from app.odata.virtual_datasets import TASK_DEFINITION_COLUMNS

    catalog(task=TASK)
    content, _ = _render()

    assert _properties(content) == [column.name for column in TASK_DEFINITION_COLUMNS]


def test_the_registration_record_is_annotated(catalog):
    catalog(task=TASK)
    annotations = _annotations(_render()[0])

    # The statement gets Custom.Definition, the term views and materialized
    # views already use, so a client that renders one renders this for free.
    assert annotations["Custom.Definition"] == TASK["sql"]
    assert annotations["Custom.Task.CreatedBy"] == "justin"
    assert annotations["Custom.Task.LastUpdatedBy"] == "alex"
    assert annotations["Custom.Task.LastFiredStatus"] == "ok"
    assert annotations["Custom.Task.CreatedAt"] == str(TASK["created-at-ms"])
    assert annotations["Custom.Task.LastFiredAt"] == str(TASK["last-fired-at-ms"])
    assert annotations["Custom.Task.LastWindowTo"] == "41"
    assert annotations["Custom.SourceType"] == "Task"


def test_a_task_never_reports_a_run_as_identity(catalog):
    """A task has no act-as identity, and must not appear to.

    Anyone may trigger a task; whether it runs follows the permissions of
    whoever triggered it. An act-as identity belongs to a TRIGGER -- the thing
    that fires work when a dataset is updated, where that work needs rights the
    updater may not have (an update to `sales` firing work against
    `inventory`). The catalog still stores `runs-as` on the task document, and
    `get_task` still returns it, so this is not absent for want of data: it is
    withheld. Publishing it lets a client present it as "the identity this task
    runs as", which lends the task's authority to anyone who can reach it --
    the exact failure the task/trigger split exists to prevent.
    """
    catalog(task=TASK)
    assert TASK["runs-as"], "the fixture must still supply one, or this proves nothing"

    content, _ = _render()
    assert "Custom.Task.RunsAs" not in _annotations(content)
    assert "runs_as" not in _properties(content)
    assert TASK["runs-as"] not in content


def test_the_tasks_own_description_wins_over_the_generic_one(catalog):
    catalog(task=TASK)
    assert _annotations(_render()[0])["Custom.Description"] == TASK["description"]


def test_a_task_with_no_description_still_gets_one(catalog):
    """An object with no description at all reads in the UI as an object nobody
    can explain, so the fixed sentence stands in -- the same treatment the
    virtual datasets get."""
    from app.odata.virtual_datasets import TASK_DESCRIPTION

    catalog(task={**TASK, "description": None})
    assert _annotations(_render()[0])["Custom.Description"] == TASK_DESCRIPTION


def test_suspended_is_emitted_even_when_false(catalog):
    """`false` has to mean "running", not "this service is too old to say" --
    so it is emitted unconditionally, unlike every other task annotation."""
    catalog(task=TASK)
    assert _annotations(_render()[0])["Custom.Task.Suspended"] == "false"


def test_a_suspended_task_says_so(catalog):
    catalog(task={**TASK, "suspended-at-ms": 1787262500000, "suspended-by": "justin"})
    annotations = _annotations(_render()[0])

    assert annotations["Custom.Task.Suspended"] == "true"
    assert annotations["Custom.Task.SuspendedBy"] == "justin"
    assert annotations["Custom.Task.SuspendedAt"] == "1787262500000"


def test_a_task_is_not_dressed_as_a_table(catalog):
    """No storage means no snapshot, no row count, no sort order and no tags.
    Reporting any of them as zero would say something false about an object
    that cannot have one, so they must be ABSENT rather than empty."""
    catalog(task=TASK)
    annotations = _annotations(_render()[0])

    for term in (
        "Custom.Snapshot.TotalRecords",
        "Custom.Snapshot.Author",
        "Custom.SortOrder.Column",
        "Custom.SortOrder.Direction",
        "Custom.Tags.Count",
    ):
        assert term not in annotations, f"{term} must not be emitted for a task"


def test_a_task_is_not_reported_as_a_materialized_view(catalog):
    """`Custom.MaterializedView.Materialized` is emitted unconditionally for an
    MV, and its absence is how a client tells an unbuilt MV from a task."""
    catalog(task=TASK)
    assert "Custom.MaterializedView.Materialized" not in _annotations(_render()[0])
