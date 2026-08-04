"""End-to-end: an OData `$filter` on a temporal column must return the same rows
whichever literal form the caller writes.

This is the executing counterpart to test_date_column_datetime_literal_regression.py,
which asserts on the pruning decision in isolation. Here the OData expression is
parsed, planned, optimised and RUN against real Parquet files with a real
`ANALYZE` manifest, and the rows that come back are compared against a ground
truth computed in Python from the source data.

Ground truth matters more than agreement between the two literal forms. The
failure being guarded against returns an empty result, and two forms can agree
on "nothing" while the engine is completely broken.

Reported live against `public.security.cisa_kev` (1657 rows, `date_added` DATE):

    $filter=date_added ge 2025-01-01            -> @odata.count 418   correct
    $filter=date_added ge 2025-01-01T00:00:00Z  -> @odata.count 0     HTTP 200

and, the same defect with the operators inverted, against a TIMESTAMP column:

    $filter=published_at lt 2026-07-01T00:00:00Z -> @odata.count 32841  correct
    $filter=published_at lt 2026-07-01           -> @odata.count 0      HTTP 200

The cause is in `opteryx.models.Manifest`, not in this repo: file bounds hold a
temporal column's raw physical integer (DATE32 days, TIMESTAMP64 ticks) and the
pushed-down predicate carries its own, so days get compared against microseconds
and every file is pruned. The OData layer is what a user reaches it through, so
the contract is pinned from this side too - these fail against any opteryx-core
without the `_temporal_domain_mismatch` guard.

The fixture writes Parquet through `rugo` rather than pyarrow: opteryx-core
deliberately dropped its pyarrow dependency (see app/routes/v4/interface.py), so
a pyarrow-based fixture would silently skip everywhere it matters.
"""

import datetime
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import opteryx
import pytest
from opteryx.constants import ResultType
from rugo.parquet import write_parquet

from app.odata.odata_to_logical_plan import build_logical_plan

DATASET_DIR = "_tmp_odata_temporal_domain"

# Three files with disjoint, ordered ranges - the shape that makes file-level
# pruning both possible and observable. `n` identifies the row, so assertions
# compare row identity rather than a count that could match by coincidence.
ROWS = [
    (1, datetime.date(2023, 1, 1)),
    (2, datetime.date(2023, 6, 15)),
    (3, datetime.date(2023, 12, 31)),
    (4, datetime.date(2025, 1, 1)),
    (5, datetime.date(2025, 6, 15)),
    (6, datetime.date(2025, 12, 31)),
    (7, datetime.date(2026, 4, 1)),
    (8, datetime.date(2026, 6, 15)),
    (9, datetime.date(2026, 8, 3)),
]
FILE_SIZE = 3
TIME_OF_DAY = datetime.time(12, 30, 15)

# Before all data, exactly on a file's first row, mid-file, exactly on the last
# row, and after all data.
BOUNDARIES = ("2022-01-01", "2025-01-01", "2025-06-15", "2026-08-03", "2027-01-01")

# Every comparison the OData grammar puts in front of a temporal literal. Live,
# `lt`/`le` answered correctly while `ge`/`gt`/`eq` returned nothing, so a test
# that checks one operator proves nothing about the others.
OPERATORS = ("eq", "ne", "gt", "ge", "lt", "le")

_PYTHON_OPERATOR = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
}


def _values_sql(batch):
    tuples = ", ".join(
        f"('{d.isoformat()}', '{d.isoformat()}T{TIME_OF_DAY.isoformat()}', {n})" for n, d in batch
    )
    return (
        "SELECT CAST(d AS DATE) AS date_added, "
        "CAST(t AS TIMESTAMP) AS published_at, n "
        f"FROM (VALUES {tuples}) AS v(d, t, n)"
    )


@pytest.fixture(scope="module")
def dataset():
    """A three-file, ANALYZE'd dataset, addressed the way the DiskConnector
    addresses one: a path relative to the repo root. `chdir` is explicit rather
    than assumed, because an absolute path would be lower-cased by the connector
    and this checkout does not live under an all-lowercase path."""
    repo_root = Path(__file__).resolve().parent.parent
    previous_cwd = Path.cwd()
    os.chdir(repo_root)
    directory = repo_root / "testdata" / DATASET_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    session = opteryx.session()
    for index in range(0, len(ROWS), FILE_SIZE):
        morsel = next(
            iter(session.execute_to_morsels(_values_sql(ROWS[index : index + FILE_SIZE])))
        )
        morsel.materialize()
        (directory / f"part-{index // FILE_SIZE}.parquet").write_bytes(write_parquet(morsel))
    name = f"testdata.{DATASET_DIR}"
    list(session.execute_to_morsels(f"ANALYZE TABLE {name}"))
    try:
        yield name
    finally:
        shutil.rmtree(directory, ignore_errors=True)
        os.chdir(previous_cwd)


