#!/usr/bin/env python3
"""Direct test of read_dataset function."""

import os
import sys
from unittest.mock import Mock

from pathlib import Path

_tests_dir = Path(__file__).resolve().parent
_project_root = _tests_dir.parent
# The external dev packages live at the workspace root (one level up from repo root)
_workspace_root = _project_root.parent
# Make imports robust to file moves: ensure repo root and app/opteryx-core live on sys.path
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "app"))
sys.path.insert(1, str(_workspace_root / "pyiceberg-firestore-gcs"))
sys.path.insert(1, str(_workspace_root / "opteryx-core"))

import opteryx
from opteryx.connectors import OpteryxConnector
from opteryx.planner.logical_planner import do_logical_planning_phase
from opteryx.third_party import sqloxide
from opteryx_catalog import OpteryxCatalog

from app.routes.v4.interface import read_dataset

os.environ["OPTERYX_DEBUG"] = "1"


FIRESTORE_DATABASE = os.environ.get("FIRESTORE_DATABASE")
BUCKET_NAME = os.environ.get("GCS_BUCKET")
GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID")

workspace = OpteryxCatalog(
    "public",
    firestore_project=GCP_PROJECT_ID,
    firestore_database=FIRESTORE_DATABASE,
    gcs_bucket=BUCKET_NAME,
)

# Set default connector only if OpteryxConnector is available in this environment
if 'OpteryxConnector' in globals() and OpteryxConnector is not None:
    opteryx.set_default_connector(
        OpteryxConnector,
        catalog=OpteryxCatalog,
        firestore_project=GCP_PROJECT_ID,
        firestore_database=FIRESTORE_DATABASE,
        gcs_bucket=BUCKET_NAME,
    )
else:
    print("Note: OpteryxConnector is not available in this environment; skipping connector setup")

print(opteryx.query("SELECT * FROM public.github.events WHERE actor_login = 'Copilot' LIMIT 10"))


