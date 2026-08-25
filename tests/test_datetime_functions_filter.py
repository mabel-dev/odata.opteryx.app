"""OData v4 datetime functions in `$filter` -- `now()`, the date component
functions, and duration arithmetic.

Without a clock a caller cannot express a relative window at all: "the last 30
days" has to be written as a hardcoded literal range, which is silently wrong
the moment the same query is reused on another day. `now()` is what closes that,
and `now() sub duration'P30D'` is the form that actually answers the question --
so the interval literal is covered here too, not as a separate concern.

Three of these were name-for-name mappings onto Opteryx functions that do not
exist (`YEAR`, `MONTH`, ...), so they parsed, bound, and then died in the
expression compiler with "FUNCTION 'YEAR' has no function_ref". Parse-level
assertions alone would not have caught that, so every function is also run
against a real dataset.

`time()`, `mindatetime()` and `maxdatetime()` are part of the OData grammar but
have no Opteryx equivalent; they are refused at the boundary with a message
naming what to write instead, which is also asserted here.

Reported live against `public.security.ghsa_advisories` (`published_at`
TIMESTAMP), which is where `now()` was confirmed to work end to end:

    $filter=published_at lt now()   -> @odata.count 34695
    $filter=published_at gt now()   -> @odata.count 0
"""

import datetime
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import opteryx
import pytest
from opteryx.connectors import DiskConnector
from opteryx.constants import ResultType
from rugo.parquet import write_parquet

from app.odata.expression_parser import ODataExpressionParser
from app.odata.odata_to_logical_plan import build_logical_plan

DATASET_DIR = "_tmp_odata_datetime_functions"

# `testdata.*` resolves through the local filesystem rather than the default
# (GCS) connector. Registered here rather than inherited from whichever other
# module happened to import first, so this file passes run on its own.
opteryx.register_workspace("testdata", DiskConnector)

# `n` identifies the row so assertions compare row identity, not a count that
# could match by coincidence. The timestamps deliberately do NOT share an
# hour/minute/second, so hour()/minute()/second() each select a distinct row --
# a fixture with one time of day would pass with the part name ignored.
#
# Rows straddle "now": the last two are far enough in the future that no
# plausible clock puts them in the past, and the first three far enough in the
# past that none of them drifts into the future.
ROWS = [
    (1, datetime.datetime(2021, 3, 4, 5, 6, 7)),
    (2, datetime.datetime(2023, 7, 19, 11, 22, 33)),
    (3, datetime.datetime(2024, 12, 31, 23, 59, 58)),
    (4, datetime.datetime(2098, 1, 15, 8, 30, 15)),
    (5, datetime.datetime(2099, 6, 30, 14, 45, 1)),
]

PAST = sorted(n for n, moment in ROWS if moment.year < 2090)
FUTURE = sorted(n for n, moment in ROWS if moment.year >= 2090)


@pytest.fixture(scope="module")
def dataset():
    """One TIMESTAMP column and one DATE column over the same instants.

    Both are present because the component functions lower to `EXTRACT`, which
    has to accept either -- a TIMESTAMP-only fixture would not have shown
    whether `year(a_date_column)` works.

    Mirrors `test_temporal_literal_domain_e2e`: Parquet is written through
    `rugo`, because opteryx-core has no pyarrow dependency and a pyarrow-based
    fixture would silently skip where it matters.
    """
    repo_root = Path(__file__).resolve().parent.parent
    previous_cwd = Path.cwd()
    os.chdir(repo_root)
    directory = repo_root / "testdata" / DATASET_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    session = opteryx.session()
    tuples = ", ".join(
        f"('{moment.isoformat()}', '{moment.date().isoformat()}', {n})" for n, moment in ROWS
    )
    morsel = next(
        iter(
            session.execute_to_morsels(
                "SELECT CAST(t AS TIMESTAMP) AS published_at, "
                "CAST(d AS DATE) AS published_on, n "
                f"FROM (VALUES {tuples}) AS v(t, d, n)"
            )
        )
    )
    morsel.materialize()
    (directory / "part-0.parquet").write_bytes(write_parquet(morsel))
    try:
        yield f"testdata.{DATASET_DIR}"
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        os.chdir(previous_cwd)


def _odata_rows(dataset_name, filter_expr):
    """Run an OData query end to end; returns the sorted `n` values."""
    plan = build_logical_plan(
        table_name=dataset_name,
        filter_expr=filter_expr,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )
    session = opteryx.session()
    results, result_type = session.execute_logical_plan(plan, connection=session)
    assert result_type == ResultType.TABULAR
    values = []
    for morsel in results:
        morsel.materialize()
        values.extend(morsel.column(b"n").to_pylist())
    return sorted(values)


# ---------------------------------------------------------------------------
# Parsing and lowering
# ---------------------------------------------------------------------------


def test_now_lowers_to_the_opteryx_clock():
    node = ODataExpressionParser().parse_filter("published_at lt now()")

    from opteryx.expression import NodeType

    assert node.node_type == NodeType.COMPARISON_OPERATOR
    assert node.right.node_type == NodeType.FUNCTION
    assert node.right.value == "NOW"
    assert not node.right.parameters


@pytest.mark.parametrize("part", ("year", "month", "day", "hour", "minute", "second"))
def test_date_components_lower_to_extract(part):
    """Opteryx has no YEAR()/MONTH()/... of its own: the part is a VARCHAR
    argument to a single EXTRACT function."""
    node = ODataExpressionParser().parse_filter(f"{part}(published_at) eq 1")

    from opteryx.expression import NodeType

    assert node.left.node_type == NodeType.FUNCTION
    assert node.left.value == "EXTRACT"
    assert node.left.parameters[0].value == part
    assert node.left.parameters[1].source_column == "published_at"


