"""What the service-wide $metadata document costs, and what it validates with.

This endpoint fans out over every dataset the caller can see, so its cost is
counted in Firestore round trips, and the assertions here are on those counts.

The shape they enforce is: the listing query reads each catalog document once
and that is the only time any of them is read. Everything the EDMX document
reports is either already in that document (name, source type, last-modified,
an inline schema) or is the single `schemas/{current-schema-id}` document it
points at -- and those are fetched for every dataset together, in one batched
read, not one dataset at a time.

The ETag half is the client-side counterpart -- the document is rebuilt on every
request (a dataset may have appeared or been dropped), but an unchanged one need
not be re-sent or re-parsed.
"""

import pytest

from tests._opteryx_stubs import load_with_stubs

# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_traffic_mod", "app", "routes", "v4", "metadata.py")

SCHEMA_ID = "s1"
COLUMNS = [{"name": "id", "type": "INTEGER"}]


class FakeRef:
    """A Firestore document reference that can address its subcollections."""

    def __init__(self, path):
        self.path = path

    def collection(self, name):
        return FakeCollection(f"{self.path}/{name}")


class FakeCollection:
    def __init__(self, path):
        self.path = path

    def document(self, doc_id):
        return FakeRef(f"{self.path}/{doc_id}")


class FakeDoc:
    def __init__(self, ref, data):
        self.reference = ref
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


class FakeFirestore:
    """Records every batched read, so a test can assert how many there were."""

    def __init__(self):
        self.documents = {}  # path -> stored data
        self.batches = []  # one entry per get_all call: the paths it asked for

    def get_all(self, refs):
        refs = list(refs)
        self.batches.append([ref.path for ref in refs])
        return [FakeDoc(ref, self.documents.get(ref.path)) for ref in refs]

    @property
    def documents_read(self):
        return [path for batch in self.batches for path in batch]


@pytest.fixture
def db(monkeypatch):
    firestore = FakeFirestore()
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: firestore)
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda _db, identity: [])
    return firestore


def _dataset(
    name,
    db=None,
    workspace="public",
    collection="ns",
    source="Table",
    schema_id=SCHEMA_ID,
    statement_id=None,
):
    """A DatasetInfo as the listing query would have produced it.

    A dataset's columns live in `schemas/{current-schema-id}`; a view's live in
    `statement/{statement-id}`, because they belong to the statement that
    produces them. Pass `statement_id` to build a view. When `db` is given, the
    document either one points at is registered there, so the batched read finds
    it.
    """
    subcollection = "views" if statement_id else "datasets"
    path = f"{workspace}/{collection}/{subcollection}/{name}"
    if db is not None:
        if statement_id:
            db.documents[f"{path}/statement/{statement_id}"] = {"columns": COLUMNS}
        elif schema_id:
            db.documents[f"{path}/schemas/{schema_id}"] = {"columns": COLUMNS}
    return metadata.DatasetInfo(
        resource=f"{workspace}.{collection}.{name}",
        workspace=workspace,
        collection=collection,
        name=name,
        source=source,
        schema_id=None if statement_id else schema_id,
        statement_id=statement_id,
        doc_ref=FakeRef(path),
    )


def _get(monkeypatch, datasets, headers=None, patterns=("public.*",), role="reader"):
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda _db, ids: list(datasets))

    request = type("R", (), {})()
    request.state = type("S", (), {})()
    request.headers = headers or {}
    request.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": pattern, "role": role} for pattern in patterns],
    }
    return metadata.get_edmx_metadata(request)


def _body(response):
    return response.body.decode() if hasattr(response, "body") else response.content.decode()


# ---------------------------------------------------------------------------
# Firestore traffic
# ---------------------------------------------------------------------------


def test_every_schema_is_fetched_in_a_single_batch(monkeypatch, db):
    """The endpoint used to open a catalog handle and load each dataset one at a
    time -- re-reading the very documents the listing had just streamed."""
    datasets = [_dataset(f"t{i}", db) for i in range(5)]

    body = _body(_get(monkeypatch, datasets))

    assert len(db.batches) == 1, "schemas must be read together, not one dataset at a time"
    assert sorted(db.batches[0]) == [f"public/ns/datasets/t{i}/schemas/s1" for i in range(5)]
    assert all(f'EntityType Name="public_ns_t{i}"' in body for i in range(5))


