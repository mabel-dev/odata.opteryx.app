"""OData v4 `cast(expr, Edm.Type)` in `$filter` and `$compute` (URL Conventions
§5.1.1.10.1).

Without it a caller holding a column in the wrong type -- a numeric code stored
as text, a timestamp compared against a day -- has no way to express the
comparison at all, and the engine's own type-mismatch advice is SQL's
`CAST(column AS type)`, which a `$filter` cannot contain.

Each supported target is run against a real dataset, not only checked at parse
level: a target mapped onto an Opteryx type name the engine does not accept
(`DOUBLE`, `BIGINT`) parses fine and only fails at bind time.

The targets this service cannot produce, the one-argument form (a cast to a
derived entity type, which no entity type here has), and a non-type second
argument are refused at the boundary with a message saying what to write.
"""

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

DATASET_DIR = "_tmp_odata_cast_function"

# See test_datetime_functions_filter for why the workspace is registered here.
opteryx.register_workspace("testdata", DiskConnector)

# `n` identifies the row. The text columns hold values whose string order and
# numeric order disagree ('42' < '7' as text, 42 > 7 as numbers), so a cast that
# silently did not happen selects the wrong row rather than the same one.
ROWS = (
    # n, code, price, published_at,          day_text,     ts_text,               flag
    (1, "42", 9.75, "2024-01-02T03:04:05", "2024-01-02", "2024-01-02T03:04:05", "true"),
    (2, "7", 120.5, "2025-06-07T08:09:10", "2025-06-07", "2025-06-07T08:09:10", "false"),
    # Nothing in the text columns converts. A failed cast is null in OData, so
    # this row must drop out of every comparison rather than fail the query --
    # which is what one empty string in production did.
    (3, "", 0.5, "2026-01-01T00:00:00", "not a date", "", "maybe"),
)


