"""A bound workspace's stub dataset docs must list like any other table.

Workspaces bound to an external Iceberg catalog have no real dataset
documents; control.opteryx projects the external catalog's listing into
Firestore as *stub* docs carrying only `workspace`/`collection`/`name`/
`external-catalog: true` (opteryx-catalog's WORKSPACE_CATALOG_RESOLUTION.md
section 5). odata.opteryx needs no code change for this - which is precisely
the claim worth pinning down: a stub has no `dataset-type`, no `sort-orders`
and no `current-schema-id`, so it must fall through every optional branch and
come out as an `ExternalTable` -- with no extra Firestore round trip and
nothing logged as a warning.

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


def test_stub_renders_as_an_external_table(caplog):
    schema_lookups = []
    with caplog.at_level(logging.WARNING):
        results = _query([("people", STUB)], schema_lookups)

    assert len(results) == 1, "the stub was skipped rather than listed"
    entry = results[0]
    assert entry.resource == "tarchia.interop.people"
    assert entry.workspace == "tarchia"
    assert entry.collection == "interop"
    # NOT "Table": a projection of someone else's catalog is a different
    # thing to a client from a table Opteryx stores, and the Studio draws it a
    # distinct glyph off this token
    assert entry.source == "ExternalTable"
    assert entry.name == "people"

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
    # the marker is the only thing that separates them, and it does
    assert by_name["people"].source == "ExternalTable"
    assert by_name["orders"].source == "Table"
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# $metadata
#
# The service document and the EDMX document are two surfaces of the same
# listing, and a name-only stub appeared in one and not the other: `$metadata`
# emits no EntityType for a dataset with no resolvable columns (metadata.py's
# `if not columns: continue`), so a bound workspace's tables were visible to a
# browser and invisible to Excel and Power BI, which read `$metadata` first.
#
# Stubs now carry an inline `schema`, projected by control.opteryx's catalog
# sync. These tests pin both halves: the schema resolves, and it resolves
# WITHOUT the extra Firestore read a `current-schema-id` pointer would cost.
# ---------------------------------------------------------------------------

from app.routes.v4 import metadata as md  # noqa: E402


STUB_COLUMNS = [
    {"id": 1, "name": "user_id", "type": "BIGINT", "nullable": False},
    {"id": 2, "name": "country", "type": "VARCHAR", "nullable": True},
    {"id": 3, "name": "price", "type": "DECIMAL(38, 9)", "nullable": True},
]

DETAILED_STUB = dict(STUB, schema=STUB_COLUMNS, statistics={"row-count": 1200})


class FakeMetadataDocRef(FakeDocRef):
    """metadata.py also reads `.path`, to recover the collection name."""

    def __init__(self, parent_doc_id, schema_lookups, path):
        super().__init__(parent_doc_id, schema_lookups)
        self.path = path


class FakeMetadataDoc(FakeDoc):
    def __init__(self, doc_id, data, parent_doc_id, schema_lookups):
        super().__init__(doc_id, data, parent_doc_id, schema_lookups)
        self.reference = FakeMetadataDocRef(
            parent_doc_id,
            schema_lookups,
            f"tarchia/{parent_doc_id}/datasets/{doc_id}",
        )


def _metadata_query(data, schema_lookups):
    db = FakeFirestore([FakeMetadataDoc("people", data, "interop", schema_lookups)])
    return md._query_collection_group(db, "datasets", workspace_filter="tarchia")


def test_detailed_stub_resolves_columns_for_metadata():
    lookups = []
    datasets = _metadata_query(DETAILED_STUB, lookups)
    assert len(datasets) == 1, "the stub was skipped rather than described"
    assert datasets[0].resource == "tarchia.interop.people"

    facts = md._facts_for(None, datasets)
    columns = facts["tarchia.interop.people"].columns
    # non-empty is the gate: metadata.py drops any dataset with no columns
    # from both the EntityType list and the EntityContainer
    assert [column.name for column in columns] == ["user_id", "country", "price"]
    # the stored spelling renders exactly as a native dataset's would
    assert dict(zip([c.name for c in columns], [c.type for c in columns]))["user_id"] == "Edm.Int64"


def test_detailed_stub_costs_no_extra_read_for_its_schema():
    lookups = []
    datasets = _metadata_query(DETAILED_STUB, lookups)
    md._facts_for(None, datasets)
    # an inline schema is read off the document already fetched; a
    # current-schema-id pointer would have cost one more read per dataset
    assert lookups == []
    assert datasets[0].schema_id is None
    assert datasets[0].inline_schema == STUB_COLUMNS


def test_name_only_stub_is_still_absent_from_metadata():
    # the behaviour that made collecting schema worth its cost - pinned so a
    # regression to name-only projections is visible as a test failure, not as
    # tables quietly missing from Excel
    datasets = _metadata_query(STUB, [])
    assert len(datasets) == 1
    facts = md._facts_for(None, datasets)
    assert facts["tarchia.interop.people"].columns == []


# ---------------------------------------------------------------------------
# Structural facts projected from the external catalog's own metadata
#
# `sort-orders` and `timestamp-ms` are fields these endpoints ALREADY read;
# the sync just fills them in for an external table. The point of each test
# below is that nothing in odata needed a special case, and nothing costs an
# extra Firestore read.
# ---------------------------------------------------------------------------


SORTED_STUB = dict(
    DETAILED_STUB,
    **{
        "timestamp-ms": 1_755_000_000_000,
        "sort-orders": [
            {"order-id": 1, "fields": [{"source-id": 2, "name": "country", "direction": "desc"}]}
        ],
        "partition-columns": ["country"],
    },
)


def test_a_sorted_external_table_reports_its_order():
    lookups = []
    results = _query([("people", SORTED_STUB)], lookups)
    assert len(results) == 1
    entry = results[0]
    assert entry.ordered is True
    assert entry.order_by == "country"
    assert entry.order_direction == "desc"
    # the projected NAME is what avoids the schema-document read; without it
    # the sort key would be left pending on a stub that has no schema doc
    assert entry._pending_sort_key is None
    assert entry._schema_ref is None
    assert lookups == []


def test_an_unsorted_external_table_still_reports_no_order():
    results = _query([("people", DETAILED_STUB)], [])
    assert results[0].ordered is False
    assert results[0].order_by is None


def test_last_modified_reaches_the_metadata_document():
    datasets = _metadata_query(SORTED_STUB, [])
    assert datasets[0].last_modified_ms == 1_755_000_000_000
    facts = md._facts_for(None, datasets)
    assert facts["tarchia.interop.people"].last_modified_ms == 1_755_000_000_000


def test_the_projected_extras_do_not_disturb_the_listing(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        results = _query([("people", SORTED_STUB)], [])
    assert results[0].source == "ExternalTable"
    assert results[0].resource == "tarchia.interop.people"
    # partition-columns and statistics are fields odata does not read; an
    # unknown field must not make a document unreadable or noisy
    assert caplog.text == ""
