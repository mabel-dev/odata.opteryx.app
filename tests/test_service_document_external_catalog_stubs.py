"""A bound workspace's stub dataset docs must list like any other table.

Workspaces bound to an external Iceberg catalog have no real dataset
documents; control.opteryx projects the external catalog's listing into
Firestore as *stub* docs carrying only `workspace`/`collection`/`name`/
`external-catalog: true` (opteryx-catalog's WORKSPACE_CATALOG_RESOLUTION.md
section 5). odata.opteryx needs no code change for this - which is precisely
the claim worth pinning down: a stub has no `dataset-type`, no `sort-orders`
and no `current-schema-id`, so it must fall through every optional branch and
come out as a plain `Table`, with no extra Firestore round trip and nothing
logged as a warning.

`_query_collection_group` swallows per-document errors with `continue`, so
every assertion here starts from "the stub actually came back" - a silently
skipped document would otherwise look like a pass.
"""

import logging

from app.routes.v4 import service_document as sd


class FakeCollectionRef:
    """Stands in for `doc.reference.collection("schemas")`.

    Records any lookup, because a stub reaching for a schema doc would mean
    an extra Firestore read per stub on every service-document request.
    """

    def __init__(self, sink):
        self._sink = sink

    def document(self, doc_id):
        self._sink.append(doc_id)
        return object()


class FakeDocRef:
    def __init__(self, parent_doc_id, schema_lookups):
        # doc.reference.parent is the "datasets" subcollection;
        # .parent.parent is the collection document itself.
        self.parent = type("Parent", (), {"parent": type("Grand", (), {"id": parent_doc_id})})
        self._schema_lookups = schema_lookups

    def collection(self, name):
        return FakeCollectionRef(self._schema_lookups)


class FakeDoc:
    def __init__(self, doc_id, data, parent_doc_id, schema_lookups):
        self.id = doc_id
        self._data = data
        self.reference = FakeDocRef(parent_doc_id, schema_lookups)

    def to_dict(self):
        return dict(self._data)


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, filter=None):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return list(self._docs)


class FakeFirestore:
    def __init__(self, docs):
        self._docs = docs

    def collection_group(self, name):
        return FakeQuery(self._docs if name == "datasets" else [])


STUB = {
    "workspace": "tarchia",
    "collection": "interop",
    "name": "people",
    "external-catalog": True,
}


def _query(docs, schema_lookups):
    db = FakeFirestore(
        [FakeDoc(name, data, "interop", schema_lookups) for name, data in docs]
    )
    return sd._query_collection_group(db, "datasets", workspace_filter="tarchia")


def test_stub_renders_as_a_plain_table(caplog):
    schema_lookups = []
    with caplog.at_level(logging.WARNING):
        results = _query([("people", STUB)], schema_lookups)

    assert len(results) == 1, "the stub was skipped rather than listed"
    entry = results[0]
    assert entry.resource == "tarchia.interop.people"
    assert entry.workspace == "tarchia"
    assert entry.collection == "interop"
    assert entry.name == "people"
    assert entry.source == "Table"

    # Nothing optional was inferred, and nothing complained about the gaps.
    assert entry.ordered is False
    assert entry.order_by is None
    assert entry.order_direction is None
    assert caplog.text == ""


def test_stub_costs_no_extra_firestore_read():
    schema_lookups = []
    results = _query([("people", STUB)], schema_lookups)
    assert len(results) == 1
    # A sort key needing resolution would have reached for a schema document.
    assert schema_lookups == []
    assert results[0]._schema_ref is None
    assert results[0]._pending_sort_key is None
    # ...and the batch resolver has nothing to do, so it never touches the db.
    sd._resolve_pending_sort_orders(None, results)


def test_stubs_and_real_datasets_list_side_by_side(caplog):
    real = {
        "workspace": "tarchia",
        "collection": "interop",
        "name": "orders",
        "location": "gs://bucket/tarchia/interop/orders",
    }
    with caplog.at_level(logging.WARNING):
        results = _query([("people", STUB), ("orders", real)], [])

    by_name = {entry.name: entry for entry in results}
    assert set(by_name) == {"people", "orders"}
    assert by_name["people"].source == "Table"
    assert by_name["orders"].source == "Table"
    assert caplog.text == ""
