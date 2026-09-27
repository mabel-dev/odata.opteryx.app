"""Type-mismatch errors restated for a `$filter` author.

Opteryx ends its type-mismatch messages with `CAST(column AS type)`. That is the
right advice in SQL's syntax; OData spells it `cast(column, Edm.Type)`. And a
caller who quoted a date (the commonest cause by far) should be told to drop the
quotes, not to cast -- a cast would work, but it would paper over the mistake.

The end-to-end cases execute a real query: the message only helps if it is what
the engine actually produces for that filter, not what the exception can be
constructed to say.
"""

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import opteryx
import pytest
from opteryx.connectors import DiskConnector
from rugo.parquet import write_parquet

from app.odata.odata_to_logical_plan import build_logical_plan
from app.routes.v4.errors import describe_type_mismatch

DATASET_DIR = "_tmp_odata_type_mismatch"

# See test_in_subnet_filter for why the workspace is registered here.
opteryx.register_workspace("testdata", DiskConnector)

_CAST_ADVICE = "CAST(column AS type)"


@pytest.fixture(scope="module")
def dataset():
    """One row, one column of each type a quoted literal gets compared against."""
    repo_root = Path(__file__).resolve().parent.parent
    previous_cwd = Path.cwd()
    os.chdir(repo_root)
    directory = repo_root / "testdata" / DATASET_DIR
    shutil.rmtree(directory, ignore_errors=True)
    directory.mkdir(parents=True)
    session = opteryx.session()
    morsel = next(
        iter(
            session.execute_to_morsels(
                "SELECT CAST(t AS TIMESTAMP) AS flow_start, CAST(d AS DATE) AS shipped_date, "
                "n AS price, s AS vendor "
                "FROM (VALUES ('2023-10-27T09:41:00', '2023-10-27', 100, 'Oracle')) "
                "AS v(t, d, n, s)"
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


def _message(dataset_name, filter_expr):
    """The message a caller would see for `filter_expr`, or None if it executed."""
    plan = build_logical_plan(
        table_name=dataset_name,
        filter_expr=filter_expr,
        select_cols=None,
        orderby_expr=None,
        top=None,
        skip=None,
    )
    session = opteryx.session()
    try:
        results, _ = session.execute_logical_plan(plan, connection=session)
        for morsel in results:
            morsel.materialize()
    except Exception as exc:  # noqa: BLE001 -- the message under test is the point
        return describe_type_mismatch(exc)
    return None


@pytest.mark.parametrize(
    ("column", "literal"),
    (
        ("flow_start", "2023-10-27T09:40:00Z"),
        ("flow_start", "2023-10-27"),
        ("shipped_date", "2023-10-27"),
    ),
)
def test_a_quoted_temporal_literal_is_told_to_drop_the_quotes(dataset, column, literal):
    message = _message(dataset, f"{column} ge '{literal}'")

    assert message is not None, "a quoted temporal literal should still be an error"
    assert "unquoted" in message
    assert _CAST_ADVICE not in message
    # The worked example uses the caller's own column, unqualified.
    assert f"`{column} ge 2024-01-01T00:00:00Z`" in message


def test_a_quoted_number_is_told_to_drop_the_quotes(dataset):
    message = _message(dataset, "price eq '100'")

    assert message is not None
    assert "`price gt 100`" in message
    assert _CAST_ADVICE not in message


def test_the_engine_message_is_kept(dataset):
    """Only the CAST sentence is replaced -- the column, both types and the
    offending literal are the useful half and stay."""
    message = _message(dataset, "flow_start ge '2023-10-27T09:40:00Z'")

    assert "flow_start" in message
    assert "TIMESTAMP" in message
    assert "VARCHAR" in message
    assert "2023-10-27T09:40:00Z" in message


def test_unquoted_literals_are_not_an_error(dataset):
    """The counterpart the advice points at: what it tells you to write works."""
    assert _message(dataset, "flow_start ge 2023-10-27T09:40:00Z") is None
    assert _message(dataset, "price eq 100") is None
    assert _message(dataset, "vendor eq 'Oracle'") is None


class _Mismatch(Exception):
    """An engine type mismatch, with the attributes opteryx's exceptions carry."""

    def __init__(self, left_type, right_type, left_column=None, right_column=None):
        super().__init__()
        self.left_type = left_type
        self.right_type = right_type
        self.left_column = left_column
        self.right_column = right_column

    def __str__(self):
        return "these do not match. Cast one side to match the other with `CAST(column AS type)`."


def test_a_mismatch_with_no_string_side_is_told_the_odata_cast():
    """Not a quoting problem, so the SQL cast advice is restated in OData's
    syntax, worked in the caller's own column and the other side's type."""
    message = describe_type_mismatch(_Mismatch("DATE", "TIMESTAMP[us]", left_column="orders.shipped_date"))

    assert _CAST_ADVICE not in message
    assert "unquoted" not in message
    assert "`cast(shipped_date, Edm.DateTimeOffset)`" in message


def test_the_worked_example_uses_whichever_side_names_a_column():
    message = describe_type_mismatch(_Mismatch("INT64", "BOOLEAN", right_column="orders.is_paid"))

    assert "`cast(is_paid, Edm.Int64)`" in message


def test_temporal_vs_numeric_gets_only_the_general_form():
    """A cast between them converts through the epoch, which is almost never
    what the comparison meant -- a worked example would read as a recommendation."""
    message = describe_type_mismatch(
        _Mismatch("TIMESTAMP[us]", "INT64", left_column="orders.shipped_date")
    )

    assert _CAST_ADVICE not in message
    assert "unquoted" not in message
    assert "`cast(column, Edm.Type)`" in message
    assert "cast(shipped_date" not in message


def test_no_column_gets_only_the_general_form():
    message = describe_type_mismatch(_Mismatch("INT64", "BOOLEAN"))

    assert "`cast(column, Edm.Type)`" in message


def test_the_suggested_cast_parses():
    """The advice is only worth giving if the caller can paste it back in."""
    from app.odata.expression_parser import ODataExpressionParser

    ODataExpressionParser().parse_filter("cast(shipped_date, Edm.DateTimeOffset) ge 2024-01-01T00:00:00Z")


def test_an_error_that_never_offered_cast_advice_is_left_alone():
    """Not every type error is a literal-quoting problem; the ones that are not
    are passed through verbatim rather than having advice bolted on."""

    class _Other(Exception):
        def __str__(self):
            return "Unknown temporal unit form: _TIMESTAMP_XS."

    assert describe_type_mismatch(_Other()) == "Unknown temporal unit form: _TIMESTAMP_XS."
