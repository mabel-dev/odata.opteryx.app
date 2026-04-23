"""Proxy to the shared opteryx-core OData $apply builder."""

import sys
from pathlib import Path

_vendored_path = str(Path(__file__).parent.parent / "vendored" / "odata-query")
if _vendored_path not in sys.path:
    sys.path.insert(0, str(_vendored_path))

from opteryx.odata import ApplyLogicalPlanBuilder

__all__ = ["ApplyLogicalPlanBuilder"]
