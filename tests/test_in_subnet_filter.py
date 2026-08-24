"""`in_subnet()` -- IPv4 CIDR containment in `$filter`.

OData has no containment operator, so callers reaching for PostgreSQL's `<<=`
get a parse error. `in_subnet(ip_column, 'cidr')` is the supported spelling: it
lowers to Opteryx's `IPContainedBy` comparison (the canonical name for `<<=`),
which masks-and-compares the underlying 32-bit address rather than matching text.

Two levels are covered here: what the parser builds (cheap, always runs) and
what the engine returns (a real dataset, one IPV4 column). The parse tests alone
would not have caught the lowering that bound but could not execute.
"""

import ipaddress
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

DATASET_DIR = "_tmp_odata_in_subnet"

# `testdata.*` resolves through the local filesystem rather than the default
# (GCS) connector. Registered here rather than inherited from whichever other
# module happened to import first, so this file passes run on its own.
opteryx.register_workspace("testdata", DiskConnector)

# `n` identifies the row so assertions compare row identity, not a count that
# could match by coincidence. Addresses straddle the /22 and /24 boundaries that
# a prefix-matching implementation would get wrong.
ROWS = [
    (1, "192.168.4.7"),
    (2, "192.168.4.255"),
    (3, "192.168.5.0"),
    (4, "192.168.7.255"),
    (5, "192.168.8.0"),
    (6, "10.0.0.1"),
    (7, "255.255.255.255"),
]


@pytest.fixture(scope="module")
def dataset():
    """One IPV4 column, addressed the way the DiskConnector addresses a dataset.

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
    tuples = ", ".join(f"('{address}', {n})" for n, address in ROWS)
    morsel = next(
        iter(
            session.execute_to_morsels(
                f"SELECT CAST(a AS IPV4) AS src_addr, n FROM (VALUES {tuples}) AS v(a, n)"
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


def _expected(cidr):
    """Ground truth from ROWS via the stdlib, not from the engine."""
    network = ipaddress.ip_network(cidr)
    return sorted(n for n, address in ROWS if ipaddress.ip_address(address) in network)


# ---------------------------------------------------------------------------
# Parsing and lowering
# ---------------------------------------------------------------------------


def test_in_subnet_lowers_to_the_ip_containment_comparison():
    node = ODataExpressionParser().parse_filter("in_subnet(src_addr, '192.168.4.0/24')")

    from opteryx.expression import NodeType

    assert node.node_type == NodeType.COMPARISON_OPERATOR
    assert node.value == "IPContainedBy"
    assert node.left.source_column == "src_addr"
    assert node.right.value == "192.168.4.0/24"


def test_in_subnet_composes_with_the_rest_of_the_grammar():
    """The lowered node is a comparison, so it nests under and/or/not like any
    other predicate -- the property that broke when it lowered to a bare binary
    operator instead."""
    node = ODataExpressionParser().parse_filter(
        "not in_subnet(src_addr, '10.0.0.0/8') and n gt 2"
    )

    from opteryx.expression import NodeType

    assert node.node_type == NodeType.AND
    assert node.left.node_type == NodeType.NOT
    assert node.left.centre.value == "IPContainedBy"


def test_in_subnet_rejects_a_non_string_cidr():
    with pytest.raises(ValueError, match="in_subnet"):
        ODataExpressionParser().parse_filter("in_subnet(src_addr, 24)")


def test_in_subnet_rejects_the_wrong_argument_count():
    with pytest.raises(ValueError, match="2 arguments"):
        ODataExpressionParser().parse_filter("in_subnet(src_addr)")


# ---------------------------------------------------------------------------
# The `<<=` hint -- what sent callers guessing before in_subnet existed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operator", ("<<=", ">>=", "<<", ">>"))
def test_ip_containment_operators_name_the_supported_spelling(operator):
    """The parser fails on the first predicate and never reaches the rest of the
    expression, so the generic 'failed to parse' message was actively misleading
    about where the problem was."""
    with pytest.raises(ValueError) as caught:
        ODataExpressionParser().parse_filter(
            f"src_addr {operator} '192.168.4.0/24' and n gt 2"
        )

    message = str(caught.value)
    assert operator in message
    assert "in_subnet" in message


def test_unknown_function_message_survives_to_the_caller():
    """FunctionCallException carries a precise message; it used to be flattened
    into the generic parse failure."""
    with pytest.raises(ValueError, match="no_such_function"):
        ODataExpressionParser().parse_filter("no_such_function(src_addr, 'x')")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cidr",
    (
        "192.168.4.0/24",  # byte boundary -- the case startswith() also handles
        "192.168.4.0/22",  # not a byte boundary -- the case it does not
        "192.168.0.0/16",
        "10.0.0.0/8",
        "192.168.4.7/32",  # single host
        "0.0.0.0/0",  # everything
        "172.16.0.0/12",  # nothing
    ),
)
def test_in_subnet_returns_the_addresses_inside_the_network(dataset, cidr):
    assert _odata_rows(dataset, f"in_subnet(src_addr, '{cidr}')") == _expected(cidr)


def test_in_subnet_negates(dataset):
    inside = set(_expected("192.168.4.0/22"))
    expected = sorted(n for n, _ in ROWS if n not in inside)

    assert _odata_rows(dataset, "not in_subnet(src_addr, '192.168.4.0/22')") == expected


def test_in_subnet_combines_with_other_predicates(dataset):
    expected = sorted(set(_expected("192.168.4.0/24")) | {6})

    assert (
        _odata_rows(dataset, "in_subnet(src_addr, '192.168.4.0/24') or n eq 6") == expected
    )
