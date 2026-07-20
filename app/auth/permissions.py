"""Permission handling utilities.

This module provides utilities for converting and handling permissions
between JWT format and Opteryx format.
"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from app.logging import get_logger

logger = get_logger()

# The workstream.collection pairs reachable with zero credentials (see
# app.auth.deps.require_authentication, which lets anonymous requests through
# rather than rejecting them). Every endpoint that accepts anonymous callers
# must still scope them down to exactly these pairs itself. Only add a pair
# here for datasets that are intentionally public (e.g. open data backing
# public-facing dashboards) — this is the full anonymous-access surface.
ANONYMOUS_EXEMPT_WORKSTREAM = "public"
ANONYMOUS_EXEMPT_PAIRS = {
    ("public", "security"),
    ("public", "geopolitics"),
}


def is_anonymous_exempt(workstream: Optional[str], collection: Optional[str]) -> bool:
    """Whether (workstream, collection) is reachable without authentication."""
    return (workstream, collection) in ANONYMOUS_EXEMPT_PAIRS


def billing_account_from_claims(claims: Dict[str, Any]) -> Optional[str]:
    """Extract the caller's billing account from the JWT "scope" claim.

    The issuer puts the billing account in `scope` as a single value. It is
    deliberately NOT derived from `sub`/`identity`: many users bill to one
    account, so identity and billing account are different things.

    Multi-valued scopes are refused rather than guessed at. OAuth allows `scope`
    to carry several space-delimited values, and the two (dead) extractors in
    billing.opteryx disagree about whether the delimiter is a space or a comma.
    Picking one would risk attributing usage to the wrong tenant — i.e. billing
    the wrong customer. Returning None instead sends the usage to the house
    account, which is wrong but not someone else's bill.

    Returns:
        The billing account, or None if absent/empty/ambiguous.
    """
    scope = claims.get("scope")

    # Some issuers emit scope as a list rather than a delimited string.
    if isinstance(scope, (list, tuple)):
        values = [str(s).strip() for s in scope if str(s).strip()]
    elif isinstance(scope, str):
        values = [s for s in scope.replace(",", " ").split() if s]
    elif scope is None:
        return None
    else:
        logger.warning(f"Ignoring 'scope' claim of unexpected type {type(scope).__name__}")
        return None

    if not values:
        return None
    if len(values) > 1:
        logger.warning(
            f"Ignoring multi-valued 'scope' claim ({len(values)} values); cannot determine "
            "billing account unambiguously, usage will fall back to the default account"
        )
        return None

    return values[0]


def convert_policies_for_opteryx(policies: Optional[list]) -> List[Dict[str, str]]:
    """Convert JWT "policies" claim to Opteryx access_policies format.

    Accepts policies in various formats:
      - list of dicts: [{"id": ..., "role": "reader", "pattern": "benchmarks.*"}, ...]
      - list of lists/tuples: [[policy_id, role, pattern], ...]

    Returns:
        list: List of dicts in Opteryx format with "pattern" and "role" keys.
              Returns empty list if policies is None or empty.
    """
    if not policies:
        logger.debug("No policies to convert (empty or None)")
        return []

    access_policies = []

    try:
        for p in policies:
            # dict format
            if isinstance(p, dict):
                role = p.get("role")
                pattern = p.get("pattern") or p.get("workspace") or p.get("resource")
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role})
            # list/tuple format: [id, role, pattern, ...]
            elif isinstance(p, (list, tuple)) and len(p) >= 3:
                role = p[1]
                pattern = p[2]
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role})
            # legacy/two-element tuples (role, pattern)
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                role = p[0]
                pattern = p[1]
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role})
    except Exception as exc:
        logger.warning(f"Failed to convert policies to Opteryx format: {exc}. Policies: {policies}")
        return []

    logger.debug(f"Converted {len(access_policies)} policies to Opteryx format: {access_policies}")
    return access_policies


def get_access_policies_from_claims(claims: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract and convert JWT "policies" claim to Opteryx access policies.

    This function DOES NOT mutate the provided `claims` dict; it converts
    the token's "policies" claim on-the-fly and returns Opteryx-format
    access policies.

    Args:
        claims: JWT claims dict containing policies

    Returns:
        list: Access policies in Opteryx format

    Examples:
        >>> claims = {"policies": [["id", "reader", "public.*"]], "identity": "user@example.com"}
        >>> get_access_policies_from_claims(claims)
        [{"pattern": "public.*", "role": "reader"}]
    """
    policies = claims.get("policies", [])
    return convert_policies_for_opteryx(policies)
