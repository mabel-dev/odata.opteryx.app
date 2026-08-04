"""Tests for the Custom.Statistics.DistinctValueCount* annotations in $metadata.

The endpoint takes cardinality straight from the catalog's describe() output
(`cardinality` / `cardinality_is_exact`) and encodes exactness in the *name* of
the annotation term:

    exact     -> Custom.Statistics.DistinctValueCount
    estimated -> Custom.Statistics.DistinctValueCountEstimate

An estimate is additionally capped at the snapshot's total-records, since a
column cannot hold more distinct values than the table has rows.

(This file previously tested cardinality inferred from `min_k_hashes`; that
inference no longer exists in the service -- describe() supplies cardinality
directly -- so these tests cover the mapping that is actually in the code.)
"""

import re

from fastapi import Response

from app.routes.v4 import interface


class DummyState:
    # dataset_metadata() requires an authenticated identity on request.state
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class FakeDataset:
    """A single-column table whose describe()/snapshot() the test controls."""

    def __init__(self, stats=None, total_records=None):
        self._stats = stats
        self._total_records = total_records

    def schema(self):
        return type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "id", "type": "INTEGER", "nullable": False})]},
        )

    def describe(self):
        return {"id": dict(self._stats)} if self._stats else {}

    def snapshot(self):
        if self._total_records is None:
            return None
        return type(
            "Snap",
            (),
            {"timestamp_ms": 1700000000000, "summary": {"total-records": self._total_records}},
        )()


def get_xml_for(stats=None, total_records=None):
    """Render $metadata for a table with the given column statistics."""

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            return FakeDataset(stats=stats, total_records=total_records)

        def load_view(self, path):
            raise RuntimeError("not a view")

    interface.OpteryxCatalog = FakeCatalog
    res = interface.dataset_metadata(
        DummyRequest(), Response(), workstream="public", collection="examples", dataset="my_table"
    )
    return res.content.decode() if hasattr(res, "content") else res.body.decode()


def cardinality_terms(content):
    """Return the DistinctValueCount* annotation terms and values present."""
    return re.findall(
        r'Term="Custom\.Statistics\.(DistinctValueCount\w*)"\s+Int="(\d+)"',
        content,
    )


def test_exact_cardinality_uses_plain_term():
    content = get_xml_for(stats={"cardinality": 2, "cardinality_is_exact": True})
    assert cardinality_terms(content) == [("DistinctValueCount", "2")]


def test_estimated_cardinality_uses_estimate_term():
    content = get_xml_for(stats={"cardinality": 42, "cardinality_is_exact": False})
    assert cardinality_terms(content) == [("DistinctValueCountEstimate", "42")]


def test_missing_exactness_flag_is_treated_as_estimate():
    """cardinality_is_exact absent -> conservative: report as an estimate."""
    content = get_xml_for(stats={"cardinality": 7})
    assert cardinality_terms(content) == [("DistinctValueCountEstimate", "7")]


def test_estimate_is_capped_at_total_records():
    """A column cannot have more distinct values than the table has rows."""
    content = get_xml_for(
        stats={"cardinality": 1000, "cardinality_is_exact": False}, total_records=25
    )
    assert cardinality_terms(content) == [("DistinctValueCountEstimate", "25")]


def test_exact_cardinality_is_not_capped():
    """An exact count is authoritative and must be emitted unchanged."""
    content = get_xml_for(
        stats={"cardinality": 1000, "cardinality_is_exact": True}, total_records=25
    )
    assert cardinality_terms(content) == [("DistinctValueCount", "1000")]


def test_zero_and_missing_cardinality_emit_no_annotation():
    assert cardinality_terms(get_xml_for(stats={"cardinality": 0})) == []
    assert cardinality_terms(get_xml_for(stats={"cardinality": None})) == []
    assert cardinality_terms(get_xml_for(stats={})) == []
