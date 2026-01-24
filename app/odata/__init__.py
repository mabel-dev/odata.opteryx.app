"""OData v4 support for Opteryx.

This package provides OData v4 query parameter parsing and conversion
to Opteryx logical plans for optimized execution.
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

# Now safe to import modules that depend on odata_query
from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder
from app.odata.odata_to_logical_plan import build_logical_plan

__all__ = ["ODataLogicalPlanBuilder", "build_logical_plan"]