def test_read_dataset_direct():
    """Test calling read_dataset directly with mock objects."""

    # Create mock request and response
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = Mock()
    response.headers = {}

    # Call with a simple query
    try:
        result = read_dataset(
            request=request,
            response=response,
            workstream="public",
            collection="examples",
            dataset="planets",
            filter=None,
            top=10,
            skip=None,
            orderby=None,
            count=None,
            select=None,
            search=None,
        )
        print(f"Success! Result type: {type(result)}")
        print(f"Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
        print(result)
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


def test_read_dataset_with_filter():
    """Test calling read_dataset with a filter."""

    # Create mock request and response
    request = Mock()
    request.state = Mock()
    request.state.claims = {"identity": "test-user", "jwt_sub": "test-user"}

    response = Mock()
    response.headers = {}

    # First, let's inspect the logical plan structure
    print("\n" + "=" * 80)
    print("INSPECTING LOGICAL PLAN STRUCTURE")
    print("=" * 80)
    from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

    builder = ODataLogicalPlanBuilder("public.github.events")
    plan = builder.build(filter_expr="actor_login eq 'Copilot'", top=10)

    print(f"Plan type: {type(plan)}")
    print(f"Plan dir: {[x for x in dir(plan) if not x.startswith('_')]}")
    print(f"Plan.graph: {type(plan.graph) if hasattr(plan, 'graph') else 'N/A'}")
    if hasattr(plan, "graph"):
        print(f"Graph nodes: {plan.graph.nodes()}")
        print(f"Graph edges: {plan.graph.edges()}")
        for node_id in plan.graph.nodes():
            node_obj = plan.graph.nodes[node_id]
            print(f"\nNode {node_id}:")
            print(f"  Attributes: {node_obj}")

    # Call with a filter
    try:
        result = read_dataset(
            request=request,
            response=response,
            workstream="public",
            collection="github",
            dataset="events",
            filter="actor_login eq 'Copilot'",
            top=10,
            skip=None,
            orderby=None,
            count=None,
            select=None,
            search=None,
        )
        print(f"\nSuccess! Result type: {type(result)}")
        print(f"Result keys: {result.keys() if isinstance(result, dict) else 'N/A'}")
        print(f"Rows returned: {len(result.get('value', []))}")
        if result.get("value"):
            print(f"First row: {result['value'][0]}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()
        return False


def compare_logical_plans():
    """Compare logical plans from SQL parser vs manual OData construction."""
    print("=" * 80)
    print("LOGICAL PLAN COMPARISON")
    print("=" * 80)

    from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

    # Create logical plan via SQL parser
    print("\n[1] SQL Parser -> Logical Plan")
    sql = "SELECT * FROM public.github.events WHERE actor_login = 'Copilot' LIMIT 10"
    parsed = sqloxide.parse_sql(sql, _dialect="opteryx")
    sql_plan, _, _ = do_logical_planning_phase(parsed[0])
    print(f"SQL Plan type: {type(sql_plan)}")
    print(f"SQL Plan attributes: {[x for x in dir(sql_plan) if not x.startswith('_')]}")

    # Create logical plan via OData builder
    print("\n[2] OData Builder -> Logical Plan")
    odata_builder = ODataLogicalPlanBuilder("public.github.events")
    odata_plan = odata_builder.build(filter_expr="actor_login eq 'Copilot'", top=10)
    print(f"OData Plan type: {type(odata_plan)}")
    print(f"OData Plan attributes: {[x for x in dir(odata_plan) if not x.startswith('_')]}")

    # Compare graph structures (plan IS the graph)
    print("\n[3] Graph Structure Comparison")
    sql_nodes = list(sql_plan.nodes())
    odata_nodes = list(odata_plan.nodes())
    print(f"SQL Plan nodes: {sql_nodes}")
    print(f"SQL Plan edges: {list(sql_plan.edges())}")
    print(f"OData Plan nodes: {odata_nodes}")
    print(f"OData Plan edges: {list(odata_plan.edges())}")

    # Access internal node storage via _nodes dict
    print("\n[4] SQL Plan Node Details:")
    for node_id in sql_nodes:
        if node_id in sql_plan._nodes:
            node_data = sql_plan._nodes[node_id]
            print(f"\n    Node {node_id}:")
            print(f"      Type: {type(node_data)}")
            print(f"      Dir: {[x for x in dir(node_data) if not x.startswith('_')]}")
            # Try common attributes
            if hasattr(node_data, "node_type"):
                print(f"      node_type: {node_data.node_type}")
            if hasattr(node_data, "columns"):
                print(f"      columns: {node_data.columns}")
            if hasattr(node_data, "condition"):
                print(f"      condition: {node_data.condition}")
            if hasattr(node_data, "dataset"):
                print(f"      dataset: {node_data.dataset}")
            if hasattr(node_data, "limit"):
                print(f"      limit: {node_data.limit}")

    print("\n[5] OData Plan Node Details:")
    for node_id in odata_nodes:
        if node_id in odata_plan._nodes:
            node_data = odata_plan._nodes[node_id]
            print(f"\n    Node {node_id}:")
            print(f"      Type: {type(node_data)}")
            print(f"      Dir: {[x for x in dir(node_data) if not x.startswith('_')]}")
            # Try common attributes
            if hasattr(node_data, "node_type"):
                print(f"      node_type: {node_data.node_type}")
            if hasattr(node_data, "columns"):
                print(f"      columns: {node_data.columns}")
            if hasattr(node_data, "condition"):
                print(f"      condition: {node_data.condition}")
            if hasattr(node_data, "dataset"):
                print(f"      dataset: {node_data.dataset}")
            if hasattr(node_data, "limit"):
                print(f"      limit: {node_data.limit}")

    # Test execution
    print("\n[6] Execution Test")
    sql_result = opteryx.execute_logical_plan(sql_plan)
    odata_result = opteryx.execute_logical_plan(odata_plan)
    print(f"SQL result: {sql_result.num_rows} rows")
    print(f"OData result: {odata_result.num_rows} rows")


if __name__ == "__main__":
    compare_logical_plans()