@pytest.fixture(scope="module")
def dataset():
    repo_root = Path(__file__).resolve().parent.parent
    previous_cwd = Path.cwd()
    os.chdir(repo_root)
    directory = repo_root / "testdata" / DATASET_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    session = opteryx.session()
    tuples = ", ".join(
        f"({n}, '{code}', {price}, '{ts}', '{day}', '{ts_text}', '{flag}')"
        for n, code, price, ts, day, ts_text, flag in ROWS
    )
    morsel = next(
        iter(
            session.execute_to_morsels(
                "SELECT n, code, price, CAST(ts AS TIMESTAMP) AS published_at, "
                "day_text, ts_text, flag "
                f"FROM (VALUES {tuples}) AS v(n, code, price, ts, day_text, ts_text, flag)"
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


def _plan(dataset_name, filter_expr=None, compute_expr=None):
    return build_logical_plan(
        table_name=dataset_name,
        filter_expr=filter_expr,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
        compute_expr=compute_expr,
    )


def _execute(plan):
    session = opteryx.session()
    results, result_type = session.execute_logical_plan(plan, connection=session)
    assert result_type == ResultType.TABULAR
    morsels = []
    for morsel in results:
        morsel.materialize()
        morsels.append(morsel)
    return morsels


def _odata_rows(dataset_name, filter_expr):
    """Run a $filter end to end; returns the sorted `n` values."""
    morsels = _execute(_plan(dataset_name, filter_expr=filter_expr))
    return sorted(value for morsel in morsels for value in morsel.column(b"n").to_pylist())


# ---------------------------------------------------------------------------
# Parsing and lowering
# ---------------------------------------------------------------------------


def test_cast_lowers_to_a_cast_node():
    """The type name must not be converted as an argument: it parses as an
    Identifier, which would otherwise become a reference to a column `Int32`."""
    from opteryx.expression import NodeType

    node = ODataExpressionParser().parse_filter("cast(code, Edm.Int32) gt 10")

    assert node.left.node_type == NodeType.CAST
    assert node.left.value == "TRY_INT32"
    assert node.left.left.source_column == "code"


@pytest.mark.parametrize("spelling", ("Edm.Int32", "edm.int32", "'Edm.Int32'"))
def test_type_name_spellings_are_accepted(spelling):
    """Qualified names are matched case-insensitively, and the pre-v4 quoted
    form some clients still send means the same type."""
    node = ODataExpressionParser().parse_filter(f"cast(code, {spelling}) eq 42")

    assert node.left.value == "TRY_INT32"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filter_expr", "expected"),
    (
        # Text -> numbers. As text, '42' gt '10' and '7' gt '10' are both true.
        ("cast(code, Edm.Int32) gt 10", [1]),
        ("cast(code, Edm.Int64) eq 7", [2]),
        ("cast(code, Edm.Int16) eq 7", [2]),
        ("cast(code, Edm.Byte) eq 42", [1]),
        ("cast(code, Edm.SByte) eq 42", [1]),
        ("cast(code, Edm.Double) gt 10.5", [1]),
        ("cast(code, Edm.Single) gt 10", [1]),
        # Numbers -> other numbers and to text.
        ("cast(price, Edm.Int32) eq 120", [2]),
        ("cast(price, Edm.Decimal) gt 100", [2]),
        ("cast(price, Edm.String) eq '9.75'", [1]),
        ("cast(n, Edm.String) eq '2'", [2]),
        # Text -> temporal, and a timestamp narrowed to its day.
        ("cast(day_text, Edm.Date) eq 2025-06-07", [2]),
        ("cast(ts_text, Edm.DateTimeOffset) gt 2025-01-01T00:00:00Z", [2]),
        ("cast(published_at, Edm.Date) eq 2024-01-02", [1]),
        # Text -> boolean.
        ("cast(flag, Edm.Boolean) eq true", [1]),
        # Literal operands.
        ("cast('2', Edm.Int32) eq n", [2]),
        ("cast(7, Edm.String) eq code", [2]),
    ),
)
def test_cast_selects_the_right_rows(dataset, filter_expr, expected):
    assert _odata_rows(dataset, filter_expr) == expected


@pytest.mark.parametrize(
    "filter_expr",
    (
        "cast(code, Edm.Int32) eq null",
        "cast(day_text, Edm.Date) eq null",
        "cast(ts_text, Edm.DateTimeOffset) eq null",
        "cast(flag, Edm.Boolean) eq null",
    ),
)
def test_a_value_that_does_not_convert_is_null(dataset, filter_expr):
    """OData §5.1.1.10.1: "If the cast fails, the cast function returns null"."""
    assert _odata_rows(dataset, filter_expr) == [3]


def test_cast_in_compute_yields_the_target_type(dataset):
    morsels = _execute(_plan(dataset, compute_expr="cast(code, Edm.Int32) as code_num"))

    values = [value for morsel in morsels for value in morsel.column(morsel.column_names[-1]).to_pylist()]
    assert sorted(values, key=lambda v: (v is None, v)) == [7, 42, None]


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filter_expr", "expected"),
    (
        ("cast(code, Edm.TimeOfDay) eq 1", "hour()/minute()/second()"),
        ("cast(code, Edm.Duration) eq 1", "duration'P30D'"),
        ("cast(code, Edm.Binary) eq 1", "Edm.String"),
        ("cast(code, Edm.Guid) eq 1", "Edm.String"),
    ),
)
def test_unsupported_targets_say_what_to_write_instead(filter_expr, expected):
    with pytest.raises(ValueError) as excinfo:
        ODataExpressionParser().parse_filter(filter_expr)

    assert "not supported by this service" in str(excinfo.value)
    assert expected in str(excinfo.value)


def test_an_unknown_target_lists_the_supported_ones():
    with pytest.raises(ValueError) as excinfo:
        ODataExpressionParser().parse_filter("cast(code, Edm.Foo) eq 1")

    message = str(excinfo.value)
    assert "'Edm.Foo'" in message
    assert "Edm.Int32" in message
    assert "Edm.DateTimeOffset" in message


def test_the_one_argument_form_is_refused():
    """cast(Edm.X) casts the entity to a derived type; treating it as a no-op
    would silently match every row."""
    with pytest.raises(ValueError) as excinfo:
        ODataExpressionParser().parse_filter("cast(Edm.Int32) eq 1")

    assert "cast(price, Edm.Int32)" in str(excinfo.value)


def test_a_non_type_second_argument_is_refused():
    with pytest.raises(ValueError) as excinfo:
        ODataExpressionParser().parse_filter("cast(code, 5) eq 1")

    assert "must be a type name" in str(excinfo.value)
