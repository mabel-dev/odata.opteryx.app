"""OData v4 support for Opteryx.

This package provides OData v4 query parameter parsing and conversion
for Opteryx, forwarding to the shared opteryx-core implementation.
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

from opteryx.odata import (
    ODataExpressionParser,
    ODataLogicalPlanBuilder,
    ODataLogicalPlanOptimizer,
    ApplyLogicalPlanBuilder,
    build_logical_plan,
)

__all__ = [
    "ODataExpressionParser",
    "ODataLogicalPlanBuilder",
    "ODataLogicalPlanOptimizer",
    "ApplyLogicalPlanBuilder",
    "build_logical_plan",
]
