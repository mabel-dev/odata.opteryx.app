"""End-to-end tests for address-column statistics in per-dataset $metadata.

An IPv4 column is physically a UINT32 with a logical descriptor, so its bounds
arrive from the catalog as raw 32-bit integers while its rows are rendered
dotted-decimal. These tests pin the two things that keeps honest: the bounds
are emitted as addresses, and the range is summarised as the smallest CIDR
block enclosing it.

They also pin the missing-bound sentinel: a manifest slot with no computed
bound holds -(1 << 63), which describe() passes straight through, and which
must never reach a client as a statistic.
"""

import re

from fastapi import Response

from app.odata.column_stats import MISSING_BOUND
from app.routes.v4 import interface
from app.routes.v4.metadata_cache import clear_describe_cache
from app.routes.v4.metadata_cache import clear_metadata_cache


class DummyState:
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()
    headers: dict = {}


class FakeDataset:
    """A single-column table whose column type and describe() the test controls."""

    def __init__(self, column_type, stats):
        self._column_type = column_type
        self._stats = stats

    def schema(self):
        column = type("C", (), {"name": "src_ip", "type": self._column_type, "nullable": False})
        return type("S", (), {"columns": [column]})

    def describe(self):
        return {"src_ip": dict(self._stats)} if self._stats else {}

    def snapshot(self):
        return None


def get_xml_for(column_type="IPV4", stats=None):
    """Render $metadata for a one-column table with the given type and stats."""

    class FakeCatalog:
        def __init__(self, *args, **kwargs):
            pass

        def load_dataset(self, path):
            return FakeDataset(column_type, stats or {})

        def load_view(self, path):
            raise RuntimeError("not a view")

    # Both caches key on things these tests hold constant, so a previous case's
    # document would otherwise be served for this one.
    clear_metadata_cache()
    clear_describe_cache()
    interface.OpteryxCatalog = FakeCatalog
    res = interface.dataset_metadata(
        DummyRequest(), Response(), workstream="public", collection="examples", dataset="traffic"
    )
    return res.content.decode() if hasattr(res, "content") else res.body.decode()


def stat_terms(content):
    """Every Custom.Statistics.* annotation carrying a String value."""
    return dict(re.findall(r'Term="Custom\.Statistics\.(\w+)"\s+String="([^"]*)"', content))


class TestSentinelBounds:
    def test_the_missing_bound_sentinel_is_not_emitted(self):
        # What every unsigned column looked like before the catalog computed
        # their bounds: a min and max of -9223372036854775808.
        terms = stat_terms(get_xml_for(stats={"min": MISSING_BOUND, "max": MISSING_BOUND}))
        assert "Min" not in terms
        assert "Max" not in terms
        assert "CIDR" not in terms

    def test_a_sentinel_on_a_plain_integer_column_is_also_dropped(self):
        terms = stat_terms(
            get_xml_for(column_type="INT64", stats={"min": MISSING_BOUND, "max": MISSING_BOUND})
        )
        assert "Min" not in terms
        assert "Max" not in terms

    def test_real_integer_bounds_are_unaffected(self):
        terms = stat_terms(get_xml_for(column_type="INT64", stats={"min": -5, "max": 12}))
        assert terms["Min"] == "-5"
        assert terms["Max"] == "12"
        # A non-address column gets no CIDR summary.
        assert "CIDR" not in terms


class TestIpv4Bounds:
    def test_bounds_are_emitted_as_addresses_not_integers(self):
        # 10.0.0.7 .. 10.0.3.200; the raw UINT32s would read as 167772167 and
        # 167773128, contradicting the rows this property serves.
        terms = stat_terms(get_xml_for(stats={"min": 167772167, "max": 167773128}))
        assert terms["Min"] == "10.0.0.7"
        assert terms["Max"] == "10.0.3.200"

    def test_the_range_is_summarised_as_an_enclosing_cidr(self):
        terms = stat_terms(get_xml_for(stats={"min": 167772167, "max": 167773128}))
        assert terms["CIDR"] == "10.0.0.0/22"

    def test_a_single_address_column_is_a_slash_32(self):
        terms = stat_terms(get_xml_for(stats={"min": 3221225985, "max": 3221225985}))
        assert terms["Min"] == "192.0.2.1"
        assert terms["CIDR"] == "192.0.2.1/32"

    def test_a_column_spanning_the_space_is_a_slash_0(self):
        terms = stat_terms(get_xml_for(stats={"min": 0, "max": 4294967295}))
        assert terms["CIDR"] == "0.0.0.0/0"

    def test_no_cidr_when_only_one_bound_is_known(self):
        terms = stat_terms(get_xml_for(stats={"min": 167772167, "max": None}))
        assert terms["Min"] == "10.0.0.7"
        assert "CIDR" not in terms

    def test_distinct_value_count_still_accompanies_the_range(self):
        # NDV comes from the manifest's min-k hashes, which are computed for
        # every hashable column regardless of whether it gets bounds -- so an
        # address column reports both its block and how many addresses are in
        # it.
        content = get_xml_for(
            stats={
                "min": 167772167,
                "max": 167773128,
                "cardinality": 361,
                "cardinality_is_exact": True,
            }
        )
        assert stat_terms(content)["CIDR"] == "10.0.0.0/22"
        assert 'Term="Custom.Statistics.DistinctValueCount" Int="361"' in content
