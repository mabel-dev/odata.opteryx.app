"""Logging utilities for the OData service.

Self-contained replacement for the previous ``orso.logging`` dependency.
``orso`` is no longer part of the fleet, and opteryx-core's ``opteryx.logging``
is not depended on here so this service stays importable independently of the
core package's logging module.

The public surface mirrors what the codebase used from ``orso.logging``:

* ``set_log_name(name)`` — set the default logger name used by ``get_logger()``.
* ``get_logger(name=None)`` — return a ``logging.Logger``; when ``name`` is
  omitted the configured default name is used.
"""

from __future__ import annotations

import logging
from typing import Optional

__all__ = ["get_logger", "set_log_name"]

_DEFAULT_LOG_NAME = "opteryx"


def set_log_name(name: str) -> None:
    """Set the default logger name returned by :func:`get_logger`.

    Args:
        name: Name applied to loggers requested without an explicit name.
    """
    global _DEFAULT_LOG_NAME
    _DEFAULT_LOG_NAME = name


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a logger instance.

    Args:
        name: Optional logger name. When omitted, the name configured via
            :func:`set_log_name` (default ``"opteryx"``) is used.

    Returns:
        A standard library :class:`logging.Logger`.
    """
    return logging.getLogger(name or _DEFAULT_LOG_NAME)
