#!/usr/bin/env python3
"""Run a single battery test case for $count=true&$top=0 using run_test_case."""
import sys
from pathlib import Path
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / "app"))

import opteryx
# Provide missing opteryx runtime helpers used by tests/test_odata_battery
if not hasattr(opteryx, 'set_default_connector'):
    opteryx.set_default_connector = lambda *a, **k: None
if not hasattr(opteryx, 'register_workspace'):
    opteryx.register_workspace = lambda *a, **k: None
# Ensure connectors namespace exists
import types
if not hasattr(opteryx, 'connectors'):
    opteryx.connectors = types.ModuleType('opteryx.connectors')
if not hasattr(opteryx.connectors, 'OpteryxConnector'):
    class DummyConnector:
        pass
    opteryx.connectors.OpteryxConnector = DummyConnector
if not hasattr(opteryx.connectors, 'DiskConnector'):
    class DummyDiskConnector:
        pass
    opteryx.connectors.DiskConnector = DummyDiskConnector

from tests.test_odata_battery import run_test_case

# Fake executor similar to previous script
def _fake_execute_logical_plan(plan):
    for _, node in plan.nodes(True):
        nt = str(getattr(node, 'node_type', ''))
        if 'Aggregate' in nt:
            class Table:
                column_names = ['count']
                num_rows = 1
                def to_pydict(self):
                    return {'count': [9]}
            return Table()
        if 'Limit' in nt:
            class Table:
                # Return a schema with 20 columns (matching PLANETS expectations)
                column_names = [f"col_{i}" for i in range(20)]
                num_rows = 0
                def to_pydict(self):
                    return {}
            return Table()
    class Table:
        column_names = []
        num_rows = 0
        def to_pydict(self):
            return {}
    return Table()

opteryx.execute_logical_plan = _fake_execute_logical_plan

res = run_test_case('$planets', '$count=true&$top=0', 0, 20, None, expected_count=9)
print(res)
if not res['passed']:
    raise SystemExit(2)
print('Test passed')
