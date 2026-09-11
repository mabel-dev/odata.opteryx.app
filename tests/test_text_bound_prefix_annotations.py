"""A TEXT column's bounds in per-dataset $metadata.

A text column has no `Custom.Statistics.Min`/`Max`, and cannot. Its manifest
bound is an ORDINAL KEY built from the value's first eight bytes (see
`draken/ops/ordinalize.h`), so what a reader can recover is a PREFIX -- which is
a sound lower bound and an unsound upper one. Publishing a prefix as `Max` would
state as the column's maximum a string that is not in the column.

So the catalog hands prefixes through a channel of their own
(`describe()`'s `min_display`/`max_display`) and this service names them apart:
`MinPrefix`/`MaxPrefix`. A client that does not know those terms ignores them,
which is the correct outcome; a client that reads `Max` can still trust it.

Before this, `min_display`/`max_display` were unreachable in the catalog -
nothing ever wrote the accumulator they were read from - so every text column
described itself with no bound whatsoever.
"""

from tests.test_ipv4_statistics_annotations import get_xml_for
from tests.test_ipv4_statistics_annotations import stat_terms


class TestTextPrefixes:
    def test_prefixes_are_emitted_for_a_text_column(self):
        terms = stat_terms(
            get_xml_for(
                column_type="VARCHAR",
                stats={"min": None, "max": None, "min_display": "Earth", "max_display": "Venus"},
            )
        )
        assert terms["MinPrefix"] == "Earth"
        assert terms["MaxPrefix"] == "Venus"

    def test_a_prefix_is_never_published_as_a_bound(self):
        """The whole reason for a separate term."""
        terms = stat_terms(
            get_xml_for(
                column_type="VARCHAR",
                stats={"min": None, "max": None, "min_display": "Earth", "max_display": "Venus"},
            )
        )
        assert "Min" not in terms
        assert "Max" not in terms

    def test_one_bound_alone_is_still_emitted(self):
        terms = stat_terms(
            get_xml_for(
                column_type="VARCHAR",
                stats={"min": None, "max": None, "min_display": "Earth", "max_display": None},
            )
        )
        assert terms["MinPrefix"] == "Earth"
        assert "MaxPrefix" not in terms

    def test_no_prefixes_means_no_terms(self):
        """A dataset described before this channel existed, or a column whose
        every value is null. Absent, not empty: an empty String annotation is a
        claim that the bound is the empty string."""
        terms = stat_terms(get_xml_for(column_type="VARCHAR", stats={"min": None, "max": None}))
        assert "MinPrefix" not in terms
        assert "MaxPrefix" not in terms

    def test_a_numeric_column_carries_bounds_not_prefixes(self):
        terms = stat_terms(get_xml_for(column_type="INT64", stats={"min": 1, "max": 9}))
        assert terms["Min"] == "1"
        assert terms["Max"] == "9"
        assert "MinPrefix" not in terms
