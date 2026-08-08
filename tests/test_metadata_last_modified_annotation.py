"""Tests for last-modified annotations in EDMX metadata.

The service-wide document reports last-modified from the catalog document's
`timestamp-ms`, which the listing query has already read. That is not an
approximation of the snapshot time -- a commit takes
`snapshot_id = int(time.time() * 1000)` and writes it as both the snapshot's
`timestamp_ms` and the dataset metadata's, so reading the snapshot document to
learn the same number would be a round trip for nothing.
"""

import datetime as dt

from tests._opteryx_stubs import load_with_stubs

COMMIT_TS_MS = 1700000000000

# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_mod", "app", "routes", "v4", "metadata.py")


class FakeParentDoc:
    def __init__(self, doc_id):
        self.id = doc_id


class FakeSubcollection:
    def __init__(self, parent_id):
        self.parent = FakeParentDoc(parent_id)


class FakeDocRef:
    def __init__(self, parent_id, path):
        self.parent = FakeSubcollection(parent_id)
        self.path = path


class FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
        self.reference = FakeDocRef("ns", f"public/ns/datasets/{doc_id}")

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
    def __init__(self, docs):
        self._docs = docs

    def collection_group(self, name):
        return FakeQuery(self._docs if name == "datasets" else [])


def _iso(ts_ms):
    return dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=dt.UTC).isoformat()


def _render(monkeypatch, ds):
    """Run the service-wide endpoint over a single dataset and return the XML."""
    # No Firestore in tests: hand the endpoint a placeholder client so it gets
    # past the connectivity check -- the dataset lookups below never use it.
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [ds])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])

    req = type("R", (), {})()
    req.state = type("S", (), {})()
    req.headers = {}
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    res = metadata.get_edmx_metadata(req)
    return res.body.decode() if hasattr(res, "body") else res.content.decode()


def test_the_listing_reads_last_modified_from_the_catalog_document(monkeypatch):
    """`timestamp-ms` is the field the catalog writes.

    The listing used to look for `timestamp_ms` with an underscore, among other
    spellings this service does not produce, so no real catalog document ever
    matched and the annotation was simply absent.
    """
    db = FakeFirestore([FakeDoc("t", {"name": "t", "timestamp-ms": COMMIT_TS_MS})])

    listed = metadata._query_collection_group(db, "datasets")

    assert [d.last_modified_ms for d in listed] == [COMMIT_TS_MS]


def test_edmx_metadata_includes_last_modified(monkeypatch):
    ds = metadata.DatasetInfo(
        resource="public.ns.t",
        workspace="public",
        collection="ns",
        name="t",
        source="Table",
        last_modified_ms=COMMIT_TS_MS,
        # Carrying its columns inline keeps this test to the annotation under
        # test; the batched schema read has its own coverage.
        inline_schema=[{"name": "id", "type": "INTEGER"}],
    )

    content = _render(monkeypatch, ds)

    assert "Custom.LastModified" in content
    assert _iso(COMMIT_TS_MS) in content


def test_a_dataset_with_no_recorded_timestamp_gets_no_annotation(monkeypatch):
    """Absent is better than invented: a client must not read a missing
    last-modified as "modified at the epoch"."""
    ds = metadata.DatasetInfo(
        resource="public.ns.t",
        workspace="public",
        collection="ns",
        name="t",
        source="Table",
        inline_schema=[{"name": "id", "type": "INTEGER"}],
    )

    content = _render(monkeypatch, ds)

    assert "public_ns_t" in content
    assert "Custom.LastModified" not in content