def test_date_lowers_to_a_cast_not_a_function():
    """`DATE` is a type name in Opteryx, and `DATE(x)` is refused outright there,
    so `date()` has to lower to a cast."""
    node = ODataExpressionParser().parse_filter("date(published_at) eq 2024-12-31")

    from opteryx.expression import NodeType

    assert node.left.node_type == NodeType.CAST
    assert node.left.value == "DATE"
    assert node.left.left.source_column == "published_at"


def test_duration_lowers_to_the_months_microseconds_pair():
    """INTERVAL literals are a `(months, microseconds)` pair in Opteryx, the same
    physical-encoding contract the DATE and TIMESTAMP literals follow. A
    timedelta here reached the expression compiler and failed there."""
    node = ODataExpressionParser().parse_filter("published_at ge now() sub duration'P30D'")

    assert node.right.right.value == (0, 30 * 86_400_000_000)


@pytest.mark.parametrize(
    "literal, expected",
    (
        ("P30D", (0, 30 * 86_400_000_000)),
        ("PT12H", (0, 12 * 3_600_000_000)),
        ("PT90M", (0, 90 * 60_000_000)),
        ("PT1.5S", (0, 1_500_000)),
        ("-P7D", (0, -7 * 86_400_000_000)),
        # Years and months stay in the months half rather than being approximated
        # as 365.25/30.44 days -- month arithmetic is calendar-aware in the
        # engine, day arithmetic is not, so `P1Y` has to mean a calendar year.
        ("P1Y", (12, 0)),
        ("P18M", (18, 0)),
        ("P1Y6M", (18, 0)),
    ),
)
def test_duration_literals_encode_exactly(literal, expected):
    node = ODataExpressionParser().parse_filter(f"published_at ge now() sub duration'{literal}'")

    assert node.right.right.value == expected


# ---------------------------------------------------------------------------
# Functions in the grammar with no Opteryx equivalent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expression, alternative",
    (
        ("time(published_at) eq 12:00:00", "hour()"),
        ("published_at ge mindatetime()", "lower bound"),
        ("published_at le maxdatetime()", "upper bound"),
    ),
)
def test_unsupported_datetime_functions_name_what_to_write_instead(expression, alternative):
    """These parse -- they are real OData v4 functions -- so without an explicit
    refusal they reached the binder and surfaced as
    "FUNCTION 'TIME' has no function_ref", which tells a caller nothing."""
    with pytest.raises(ValueError) as caught:
        ODataExpressionParser().parse_filter(expression)

    message = str(caught.value)
    assert "not supported" in message
    assert alternative in message


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def test_now_partitions_the_rows_at_the_current_instant(dataset):
    """Ground truth is the fixture's own split around 2090, not the engine's
    idea of the time -- the two halves must also be exhaustive."""
    assert _odata_rows(dataset, "published_at lt now()") == PAST
    assert _odata_rows(dataset, "published_at gt now()") == FUTURE


def test_now_works_against_a_date_column(dataset):
    assert _odata_rows(dataset, "published_on lt now()") == PAST


def test_now_is_a_single_instant_for_the_whole_query(dataset):
    """The planner folds NOW() to one timestamp per plan, so a row cannot fall
    on both sides of two comparisons against it within one query."""
    assert _odata_rows(dataset, "published_at lt now() and published_at gt now()") == []


@pytest.mark.parametrize("column", ("published_at", "published_on"))
@pytest.mark.parametrize("part", ("year", "month", "day"))
def test_date_components_select_the_right_row(dataset, column, part):
    for n, moment in ROWS:
        value = getattr(moment, part)
        expected = sorted(m for m, other in ROWS if getattr(other, part) == value)
        assert _odata_rows(dataset, f"{part}({column}) eq {value}") == expected, (
            f"{part}({column}) eq {value} (row {n})"
        )


@pytest.mark.parametrize("part", ("hour", "minute", "second"))
def test_time_components_select_the_right_row(dataset, part):
    """Only meaningful on the TIMESTAMP column; the DATE column has no clock."""
    for n, moment in ROWS:
        value = getattr(moment, part)
        expected = sorted(m for m, other in ROWS if getattr(other, part) == value)
        assert _odata_rows(dataset, f"{part}(published_at) eq {value}") == expected, (
            f"{part}(published_at) eq {value} (row {n})"
        )


def test_date_narrows_a_timestamp_to_its_day(dataset):
    """`date(published_at) eq <day>` has to match the row whatever its time of
    day -- the whole difference between it and `published_at eq <day>`."""
    for n, moment in ROWS:
        day = moment.date().isoformat()
        assert _odata_rows(dataset, f"date(published_at) eq {day}") == [n]


def test_relative_window_selects_the_same_rows_as_an_equivalent_literal(dataset):
    """The question `now()` exists to answer. The ground truth is computed here
    from the same clock the engine reads, so the assertion is about the
    arithmetic rather than about which rows happen to be recent."""
    boundary = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(
        days=30
    )
    expected = sorted(n for n, moment in ROWS if moment >= boundary)

    assert _odata_rows(dataset, "published_at ge now() sub duration'P30D'") == expected


def test_relative_window_composes_with_other_predicates(dataset):
    expected = sorted(set(FUTURE) & {4})

    assert (
        _odata_rows(dataset, "published_at ge now() sub duration'P30D' and n eq 4") == expected
    )


def test_duration_added_to_the_clock_moves_forward(dataset):
    """`add` and `sub` must move in opposite directions; a duration whose sign
    was dropped would pass a `sub`-only test."""
    assert _odata_rows(dataset, "published_at ge now() add duration'P36500D'") == []
    assert sorted(
        _odata_rows(dataset, "published_at lt now() add duration'P36500D'")
    ) == sorted(n for n, _ in ROWS)
