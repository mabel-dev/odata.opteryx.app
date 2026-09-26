"""Regression tests: a DATE column filtered with a datetime literal must not
silently return an empty result.

Reported live against `public.security.cisa_kev` (1657 rows, `date_added` DATE,
data through 2026-08-03):

    $filter=date_added ge 2025-01-01              -> @odata.count 418   correct
    $filter=date_added ge 2025-01-01T00:00:00Z    -> @odata.count 0     HTTP 200

Zero rows and HTTP 200 is the worst possible failure for this service: a broken
query is indistinguishable from a legitimately empty result, so a consumer that
expires records against a query result silently wipes its list.

Where it actually goes wrong
----------------------------
Not in the parser, and not in row-level evaluation. `DateTime.to_opteryx_node`
correctly encodes `2025-01-01T00:00:00Z` as a TIMESTAMP literal holding
microseconds-since-epoch, and the filter Opteryx evaluates over the rows gives
the right answer - `date_added ge 2025-01-01T00:00:00Z or date_added lt
1900-01-01` returns the full 418 live, because an OR is not pushed down.

The loss happens in manifest file pruning. File bounds store a DATE column's raw
physical integer - days since the epoch - and the pushed-down predicate carries
microseconds, so `max_ < v` compares 20668 against 1735689600000000, holds for
every file, and the scan reads nothing at all.

That makes this an integration test by necessity: the two halves are individually
correct and only the join between them is wrong, so it asserts on the OData plan's
real predicate node driving a manifest whose bounds are encoded the way a
catalog-backed DATE column really encodes them. `opteryx.models.Manifest` is
where the fix lives (`_temporal_domain_mismatch`); this pins the contract the
OData surface depends on, and fails against any opteryx-core without it.

The reverse pairing is covered too. A TIMESTAMP column against a date-only
literal fails the same way with the operators inverted - `published_at lt
2026-07-01` returned 0 live while `published_at lt 2026-07-01T00:00:00Z`
returned 32841 - which is why "TIMESTAMP column + date-only literal works" held
up as a control case: it was only ever checked with `ge`.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from opteryx.models.file_entry import FileEntry
from opteryx.models.manifest import Manifest
from opteryx.planner.logical_planner import LogicalPlanStepType
from opteryx.planner.plan_context import PlanContext
from opteryx.types.logical_type import DATE, TIMESTAMP
from opteryx.types.schema import ColumnDescriptor, RelationDescriptor

from app.odata.odata_to_logical_plan import build_logical_plan

US_PER_DAY = 86_400_000_000

# 2025-01-01 and 2026-08-03 - the live table's filter boundary and its newest
# row - in the two raw domains a temporal bound can be stored in.
DAY_2025_01_01 = 20089
DAY_2026_08_03 = 20668
US_2025_01_01 = DAY_2025_01_01 * US_PER_DAY
US_2026_08_03 = DAY_2026_08_03 * US_PER_DAY

# Every comparison the OData grammar can put in front of a temporal literal.
# Live, `lt`/`le` answered correctly while `ge`/`gt`/`eq` returned nothing, so a
# test that checks one operator proves nothing about the others.
OPERATORS = ("eq", "ne", "gt", "ge", "lt", "le")


def _filter_condition(filter_expr, table_name="public.security.cisa_kev"):
    """The predicate node the OData layer hands Opteryx for `filter_expr`."""
    plan = build_logical_plan(table_name=table_name, filter_expr=filter_expr)
    for _, node in plan.nodes(True):
        if node.node_type == LogicalPlanStepType.Filter:
            return node.condition
    raise AssertionError(f"no Filter node in the plan for {filter_expr!r}")


def _manifest(column_type, lower, upper, plan_context, column_name="date_added"):
    """One file whose bounds straddle the filter boundary, so a correct
    comparison keeps it and only an incorrect one drops it.

    Built the way the binder builds a scan's manifest (binder/dataset.py
    `visit_scan`): the source describes the relation with a RelationDescriptor,
    the Manifest is built over that description, and the query's ColumnTable
    binds it - the manifest's statistics are keyed by BOUND column identity, so
    it reads the bound schema from then on."""
    descriptor = RelationDescriptor(
        name="cisa_kev",
        columns=[ColumnDescriptor(name=column_name, column_type=column_type)],
    )
    file_entry = FileEntry(
        file_path="data/cisa_kev/0000.parquet",
        file_format="PARQUET",
        record_count=1657,
        file_size_in_bytes=0,
        lower_bounds={0: lower},
        upper_bounds={0: upper},
    )
    # `bounds_are_ordinal=True` is what the catalog connector the hosted service
    # runs on builds, and `ColumnType.ordinalize` is the identity for
    # DATE32/TIMESTAMP64 - the ordinal key IS the raw tick - so the mismatch
    # survives ordinalisation unchanged. Set explicitly rather than defaulted,
    # so this stays the shape production actually executes.
    manifest = Manifest(files=[file_entry], schema=descriptor, bounds_are_ordinal=True)
    manifest.schema = plan_context.columns.bind_relation(descriptor, "cisa_kev")
    return manifest


def _surviving_files(column_type, bounds, filter_expr):
    # One query's planning context for the manifest and the prune, as in a
    # real query: the binder binds the scan and the optimizer prunes in the
    # same PlanContext.
    plan_context = PlanContext()
    manifest = _manifest(column_type, *bounds, plan_context=plan_context)
    # `prune_files` does not mutate: it returns the pruned manifest and leaves
    # this one whole. Discarding the return value read the ORIGINAL file list,
    # which made every "reads the data" assertion pass vacuously and every
    # "still prunes" assertion impossible - the test could not see a prune at
    # all, so it could not have caught the regression it exists to pin.
    return manifest.prune_files([_filter_condition(filter_expr)], plan_context=plan_context).files


# ---------------------------------------------------------------------------
# DATE column + datetime literal - the reported bug.
# ---------------------------------------------------------------------------


def test_date_column_with_datetime_literal_reads_the_data():
    """`date_added ge 2025-01-01T00:00:00Z` must not prune the only file that
    holds the 418 matching rows. This is the reported query, verbatim."""
    files = _surviving_files(
        DATE,
        (DAY_2025_01_01, DAY_2026_08_03),
        "date_added ge 2025-01-01T00:00:00Z",
    )

    assert len(files) == 1, (
        "the file holding every matching row was pruned - the service would "
        "return HTTP 200 with an empty value array"
    )


@pytest.mark.parametrize("operator", OPERATORS)
def test_date_column_with_datetime_literal_reads_the_data_for_every_operator(operator):
    files = _surviving_files(
        DATE,
        (DAY_2025_01_01, DAY_2026_08_03),
        f"date_added {operator} 2025-01-01T00:00:00Z",
    )

    assert len(files) == 1, f"'{operator}' pruned a file that spans the literal"


@pytest.mark.parametrize("operator", OPERATORS)
@pytest.mark.parametrize(
    "bounds",
    (
        (DAY_2025_01_01 - 400, DAY_2025_01_01 - 1),  # entirely before the boundary
        (DAY_2025_01_01, DAY_2026_08_03),  # straddling it
        (DAY_2026_08_03, DAY_2026_08_03 + 100),  # entirely after it
    ),
)
def test_datetime_literal_never_prunes_a_file_the_date_only_literal_keeps(bounds, operator):
    """The guarantee the docs make: the two literal forms are the same query.

    Stated as a superset, because pruning is an optimisation and the two forms
    need not eliminate the same files - the datetime form declines the pushdown
    where the date-only form can still use it. What they may never do is differ
    in the rows they can return, and a file the date-only form keeps holds rows
    the datetime form must be able to see.
    """
    date_only = _surviving_files(DATE, bounds, f"date_added {operator} 2025-01-01")
    datetime_form = _surviving_files(DATE, bounds, f"date_added {operator} 2025-01-01T00:00:00Z")

    kept_by_date_only = {f.file_path for f in date_only}
    kept_by_datetime = {f.file_path for f in datetime_form}

    assert kept_by_date_only <= kept_by_datetime, (
        f"'{operator}' with a datetime literal pruned files the date-only form "
        f"reads: {sorted(kept_by_date_only - kept_by_datetime)}"
    )


def test_date_only_literal_still_prunes_an_irrelevant_file():
    """The fix must not be "stop pruning". A date-only literal against a DATE
    column is a same-domain comparison and has to keep eliminating files."""
    files = _surviving_files(
        DATE,
        (DAY_2025_01_01 - 400, DAY_2025_01_01 - 1),
        "date_added ge 2025-01-01",
    )

    assert files == []


# ---------------------------------------------------------------------------
# TIMESTAMP column + date-only literal - the same defect, operators inverted.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operator", OPERATORS)
def test_timestamp_column_with_date_only_literal_reads_the_data(operator):
    files = _surviving_files(
        TIMESTAMP(),
        (US_2025_01_01, US_2026_08_03),
        f"date_added {operator} 2026-01-01",
    )

    assert len(files) == 1, f"'{operator}' pruned a file that spans the literal"


def test_timestamp_column_with_datetime_literal_still_prunes():
    files = _surviving_files(
        TIMESTAMP(),
        (US_2025_01_01, US_2026_08_03),
        "date_added gt 2026-08-03T00:00:00Z",
    )

    assert files == []