def test_the_catalog_document_itself_is_never_re_read(monkeypatch, db):
    """The listing already read it: name, source type and last-modified all come
    from there, so nothing should go back for the dataset document."""
    _get(monkeypatch, [_dataset("t", db)])

    assert db.documents_read == ["public/ns/datasets/t/schemas/s1"]


def test_a_views_columns_are_read_from_its_statement(monkeypatch, db):
    """A view's columns belong to the statement that produces them, so they are
    read from `statement/{statement-id}` -- in the SAME batch as every dataset's
    schema document, so a view costs one more entry in a read already being
    made, not a round trip of its own."""
    view = _dataset("v", db, source="View", statement_id="1700000000000")

    body = _body(_get(monkeypatch, [view, _dataset("t", db)]))

    assert len(db.batches) == 1, "a view must not cost a read of its own"
    assert sorted(db.batches[0]) == [
        "public/ns/datasets/t/schemas/s1",
        "public/ns/views/v/statement/1700000000000",
    ]
    assert 'EntitySet Name="public_ns_v"' in body
    assert 'Property Name="id"' in body


def test_a_view_naming_no_statement_declares_no_columns(monkeypatch, db):
    """A view document written before columns were recorded names no statement
    to read them from. It declares none rather than inventing them, and the
    builder then leaves it out of the document entirely."""
    view = _dataset("v", db, source="View", schema_id=None, statement_id=None)

    body = _body(_get(monkeypatch, [view]))

    assert db.batches == []
    assert "public_ns_v" not in body


def test_a_schema_id_wins_when_a_document_carries_both_ids(monkeypatch, db):
    """A document should carry one id or the other, never both. If one ever
    does, `current-schema-id` is the dataset's own answer and wins - resolving
    it as a view would read a statement that is not what this relation is."""
    dataset = metadata.DatasetInfo(
        resource="public.ns.t",
        workspace="public",
        collection="ns",
        name="t",
        schema_id=SCHEMA_ID,
        statement_id="1700000000000",
        doc_ref=FakeRef("public/ns/datasets/t"),
    )
    db.documents[f"public/ns/datasets/t/schemas/{SCHEMA_ID}"] = {"columns": COLUMNS}
    db.documents["public/ns/datasets/t/statement/1700000000000"] = {
        "columns": [{"name": "wrong_column", "type": "VARCHAR"}]
    }

    body = _body(_get(monkeypatch, [dataset]))

    assert 'Property Name="id"' in body
    assert "wrong_column" not in body


def test_virtual_datasets_are_never_read(monkeypatch, db):
    """Their schema is fixed and nothing backs them, so a read is a guaranteed
    miss -- one per virtual table per workspace the caller can reach."""
    virtual = [
        _dataset(name, db, collection="information_schema", source="Virtual")
        for name in metadata.VIRTUAL_INFORMATION_SCHEMA_TABLES
    ]

    body = _body(_get(monkeypatch, virtual))

    assert db.batches == []
    assert "public_information_schema_tables" in body


def test_a_reader_is_not_offered_the_automation_tables(monkeypatch, db):
    """The virtual tables are offered at the tier the engine filters their rows
    at. A reader's `tasks` would be an always-empty entity set whose columns say
    what they may not see, so it is not in their document at all."""
    body = _body(_get(monkeypatch, [], patterns=("ws.*",), role="reader"))

    assert 'EntitySet Name="ws_information_schema_tables"' in body
    assert 'EntitySet Name="ws_information_schema_columns"' in body
    assert "ws_information_schema_tasks" not in body
    assert "ws_information_schema_triggers" not in body
    assert "ws_information_schema_grants" not in body
    # public is reader for everyone; personal is the caller's own, as owner.
    assert "public_information_schema_tasks" not in body
    assert 'EntitySet Name="personal_information_schema_tasks"' in body