def _odata_rows(dataset_name, filter_expr, **kwargs):
    """Run an OData query end to end; returns the sorted `n` values."""
    plan = build_logical_plan(table_name=dataset_name, filter_expr=filter_expr, **kwargs)
    session = opteryx.session()
    results, result_type = session.execute_logical_plan(plan, connection=session)
    assert result_type == ResultType.TABULAR
    values = []
    for morsel in results:
        morsel.materialize()
        values.extend(morsel.column(b"n").to_pylist())
    return sorted(values)


def _expected(column, operator, boundary, time_of_day=None):
    """Ground truth from ROWS, not from the engine.

    A DATE value compared against a datetime is promoted to midnight on that
    date. That is what makes a midnight literal equivalent to the date-only form
    - and a non-midnight literal deliberately not equivalent.
    """
    limit = datetime.datetime.combine(
        datetime.date.fromisoformat(boundary), time_of_day or datetime.time(0, 0, 0)
    )
    compare = _PYTHON_OPERATOR[operator]
    matched = []
    for n, date in ROWS:
        clock = datetime.time(0, 0, 0) if column == "date_added" else TIME_OF_DAY
        if compare(datetime.datetime.combine(date, clock), limit):
            matched.append(n)
    return sorted(matched)


# ---------------------------------------------------------------------------
# DATE column + datetime literal - the reported failure.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("operator", OPERATORS)
def test_date_column_both_literal_forms_return_the_right_rows(dataset, operator, boundary):
    date_form = _odata_rows(dataset, f"date_added {operator} {boundary}")
    datetime_form = _odata_rows(dataset, f"date_added {operator} {boundary}T00:00:00Z")
    expected = _expected("date_added", operator, boundary)

    assert date_form == expected, f"date-only literal wrong for '{operator} {boundary}'"
    assert datetime_form == expected, f"datetime literal wrong for '{operator} {boundary}'"


# ---------------------------------------------------------------------------
# TIMESTAMP column + date-only literal - same defect, opposite operators.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("operator", OPERATORS)
def test_timestamp_column_both_literal_forms_return_the_right_rows(dataset, operator, boundary):
    datetime_form = _odata_rows(dataset, f"published_at {operator} {boundary}T00:00:00Z")
    date_form = _odata_rows(dataset, f"published_at {operator} {boundary}")
    expected = _expected("published_at", operator, boundary)

    assert datetime_form == expected, f"datetime literal wrong for '{operator} {boundary}'"
    assert date_form == expected, f"date-only literal wrong for '{operator} {boundary}'"


# ---------------------------------------------------------------------------
# Offsets and precision. A `Z` literal, an explicit `+00:00`, and a fractional
# second all have to land in the same place; a non-UTC offset has to shift.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    (
        "2025-06-15T00:00:00Z",
        "2025-06-15T00:00:00+00:00",
        "2025-06-15T00:00:00.0Z",
        "2025-06-15T00:00:00.00000Z",
        "2025-06-15T00:00:00.000000",
    ),
)
def test_equivalent_utc_spellings_agree(dataset, literal):
    assert _odata_rows(dataset, f"date_added ge {literal}") == [5, 6, 7, 8, 9]


def test_non_utc_offset_shifts_the_boundary(dataset):
    # 2025-06-15T00:00:00-05:00 is 05:00 UTC on the 15th, which is AFTER the
    # DATE column's midnight promotion of that same day - so row 5 drops out.
    assert _odata_rows(dataset, "date_added ge 2025-06-15T00:00:00-05:00") == [6, 7, 8, 9]
    assert _odata_rows(dataset, "date_added ge 2025-06-15T00:00:00+05:00") == [5, 6, 7, 8, 9]


@pytest.mark.xfail(
    strict=True,
    reason="SEPARATE, PRE-EXISTING BUG in opteryx-core: _parse_iso_parts "
    "(compiled/functions/timestamp.pyx) rejects any ISO timestamp longer than 26 "
    "characters, so 6 fractional digits plus a timezone designator - i.e. exactly "
    "what Python's datetime.isoformat() emits - fails to parse. try_parse_iso then "
    "returns None, DateTime.to_opteryx_node falls back to a VARCHAR literal, and "
    "the binder rejects it with HTTP 400. Loud, not silent, but wrong.",
)
@pytest.mark.parametrize(
    "literal",
    (
        "2025-06-15T00:00:00.000000Z",
        "2025-06-15T00:00:00.000000+00:00",
    ),
)
def test_microsecond_precision_with_an_offset_is_accepted(dataset, literal):
    assert _odata_rows(dataset, f"date_added ge {literal}") == [5, 6, 7, 8, 9]


