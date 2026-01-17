"""Permission handling utilities.

This module provides utilities for converting and handling permissions
between JWT format and Opteryx format.
"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from orso.logging import get_logger

logger = get_logger()


def convert_permissions_for_opteryx(
    permissions: Optional[List[Tuple[str, str]]],
) -> List[Dict[str, str]]:
    """Convert JWT permissions to Opteryx access_policies format.

    Converts from JWT format: [(role, workspace), ...]
    To Opteryx format: [{"pattern": workspace, "role": role}, ...]

    Args:
        permissions: List of (role, workspace) tuples from JWT claims.
                    Can be None or empty.

    Returns:
        list: List of dicts in Opteryx format with "pattern" and "role" keys.
              Returns empty list if permissions is None or empty.

    Examples:
        >>> convert_permissions_for_opteryx([("reader", "public.*")])
        [{"pattern": "public.*", "role": "reader"}]

        >>> convert_permissions_for_opteryx([])
        []

        >>> convert_permissions_for_opteryx(None)
        []
    """
    if not permissions:
        logger.debug("No permissions to convert (empty or None)")
        return []

    try:
        access_policies = [{"pattern": workspace, "role": role} for role, workspace in permissions]

        logger.debug(
            "Converted %d permissions to Opteryx format: %s",
            len(access_policies),
            access_policies,
        )

        return access_policies

    except (TypeError, ValueError) as exc:
        # Handle malformed permissions gracefully
        logger.warning(
            "Failed to convert permissions to Opteryx format: %s. Permissions: %s",
            exc,
            permissions,
        )
        return []


def get_access_policies_from_claims(claims: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract and convert permissions from JWT claims to Opteryx access policies.

    This is a convenience function that extracts the permissions claim
    and converts it to Opteryx format in one call.

    Args:
        claims: JWT claims dict containing permissions

    Returns:
        list: Access policies in Opteryx format

    Examples:
        >>> claims = {"permissions": [("reader", "public.*")], "identity": "user@example.com"}
        >>> get_access_policies_from_claims(claims)
        [{"pattern": "public.*", "role": "reader"}]
    """
    permissions = claims.get("permissions", [])
    return convert_permissions_for_opteryx(permissions)
