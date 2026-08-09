"""Tests for Custom.Statistics.ElementDistinctValueCount* in $metadata.

An ARRAY column has no ordinal encoding of its own, so it reports no bounds and
no histogram, and its whole-column distinct count would answer a question
nobody asks -- how many distinct LISTS there are. The catalog therefore sketches
the flat child vector as well, pooling every row's elements, and that count is
what these annotations carry.

Exactness is encoded in the term name, exactly as the row-level count does it:

    exact     -> Custom.Statistics.ElementDistinctValueCount
    estimated -> Custom.Statistics.ElementDistinctValueCountEstimate
"""

import re

from fastapi import Response

from app.routes.v4 import interface
from app.routes.v4.metadata_cache import clear_describe_cache
from app.routes.v4.metadata_cache import clear_metadata_cache


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


class FakeDataset:
    def __init__(self, stats, total_records):
        self._stats = stats
        self._total_records = total_records

    def schema(self):
        column = type("C", (), {"name": "tags", "type": "ARRAY<VARCHAR>", "nullable": True})
        return type("S", (), {"columns": [column]})

    def describe(self):
        return {"tags": dict(self._stats)} if self._stats else {}

    def snapshot(self):
        if self._total_records is None:
            return None
        return type(
            "Snap",
            (),
            {"timestamp_ms": 1700000000000, "summary": {"total-records": self._total_records}},
        )()


def get_xml_for(stats=None, total_records=None):
    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            return FakeDataset(stats or {}, total_records)

        def load_view(self, path):
            raise RuntimeError("not a view")

    clear_metadata_cache()
    clear_describe_cache()
    interface.OpteryxCatalog = FakeCatalog
    res = interface.dataset_metadata(
        DummyRequest(), Response(), workstream="public", collection="examples", dataset="events"
    )
    return res.content.decode() if hasattr(res, "content") else res.body.decode()


def element_terms(content):
    return re.findall(
        r'Term="Custom\.Statistics\.(ElementDistinctValueCount\w*)"\s+Int="(\d+)"',
        content,
    )


def row_terms(content):
    return re.findall(
        r'Term="Custom\.Statistics\.(DistinctValueCount\w*)"\s+Int="(\d+)"',
        content,
    )


def test_exact_element_count_uses_plain_term():
    content = get_xml_for(
        stats={"element_cardinality": 7, "element_cardinality_is_exact": True}
    )
    assert element_terms(content) == [("ElementDistinctValueCount", "7")]


def test_estimated_element_count_uses_estimate_term():
    content = get_xml_for(
        stats={"element_cardinality": 5012, "element_cardinality_is_exact": False}
    )
    assert element_terms(content) == [("ElementDistinctValueCountEstimate", "5012")]


def test_missing_exactness_flag_is_treated_as_estimate():
    content = get_xml_for(stats={"element_cardinality": 42})
    assert element_terms(content) == [("ElementDistinctValueCountEstimate", "42")]


def test_element_count_is_not_capped_at_total_records():
    """Unlike the row-level estimate, which IS capped.

    A row contributes a whole list, so a column can hold far more distinct
    elements than the table has rows -- capping would silently under-report the
    one statistic an array column has.
    """
    content = get_xml_for(
        stats={"element_cardinality": 1000, "element_cardinality_is_exact": False},
        total_records=25,
    )
    assert element_terms(content) == [("ElementDistinctValueCountEstimate", "1000")]


def test_row_count_is_still_capped_alongside_it():
    content = get_xml_for(
        stats={
            "cardinality": 1000,
            "cardinality_is_exact": False,
            "element_cardinality": 1000,
            "element_cardinality_is_exact": False,
        },
        total_records=25,
    )
    assert row_terms(content) == [("DistinctValueCountEstimate", "25")]
    assert element_terms(content) == [("ElementDistinctValueCountEstimate", "1000")]


def test_zero_and_missing_element_counts_emit_nothing():
    # 0 from the sketch means "unknown", not "no elements".
    assert element_terms(get_xml_for(stats={"element_cardinality": 0})) == []
    assert element_terms(get_xml_for(stats={"element_cardinality": None})) == []
    assert element_terms(get_xml_for(stats={})) == []


def test_the_two_counts_are_separate_annotations():
    """A column can report both, and they are different numbers."""
    content = get_xml_for(
        stats={
            "cardinality": 40,
            "cardinality_is_exact": True,
            "element_cardinality": 7,
            "element_cardinality_is_exact": True,
        }
    )
    assert row_terms(content) == [("DistinctValueCount", "40")]
    assert element_terms(content) == [("ElementDistinctValueCount", "7")]
