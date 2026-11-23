"""Views appear in the service-wide $metadata document.

A view is queryable like any other relation, and the per-dataset $metadata
endpoint has always described one. But the service-wide listing queried only the
`datasets` collection group, so no view was ever named in the EDMX document --
the service document's twin listing queries both subcollections, and this one
had drifted from it. A client driving itself off $metadata could not see that
views existed at all.
"""

from tests._opteryx_stubs import load_with_stubs

metadata = load_with_stubs("metadata_views_mod", "app", "routes", "v4", "metadata.py")


class FakeParentDoc:
    def __init__(self, doc_id):
        self.id = doc_id


class FakeSubcollection:
    def __init__(self, parent_id):
        self.parent = FakeParentDoc(parent_id)


class FakeSubcollectionRef:
    """The `statement`/`schemas` subcollection hanging off a document."""

    def __init__(self, path):
        self.path = path

    def document(self, doc_id):
        return FakeDocRef("ns", f"{self.path}/{doc_id}")


class FakeDocRef:
    def __init__(self, parent_id, path):
        self.parent = FakeSubcollection(parent_id)
        self.path = path

    def collection(self, name):
        return FakeSubcollectionRef(f"{self.path}/{name}")


class FakeDoc:
    def __init__(self, doc_id, data, subcollection):
        self.id = doc_id
        self._data = data
        self.reference = FakeDocRef("ns", f"public/ns/{subcollection}/{doc_id}")

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class FakeFirestore:
    def __init__(self, by_subcollection):
        self._by_subcollection = by_subcollection
        self.queried = []

    def collection_group(self, name):
        self.queried.append(name)
        return FakeQuery(self._by_subcollection.get(name, []))


VIEW_DOC = {
    "name": "recent_orders",
    "workspace": "public",
    "namespace": "ns",
    # A view's columns belong to the STATEMENT that produces them, not to the
    # view: each statement version has its own shape, and the view's is
    # whichever one its current statement has. The view document names that
    # statement and nothing more.
    "statement-id": "1700000000000",
}

TABLE_DOC = {
    "name": "orders",
    "workspace": "public",
    "namespace": "ns",
    "current-schema-id": "s1",
}


def test_both_subcollections_are_queried():
    db = FakeFirestore({"datasets": [], "views": [], "property_graphs": []})

    metadata._get_workspace_datasets(db, {"public"})

    assert sorted(db.queried) == ["datasets", "property_graphs", "views"]


def test_a_view_is_listed_and_labelled_as_one():
    db = FakeFirestore(
        {
            "datasets": [FakeDoc("orders", TABLE_DOC, "datasets")],
            "views": [FakeDoc("recent_orders", VIEW_DOC, "views")],
        }
    )

    listed = metadata._get_workspace_datasets(db, {"public"})

    by_resource = {d.resource: d for d in listed}
    assert set(by_resource) == {"public.ns.orders", "public.ns.recent_orders"}
    assert by_resource["public.ns.recent_orders"].source == "View"
    assert by_resource["public.ns.orders"].source == "Table"


def test_a_views_columns_are_read_from_its_statement():
    """The columns are fetched from `statement/{statement-id}`, in the same
    batched read that resolves a dataset's schema document -- one more entry in
    a batch the listing already performs, not a round trip of its own."""
    db = FakeFirestore({"views": [FakeDoc("recent_orders", VIEW_DOC, "views")]})
    [view] = metadata._get_workspace_datasets(db, {"public"})

    statement_path = "public/ns/views/recent_orders/statement/1700000000000"
    read = []

    def fake_read(_db, refs):
        read.extend(ref.path for ref in refs)
        return {
            statement_path: metadata._columns_from_stored(
                [{"name": "order_id", "type": "INTEGER"}]
            )
        }

    original, metadata._read_schema_documents = metadata._read_schema_documents, fake_read
    try:
        facts = metadata._facts_for(None, [view])
    finally:
        metadata._read_schema_documents = original

    assert read == [statement_path]
    assert [column.name for column in facts["public.ns.recent_orders"].columns] == ["order_id"]


def test_a_view_whose_statement_is_unknown_declares_no_columns():
    """A view document written before schemas were recorded names no statement
    this can read columns from. It declares none rather than inventing them -
    the caller then drops it from the document."""
    db = FakeFirestore({"views": [FakeDoc("recent_orders", {**VIEW_DOC, "statement-id": None}, "views")]})
    [view] = metadata._get_workspace_datasets(db, {"public"})

    facts = metadata._facts_for(None, [view])

    assert facts["public.ns.recent_orders"].columns == []
