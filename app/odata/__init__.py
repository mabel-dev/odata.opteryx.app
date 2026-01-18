"""OData v4 support for Opteryx.

This package provides OData v4 query parameter parsing and conversion
to Opteryx logical plans for optimized execution.
"""

from app.odata.odata_to_logical_plan import ODataLogicalPlanBuilder, build_logical_plan

__all__ = ["ODataLogicalPlanBuilder", "build_logical_plan"]
