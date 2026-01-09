"""Minimal odata.auth package exposing authentication dependency stubs.

This package is intentionally small: we just provide a `require_bearer_token`
dependency to match the original code's expectations. Replace with the
real implementation if you reintroduce full auth verification logic.
"""

__all__ = []
