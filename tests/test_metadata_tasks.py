"""Tasks in the service-wide `$metadata` document.

The service document lists a task as an EntitySet, so this document has to carry
the matching EntityType: an EntitySet whose type is absent is a dangling
reference to any client that reads both, and the Studio reads both.

A task holds no rows, which is not the same as being unreadable -- it occupies a
name in the one `workspace.collection.<object>` namespace, and a name in that
namespace has to answer to a read. What it answers with is the single row of its
own definition, so its EntityType is that definition's fixed column list and no
schema document is fetched to build it.
"""

import xml.etree.ElementTree as ET

from app.odata.virtual_datasets import TASK_DEFINITION_COLUMNS
from tests._opteryx_stubs import load_with_stubs

metadata = load_with_stubs("metadata_tasks_mod", "app", "routes", "v4", "metadata.py")

NS_EDM = "http://docs.oasis-open.org/odata/ns/edm"


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    headers: dict = {}


def _task(resource="opteryx.ops.nightly_rollup"):
    workspace, collection, name = resource.split(".", 2)
    return metadata.DatasetInfo(
        resource=resource,
        workspace=workspace,
        collection=collection,
        name=name,
        source=metadata.TASK_SOURCE,
    )


def _render(monkeypatch, datasets, policies):
    """Build the EDMX document over `datasets` and return its parsed root.

    `_facts_for` is deliberately NOT stubbed: resolving a task's columns without
    a Firestore read is the behaviour under test.
    """
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(metadata, "_get_tasks", lambda db, ids, **_kw: list(datasets))
    monkeypatch.setattr(metadata, "_get_relationships", lambda db, ids: [])

    request = DummyRequest()
    request.state.claims = {"identity": "tester", "policies": policies}

    response = metadata.get_edmx_metadata(request)
    body = response.body if hasattr(response, "body") else response.content
    return ET.fromstring(body.decode()), body.decode()


GRANT = [{"pattern": "opteryx.ops.*", "role": "writer"}]


def test_a_task_gets_an_entity_type(monkeypatch):
    root, _ = _render(monkeypatch, [_task()], GRANT)

    names = {entity.get("Name") for entity in root.iter(f"{{{NS_EDM}}}EntityType")}
    assert "opteryx_ops_nightly_rollup" in names


def test_the_entity_type_is_the_task_definition(monkeypatch):
    """One row, described by the fields the catalog stores for a task -- its
    statement, who created it, and how the last firing went. NOT the identity it
    runs as: a task has none, that belongs to the trigger that fires it."""
    root, _ = _render(monkeypatch, [_task()], GRANT)

    entity = next(
        entity
        for entity in root.iter(f"{{{NS_EDM}}}EntityType")
        if entity.get("Name") == "opteryx_ops_nightly_rollup"
    )
    properties = [prop.get("Name") for prop in entity.iter(f"{{{NS_EDM}}}Property")]

    assert properties == [column.name for column in TASK_DEFINITION_COLUMNS]
    # The fields that make it a task rather than a table, spot-checked by name so
    # a rename of any of them fails here rather than silently in the Studio.
    for column in ("statement", "last_fired_status", "last_window_to"):
        assert column in properties
    # A task has no act-as identity -- that belongs to the trigger that fires
    # it, never to the task. See test_dataset_metadata_task.py.
    assert "runs_as" not in properties


def test_a_task_is_annotated_as_a_task(monkeypatch):
    """The `source` discriminator is what the Studio switches on to draw a
    per-type glyph, so it has to reach this document too, not only the service
    document."""
    _, body = _render(monkeypatch, [_task()], GRANT)

    assert "Custom.SourceType" in body
    assert 'String="Task"' in body


def test_a_task_costs_no_schema_read(monkeypatch):
    """Its columns are fixed, so `_facts_for` must answer for it outright --
    there is no `schemas/{id}` document behind a task to batch-read."""

    class ExplodingFirestore:
        def get_all(self, refs):  # pragma: no cover - must not be called
            raise AssertionError("fetched a schema document for a task")

    facts = metadata._facts_for(ExplodingFirestore(), [_task()])

    assert facts["opteryx.ops.nightly_rollup"].columns == TASK_DEFINITION_COLUMNS
    # Nothing behind a task carries a modification time: its statement is
    # versioned in a subcollection the listing does not read, and `created-at-ms`
    # answers a different question.
    assert facts["opteryx.ops.nightly_rollup"].last_modified_ms is None


def test_a_task_the_caller_cannot_read_is_not_described(monkeypatch):
    """Same rule as the service document's: the grant has to name THIS relation,
    or the document discloses the name of every task beside the one granted."""
    root, _ = _render(
        monkeypatch,
        [_task("opteryx.ops.visible"), _task("opteryx.secrets.hidden")],
        GRANT,
    )

    names = {entity.get("Name") for entity in root.iter(f"{{{NS_EDM}}}EntityType")}
    assert "opteryx_ops_visible" in names
    assert "opteryx_secrets_hidden" not in names