def test_an_owner_is_offered_the_automation_tables(monkeypatch, db):
    body = _body(_get(monkeypatch, [], patterns=("ws.*",), role="owner"))

    assert 'EntitySet Name="ws_information_schema_tasks"' in body
    assert 'EntitySet Name="ws_information_schema_triggers"' in body
    assert 'EntitySet Name="ws_information_schema_grants"' in body


def test_duplicate_resources_are_resolved_once(monkeypatch, db):
    """A dataset reachable by two routes used to cost a read each and emit a
    repeated EntitySet for the same EntityType."""
    body = _body(_get(monkeypatch, [_dataset("t", db), _dataset("t", db)]))

    assert db.documents_read == ["public/ns/datasets/t/schemas/s1"]
    assert body.count('EntitySet Name="public_ns_t"') == 1


def test_a_dataset_whose_schema_is_missing_is_left_out(monkeypatch, db):
    """A batch that comes back without a document must drop that dataset, not
    emit an EntityType with no key."""
    present = _dataset("here", db)
    absent = _dataset("gone")  # never registered in the fake Firestore

    body = _body(_get(monkeypatch, [present, absent]))

    assert 'EntitySet Name="public_ns_here"' in body
    assert "public_ns_gone" not in body


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_response_carries_an_etag(monkeypatch, db):
    res = _get(monkeypatch, [_dataset("t", db)])

    assert res.status_code == 200
    assert res.headers["etag"].startswith('"')
    assert res.headers["odata-version"] == "4.0"


def test_matching_if_none_match_returns_304(monkeypatch, db):
    etag = _get(monkeypatch, [_dataset("t", db)]).headers["etag"]

    res = _get(monkeypatch, [_dataset("t", db)], headers={"if-none-match": etag})

    assert res.status_code == 304
    assert res.body == b""
    assert res.headers["etag"] == etag


def test_a_new_dataset_changes_the_validator(monkeypatch, db):
    one = _get(monkeypatch, [_dataset("t", db)]).headers["etag"]

    two = _get(monkeypatch, [_dataset("t", db), _dataset("u", db)]).headers["etag"]

    assert one != two


def test_the_validator_does_not_depend_on_discovery_order(monkeypatch, db):
    """Firestore stream order and set iteration order are not stable across
    processes, so without a deterministic ordering two replicas would hand the
    same client different validators for identical content."""
    forwards = [_dataset("a", db), _dataset("b", db), _dataset("c", db)]

    first = _get(monkeypatch, forwards).headers["etag"]
    second = _get(monkeypatch, list(reversed(forwards))).headers["etag"]

    assert first == second


def test_the_document_is_still_rebuilt_for_a_conditional_request(monkeypatch, db):
    """A 304 saves the transfer, not the read: the answer to "what exists now"
    has to be recomputed to know it is still the same answer."""
    etag = _get(monkeypatch, [_dataset("t", db)]).headers["etag"]
    assert len(db.batches) == 1

    _get(monkeypatch, [_dataset("t", db)], headers={"if-none-match": etag})

    assert len(db.batches) == 2, "content must be re-checked, just not re-sent"


def test_ttl_serves_a_repeat_request_without_touching_firestore(monkeypatch, db):
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    _get(monkeypatch, [_dataset("t", db)])
    assert len(db.batches) == 1

    res = _get(monkeypatch, [_dataset("t", db)])

    assert len(db.batches) == 1, "second request should have been served from cache"
    assert res.headers["odata-version"] == "4.0"


def test_cached_documents_are_not_shared_between_callers(monkeypatch, db):
    """The document lists exactly what its caller may see, so a cache hit across
    two different callers would be a disclosure, not an optimisation."""
    monkeypatch.setenv("ODATA_METADATA_CACHE_TTL", "60")
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda _db, ids: [_dataset("t", db)])

    def _request(identity):
        request = type("R", (), {})()
        request.state = type("S", (), {})()
        request.headers = {}
        request.state.claims = {
            "identity": identity,
            "policies": [{"pattern": "public.*", "role": "reader"}],
        }
        return request

    metadata.get_edmx_metadata(_request("alice"))
    assert len(db.batches) == 1

    metadata.get_edmx_metadata(_request("bob"))
    assert len(db.batches) == 2, "a second identity must not reuse the first's document"
