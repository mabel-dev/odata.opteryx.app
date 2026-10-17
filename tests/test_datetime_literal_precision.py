"""A datetime literal finer than microseconds is refused with a precision message.

The grammar accepts up to 12 fractional-second digits, as Edm precision allows,
but the engine stores microseconds and its ISO parser reads at most 6. A literal
with 7 or more used to fail parsing silently and fall back to a string, which
the engine then reported as a string-vs-timestamp mismatch -- and the error
mapper turned that into advice to drop quotes the caller never wrote.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from app.odata.expression_parser import ODataExpressionParser


@pytest.mark.parametrize(
    "filter_expr, kind",
    [
        ("published_at ge 2026-07-01T00:00:00.1234567Z", "datetime"),
        ("published_at ge 2026-07-01T00:00:00.123456789012Z", "datetime"),
        ("published_at ge 2026-07-01T00:00:00.1234567+05:30", "datetime"),
        ("start_time lt 09:30:00.1234567", "time"),
    ],
)
def test_more_than_six_fractional_digits_says_so(filter_expr, kind):
    with pytest.raises(ValueError) as raised:
        ODataExpressionParser().parse_filter(filter_expr)

    message = str(raised.value)
    assert f"Invalid {kind} literal" in message
    assert "at most 6 fractional-second digits" in message
    assert "quote" not in message


@pytest.mark.parametrize(
    "filter_expr",
    [
        "published_at ge 2026-07-01T00:00:00Z",
        "published_at ge 2026-07-01T00:00:00.1Z",
        "published_at ge 2026-07-01T00:00:00.123456Z",
        "start_time lt 09:30:00.123456",
    ],
)
def test_up_to_six_fractional_digits_still_parse(filter_expr):
    assert ODataExpressionParser().parse_filter(filter_expr) is not None


def test_compute_reports_the_same_message():
    with pytest.raises(ValueError) as raised:
        ODataExpressionParser().parse_compute("published_at ge 2026-07-01T00:00:00.1234567Z as recent")

    assert "at most 6 fractional-second digits" in str(raised.value)
