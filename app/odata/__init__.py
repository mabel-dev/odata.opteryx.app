"""OData v4 support for Opteryx.

This package provides OData v4 query parameter parsing and conversion to
Opteryx logical plans for optimized execution. The implementation lives in
this repository (it was previously delegated to an ``opteryx.odata`` module
that no longer ships with opteryx-core).
"""

# CRITICAL: Setup vendored odata_query BEFORE any imports that use it
# ruff: noqa: E402
# isort: off
import sys
from pathlib import Path

_vendored_path = str(Path(__file__).parent.parent / "vendored" / "odata-query")
if _vendored_path not in sys.path:
    sys.path.insert(0, _vendored_path)
# isort: on

from app.odata.apply_builder import ApplyLogicalPlanBuilder
from app.odata.expression_parser import ODataExpressionParser
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.odata_to_logical_plan import build_logical_plan
from app.odata.plan_optimizer import ODataLogicalPlanOptimizer

__all__ = [
    "ODataExpressionParser",
    "ODataLogicalPlanBuilder",
    "ODataLogicalPlanOptimizer",
    "ApplyLogicalPlanBuilder",
    "build_logical_plan",
]
