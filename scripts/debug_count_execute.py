#!/usr/bin/env python3
import sys
from pathlib import Path
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root))
sys.path.insert(0, str(_repo_root / 'app'))

import traceback
import opteryx
# Prefer workspace-local opteryx-core if available
from pathlib import Path
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(1, str(_repo_root / 'opteryx-core'))
import importlib
importlib.reload(opteryx)

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder

plan = ODataLogicalPlanBuilder('$planets').build(count_only=True)
print('Plan nodes:', list(plan.nodes()))
try:
    res = opteryx.execute_logical_plan(plan)
    print('execute_logical_plan returned:', res)
except Exception as e:
    print('execute_logical_plan raised:')
    traceback.print_exc()
