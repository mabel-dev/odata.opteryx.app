#!/usr/bin/env python3
"""Compare Opteryx SQL COUNT(*) plan with OData count/top=0 plans.

This script:
  - Builds an Opteryx logical plan for "SELECT COUNT(*) FROM $planets"
  - Builds an OData count-only logical plan (builder.build(count_only=True))
  - Builds an OData plan for top=0 and shows optimizer strategy (schema_count_split)
  - Prints structural differences (node sequence, node types, and aggregate
    attribute shapes) so we can see why OData's COUNT plan may differ.

Run from repository root: python3 scripts/compare_count_plans.py
"""

import pprint
import sys
from pathlib import Path

from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.plan_optimizer import ODataLogicalPlanOptimizer

# Make imports work regardless of where this script is run from
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "app"))
sys.path.insert(1, str(_repo_root / "opteryx-core"))
sys.path.insert(1, str(_repo_root / "pyiceberg-firestore-gcs"))


def print_plan(plan, title):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

    nodes = list(plan.nodes(True))
    print(f"total_nodes: {len(nodes)}")
    for node_id, node in nodes:
        print(f"\n[{node_id[:8]}...] node_type: {getattr(node, 'node_type', None)}")
        # Show common attrs
        for attr in [
            "columns",
            "order_by",
            "limit",
            "offset",
            "relation",
            "aggregates",
            "aggregations",
            "group_by",
            "order_by_columns",
            "columns",
        ]:
            if hasattr(node, attr):
                val = getattr(node, attr)
                if val is not None:
                    print(f"  {attr}: {pprint.pformat(val)}")


def build_sql_count_plan(table_name: str):
    sql = f"SELECT COUNT(*) FROM {table_name}"
    parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
    logical_plan, ast, ctes = do_logical_planning_phase(parsed[0])
    return logical_plan


def build_odata_count_plan(table_name: str):
    builder = ODataLogicalPlanBuilder(table_name)
    plan = builder.build(count_only=True)
    return plan


def build_odata_schema_count_split(table_name: str):
    # Build the initial logical plan for top=0
    builder = ODataLogicalPlanBuilder(table_name)
    base_plan = builder.build(top=0)

    optimizer = ODataLogicalPlanOptimizer()
    optimized_plan, strategy_info = optimizer.optimize_with_constraints(
        base_plan, top=0, count_requested=True
    )

    # If optimizer suggests schema_count_split, show schema and count plans
    details = {
        "strategy": strategy_info.get("strategy"),
        "description": strategy_info.get("description"),
    }

    schema_builder = ODataLogicalPlanBuilder(table_name)
    schema_plan = schema_builder.build(top=1)

    count_builder = ODataLogicalPlanBuilder(table_name)
    count_plan = count_builder.build(count_only=True)

    return base_plan, details, schema_plan, count_plan


def compare_aggregate_shapes(sql_plan, odata_plan):
    """Compare aggregate node shapes between SQL and OData COUNT plans."""

    def find_agg_node(plan):
        for _, node in plan.nodes(True):
            if getattr(node, "node_type", None) is not None and str(
                getattr(node, "node_type")
            ).endswith("AggregateAndGroup"):
                return node
        return None

    sql_agg = find_agg_node(sql_plan)
    odata_agg = find_agg_node(odata_plan)

    print("\nAGGREGATE NODE COMPARISON:\n")

    if sql_agg is None:
        # Try to find SQL Aggregate node by searching for 'Aggregate' substring
        for _, node in sql_plan.nodes(True):
            if getattr(node, "node_type", None) and "Aggregate" in str(node.node_type):
                sql_agg = node
                break

    if sql_agg is None:
        print("  SQL plan: no Aggregate node found")
    else:
        print("  SQL Aggregate node attributes:")
        for attr in ["aggregations", "aggregates", "group_by"]:
            if hasattr(sql_agg, attr):
                val = getattr(sql_agg, attr)
                print(f"    {attr}: {pprint.pformat(val)}")
                if isinstance(val, list):
                    for i, item in enumerate(val):
                        print(f"      Item[{i}] type: {type(item).__name__}")
                        # Print salient fields if available
                        for field in [
                            "node_type",
                            "value",
                            "parameters",
                            "function",
                            "alias",
                            "expression",
                            "query_column",
                        ]:
                            if hasattr(item, field):
                                print(f"        {field}: {getattr(item, field)}")
                        # Print dir for diagnostics
                        print(f"        dir: {[x for x in dir(item) if not x.startswith('_')]}")
                        try:
                            print(f"        __dict__: {getattr(item, '__dict__', None)}")
                        except Exception as e:
                            print(f"        __dict__ access error: {e}")

    if odata_agg is None:
        print("  OData plan: no AggregateAndGroup node found")
    else:
        print("  OData Aggregate node attributes:")
        for attr in ["aggregations", "aggregates", "group_by"]:
            if hasattr(odata_agg, attr):
                print(f"    {attr}: {pprint.pformat(getattr(odata_agg, attr))}")

    # Basic shape check
    def agg_shape_summary(node):
        if node is None:
            return None
        s = {}
        if hasattr(node, "aggregates"):
            s["aggregates_type"] = type(node.aggregates).__name__
            if isinstance(node.aggregates, list) and len(node.aggregates) > 0:
                s["first_aggregate_item_type"] = type(node.aggregates[0]).__name__
        if hasattr(node, "aggregations"):
            s["aggregations_type"] = type(node.aggregations).__name__
            if isinstance(node.aggregations, list) and len(node.aggregations) > 0:
                s["first_aggregation_item_type"] = type(node.aggregations[0]).__name__
        return s

    print("\nAggregate shape summary:")
    print("  SQL:", agg_shape_summary(sql_agg))
    print("  OData:", agg_shape_summary(odata_agg))


