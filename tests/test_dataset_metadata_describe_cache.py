"""Column statistics are cached against the artifacts they were computed from.

`describe()` is the most expensive call the per-dataset $metadata endpoint
makes -- it fetches the current snapshot's manifest from GCS and walks every
entry to accumulate per-column min/max, null counts and cardinality. Its answer
is a pure function of that manifest and the schema used to index it, both named
by ids that change whenever their content does.

That is what makes this cache different from the response cache next to it: it
gives up no freshness at all. A dataset that has been written to since misses,
because the key it would be looked up under did not exist before the write.
"""

import pytest
from fastapi import Response

from app.routes.v4 import interface


class DummyRequest:
    def __init__(self):
        self.state = type("S", (), {"claims": {"identity": "test-user"}})()
        self.headers: dict = {}


class FakeMetadata:
    def __init__(self, schema_id):
        self.current_schema_id = schema_id
        self.sort_orders = []


class FakeSnapshot:
    def __init__(self, snapshot_id):
        self.snapshot_id = snapshot_id
        self.timestamp_ms = 1700000000000
        self.summary = {}


class FakeDataset:
    """A dataset whose describe() records that it was actually called."""

    def __init__(self, describes, snapshot_id=1, schema_id="s1", maximum=100):
        self._describes = describes
        self._snapshot_id = snapshot_id
        self._maximum = maximum
        self.metadata = FakeMetadata(schema_id)

    def schema(self):
        return type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def snapshot(self):
        return FakeSnapshot(self._snapshot_id)

    def describe(self):
        self._describes.append(self._snapshot_id)
        return {"id": {"min": 1, "max": self._maximum}}


class Describes:
    """The snapshot ids describe() was actually invoked for, in order.

    Doubles as the handle for swapping in a different dataset mid-test, which is
    how a write (new snapshot) or a schema change is simulated.
    """

    def __init__(self):
        self.calls = []
        self.dataset = FakeDataset(self.calls)

    def rebind(self, dataset):
        self.dataset = dataset

    def new_dataset(self, cls=FakeDataset, **kwargs):
        return cls(self.calls, **kwargs)


@pytest.fixture
def describes(monkeypatch):
    """Install a catalog whose datasets record every describe() call."""
    recorder = Describes()

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            return recorder.dataset

        def load_view(self, path):
            raise RuntimeError("not a view")

    monkeypatch.setattr(interface, "OpteryxCatalog", FakeCatalog)
    return recorder


def _get():
    return interface.dataset_metadata(
        DummyRequest(),
        Response(),
        workstream="public",
        collection="examples",
        dataset="my_table",
    )


def _body(response):
    return response.body.decode()


def test_statistics_are_computed_once_for_an_unchanged_dataset(describes):
    _get()
    _get()

    assert describes.calls == [1], "the manifest must not be re-read for the same snapshot"


def test_the_document_still_reports_the_statistics_on_a_cache_hit(describes):
    first = _body(_get())

    assert _body(_get()) == first


def test_a_new_snapshot_recomputes_the_statistics(describes):
    """A write produces a new snapshot id, so the stale entry is unreachable
    rather than merely expired."""
    _get()
    describes.rebind(describes.new_dataset(snapshot_id=2, maximum=999))

    _get()

    assert describes.calls == [1, 2]
    assert "999" in _body(_get())


def test_a_new_schema_recomputes_the_statistics(describes):
    """The description is indexed by schema column, so a schema change makes the
    previous one no longer describe the same thing."""
    _get()
    describes.rebind(describes.new_dataset(schema_id="s2"))

    _get()

    assert describes.calls == [1, 1], "same snapshot, but the schema it is read against changed"


def test_a_dataset_without_a_snapshot_is_never_cached(describes):
    """There is no immutable id to pin the entry to, so it must be recomputed
    rather than cached under a key that cannot detect a change."""

    class SnapshotlessDataset(FakeDataset):
        def snapshot(self):
            return None

    describes.rebind(describes.new_dataset(SnapshotlessDataset))

    _get()
    _get()

    assert describes.calls == [1, 1]