def test_non_midnight_literal_keeps_midnight_promotion_semantics(dataset):
    # 2025-06-15 (midnight) is strictly before 2025-06-15T12:00:00, so row 5
    # drops out of `ge` and joins `lt`. The two literal forms are NOT equivalent
    # here and must not be bent into equivalence by any coercion.
    assert _odata_rows(dataset, "date_added ge 2025-06-15T12:00:00Z") == [6, 7, 8, 9]
    assert _odata_rows(dataset, "date_added lt 2025-06-15T12:00:00Z") == [1, 2, 3, 4, 5]
    assert _odata_rows(dataset, "date_added eq 2025-06-15T12:00:00Z") == []


# ---------------------------------------------------------------------------
# Compound filters. AND-conjuncts are pushed down individually, so a mixed pair
# has to prune on the safe half and still read what the unsafe half needs.
# ---------------------------------------------------------------------------


def test_and_of_mixed_literal_forms(dataset):
    rows = _odata_rows(dataset, "date_added ge 2025-01-01 and date_added lt 2026-06-15T00:00:00Z")

    assert rows == [4, 5, 6, 7]


def test_and_of_two_datetime_literals(dataset):
    rows = _odata_rows(
        dataset, "date_added ge 2025-01-01T00:00:00Z and date_added le 2026-06-15T00:00:00Z"
    )

    assert rows == [4, 5, 6, 7, 8]


def test_or_of_datetime_literals(dataset):
    # An OR is not pushed down, so it was already correct pre-fix - it is how the
    # defect was localised to pruning rather than to evaluation. It stays as the
    # control that the two now agree.
    rows = _odata_rows(
        dataset, "date_added ge 2026-01-01T00:00:00Z or date_added lt 2023-06-15T00:00:00Z"
    )

    assert rows == [1, 7, 8, 9]


def test_not_of_a_datetime_literal(dataset):
    rows = _odata_rows(dataset, "not (date_added ge 2026-01-01T00:00:00Z)")

    assert rows == [1, 2, 3, 4, 5, 6]


def test_filter_across_both_temporal_columns(dataset):
    rows = _odata_rows(dataset, "date_added ge 2025-01-01T00:00:00Z and published_at lt 2026-06-15")

    assert rows == [4, 5, 6, 7]


# ---------------------------------------------------------------------------
# The rest of the OData surface reads through the same pruned file list, so a
# wrong prune surfaces as a wrong page or a wrong scalar rather than as an
# obviously missing row.
# ---------------------------------------------------------------------------


def test_select_projection_over_a_datetime_literal(dataset):
    rows = _odata_rows(dataset, "date_added ge 2025-01-01T00:00:00Z", select_cols="n")

    assert rows == [4, 5, 6, 7, 8, 9]


def test_top_without_an_orderby_returns_a_full_page(dataset):
    rows = _odata_rows(dataset, "date_added ge 2025-01-01T00:00:00Z", top=3)

    assert len(rows) == 3
    assert set(rows) <= {4, 5, 6, 7, 8, 9}


@pytest.mark.xfail(
    strict=True,
    reason="SEPARATE, PRE-EXISTING BUG in opteryx-core's TopNManifestPruningStrategy: "
    "$orderby + $top over a filtered scan prunes files by the sort column's bounds "
    "and each file's TOTAL record_count, ignoring that the filter will discard some "
    "of those rows - so files holding the only matching rows get dropped and the "
    "page comes back short or empty. Reproduces on an unmodified build with no "
    "temporal types involved; see opteryx-core "
    "tests/storage/test_temporal_domain_manifest_pruning.py.",
)
def test_skip_pages_through_the_full_filtered_set(dataset):
    first = _odata_rows(dataset, "date_added ge 2025-01-01T00:00:00Z", orderby_expr="n asc", top=3)
    second = _odata_rows(
        dataset, "date_added ge 2025-01-01T00:00:00Z", orderby_expr="n asc", top=3, skip=3
    )

    assert first == [4, 5, 6]
    assert second == [7, 8, 9]


def test_count_over_a_datetime_literal(dataset):
    from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

    builder = ODataLogicalPlanBuilder(dataset)
    plan = builder.build(filter_expr="date_added ge 2025-01-01T00:00:00Z", count_only=True)
    session = opteryx.session()
    results, _ = session.execute_logical_plan(plan, connection=session)
    counts = []
    for morsel in results:
        morsel.materialize()
        name = morsel.column_names[0]
        counts.extend(morsel.column(name).to_pylist())

    assert counts == [6]
