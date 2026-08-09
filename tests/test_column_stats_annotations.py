"""Tests for how catalog column bounds become ``Custom.Statistics.*`` annotations.

Two failures these pin down, both of which reached clients as confident lies:

1. A column the manifest computed no bounds for carries ``-(1 << 63)`` in its
   min/max slots, not a null. Every unsigned column was in that state, so an
   IPv4 property advertised a min and max of -9223372036854775808.
2. An IPv4 column's bounds are the raw UINT32 addresses. Rendered as numbers
   they contradict the dotted-decimal rows the same property serves.
"""

from app.odata.column_stats import MISSING_BOUND
from app.odata.column_stats import ipv4_address
from app.odata.column_stats import ipv4_enclosing_cidr
from app.odata.column_stats import is_ipv4_source
from app.odata.column_stats import usable_bound


class TestMissingBoundSentinel:
    def test_the_sentinel_is_not_a_bound(self):
        # -(1 << 63) in a manifest slot means "no bound was computed", and the
        # annotation is then absent -- the same thing a client sees for a
        # column whose statistics were never computed at all.
        assert usable_bound(MISSING_BOUND) is None
        assert MISSING_BOUND == -9223372036854775808

    def test_real_bounds_pass_through_unchanged(self):
        assert usable_bound(0) == 0
        assert usable_bound(-1) == -1
        assert usable_bound(MISSING_BOUND + 1) == MISSING_BOUND + 1
        assert usable_bound("apple") == "apple"
        assert usable_bound(1.5) == 1.5

    def test_absent_bounds_stay_absent(self):
        assert usable_bound(None) is None


class TestIpv4SourceDetection:
    def test_the_original_type_annotation_identifies_address_columns(self):
        assert is_ipv4_source("IPV4")
        assert is_ipv4_source("ipv4")

    def test_other_uint32_columns_are_not_addresses(self):
        # IPv4 is physically UINT32; only the logical type makes it an address.
        assert not is_ipv4_source("UINT32")
        assert not is_ipv4_source("INT64")
        assert not is_ipv4_source("VARCHAR")
        assert not is_ipv4_source(None)
        assert not is_ipv4_source("")


class TestIpv4Addresses:
    def test_bounds_render_as_the_rows_do(self):
        assert ipv4_address(0) == "0.0.0.0"
        assert ipv4_address(3221225985) == "192.0.2.1"
        assert ipv4_address(167772167) == "10.0.0.7"
        assert ipv4_address(4294967295) == "255.255.255.255"

    def test_values_that_are_not_addresses_are_declined(self):
        # Left to the caller rather than coerced into a plausible-looking but
        # wrong address.
        assert ipv4_address(-1) is None
        assert ipv4_address(1 << 32) is None
        assert ipv4_address("192.0.2.1") is None
        assert ipv4_address(1.5) is None
        assert ipv4_address(None) is None
        assert ipv4_address(True) is None


class TestEnclosingCidr:
    def test_a_single_address_is_a_slash_32(self):
        assert ipv4_enclosing_cidr(3221225985, 3221225985) == "192.0.2.1/32"

    def test_the_block_covers_the_whole_range(self):
        # 192.0.2.1 .. 192.0.2.200 share their first 24 bits.
        assert ipv4_enclosing_cidr(3221225985, 3221226184) == "192.0.2.0/24"
        # 10.0.0.0 .. 10.0.1.255 needs one more bit of host space.
        assert ipv4_enclosing_cidr(167772160, 167772671) == "10.0.0.0/23"

    def test_a_range_spanning_the_space_is_a_slash_0(self):
        # Not a failure to summarise: a column holding both 0.0.0.0 and
        # 255.255.255.255 genuinely has no tighter enclosing block.
        assert ipv4_enclosing_cidr(0, 4294967295) == "0.0.0.0/0"
        # Two addresses either side of the top bit force the same answer.
        assert ipv4_enclosing_cidr(167772160, 4110417921) == "0.0.0.0/0"

    def test_bounds_arriving_reversed_still_give_the_enclosing_block(self):
        assert ipv4_enclosing_cidr(3221226184, 3221225985) == "192.0.2.0/24"

    def test_no_block_without_two_real_addresses(self):
        assert ipv4_enclosing_cidr(None, 3221225985) is None
        assert ipv4_enclosing_cidr(3221225985, None) is None
        assert ipv4_enclosing_cidr(-1, 3221225985) is None
        assert ipv4_enclosing_cidr(3221225985, 1 << 32) is None
        assert ipv4_enclosing_cidr("10.0.0.7", "10.0.0.9") is None