def inspect_aggregate_node(node):
    """Return a serializable description of aggregate node internals."""
    if node is None:
        return None
    desc = {}
    for attr in ["aggregations", "aggregates", "group_by"]:
        if hasattr(node, attr):
            val = getattr(node, attr)
            if val is None:
                desc[attr] = None
                continue
            if isinstance(val, list):
                items = []
                for item in val:
                    if isinstance(item, dict):
                        items.append({"type": "dict", "keys": list(item.keys())})
                    else:
                        # Try to capture salient attributes
                        item_info = {"type": type(item).__name__}
                        if hasattr(item, "node_type"):
                            item_info["node_type"] = str(getattr(item, "node_type"))
                        if hasattr(item, "alias"):
                            item_info["alias"] = getattr(item, "alias")
                        if hasattr(item, "function"):
                            item_info["function"] = getattr(item, "function")
                        items.append(item_info)
                desc[attr] = items
            else:
                desc[attr] = {"type": type(val).__name__}
    return desc


def compare_count_plans_for_table(table_name: str):
    print("\n" + "#" * 80)
    print(f"Inspecting COUNT plans for table: {table_name}")

    sql_plan = build_sql_count_plan(table_name)
    odata_count_plan = build_odata_count_plan(table_name)
    base_plan, details, schema_plan, count_plan = build_odata_schema_count_split(table_name)

    print_plan(sql_plan, "Opteryx SQL COUNT(*) plan")
    print_plan(odata_count_plan, "OData count-only (builder.build(count_only=True)) plan")
    print("\nOptimizer decision:", pprint.pformat(details))
    print_plan(base_plan, "OData base plan (top=0)")
    print_plan(schema_plan, "OData schema plan (top=1)")
    print_plan(count_plan, "OData count plan (count_only=True)")

    # Inspect aggregates
    sql_agg = None
    for _, n in sql_plan.nodes(True):
        if getattr(n, "node_type", None) is not None and str(getattr(n, "node_type")).endswith(
            "AggregateAndGroup"
        ):
            sql_agg = n
            break
    odata_agg = None
    for _, n in odata_count_plan.nodes(True):
        if getattr(n, "node_type", None) is not None and str(getattr(n, "node_type")).endswith(
            "AggregateAndGroup"
        ):
            odata_agg = n
            break
    count_agg = None
    for _, n in count_plan.nodes(True):
        if getattr(n, "node_type", None) is not None and str(getattr(n, "node_type")).endswith(
            "AggregateAndGroup"
        ):
            count_agg = n
            break

    sql_desc = inspect_aggregate_node(sql_agg)
    odata_desc = inspect_aggregate_node(odata_agg)
    count_desc = inspect_aggregate_node(count_agg)

    print("\nAggregate descriptions:")
    print("  SQL:", pprint.pformat(sql_desc))
    print("  OData (count_only):", pprint.pformat(odata_desc))
    print("  OData (schema_count_split count plan):", pprint.pformat(count_desc))

    # Heuristic mismatch detection
    issues = []

    # If SQL aggregates are list of non-dict items but OData aggregates are dicts -> mismatch
    def agg_is_dicts(desc):
        if not desc or "aggregates" not in desc:
            return False
        val = desc["aggregates"]
        if not isinstance(val, list) or len(val) == 0:
            return False
        return all(isinstance(x, dict) and x.get("type") == "dict" for x in val)

    if sql_desc and odata_desc:
        if not agg_is_dicts(sql_desc) and agg_is_dicts(odata_desc):
            issues.append(
                "OData count-only has aggregate entries as dicts while SQL plan has non-dict aggregate items. This may cause binder to expect Node-like objects and fail."
            )

    if sql_desc and count_desc:
        if not agg_is_dicts(sql_desc) and agg_is_dicts(count_desc):
            issues.append(
                "OData schema_count_split count plan has aggregate entries as dicts while SQL plan has non-dict aggregate items."
            )

    if issues:
        print("\n✗ Issues detected:")
        for i in issues:
            print(" - ", i)
        return False

    print("\n✓ No immediate aggregate-shape mismatches detected for this table.")
    return True


if __name__ == "__main__":
    tables = ["$planets"]
    all_ok = True
    for t in tables:
        ok = compare_count_plans_for_table(t)
        all_ok = all_ok and ok

    print("\nSummary: All tables OK:", all_ok)
    if not all_ok:
        raise SystemExit(2)
    print("\nDone.")
