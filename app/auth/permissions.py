"""Permission handling utilities.

This module provides utilities for converting and handling permissions
between JWT format and Opteryx format.
"""

import fnmatch
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from opteryx_shared_services.logging import get_logger

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


# Entitlement that lifts row-level filtering entirely. Holders see every account's rows.
DATA_ADMIN_ENTITLEMENT = "data_admin"

# Some consumers namespace entitlements (authorize.opteryx uses
# "urn:opteryx:entitlement:user_admin"); the issuer currently emits them bare. Accept
# either so this does not silently stop matching if the issuer starts namespacing.
_ENTITLEMENT_URN_PREFIX = "urn:opteryx:entitlement:"


def entitlements_from_claims(claims: Dict[str, Any]) -> set:
    """Extract the caller's entitlements as a set of bare names.

    The identity store holds these as a list (`entitlements: ["user_admin",
    "data_admin"]`) but the issuer flattens them into a single comma-delimited
    `entitlement` claim. Both shapes are accepted, as is the plural key.

    Splitting on the delimiter is safe here: entitlements are multi-valued by
    definition, so a delimiter is unambiguous. The billing account is not — it has its
    own single-valued claim and is never parsed out of a delimited string.
    """
    raw = claims.get("entitlement") or claims.get("entitlements") or ()

    if isinstance(raw, str):
        values = [v for v in raw.replace(",", " ").split() if v]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(v).strip() for v in raw if str(v).strip()]
    else:
        logger.warning(f"Ignoring entitlement claim of unexpected type {type(raw).__name__}")
        return set()

    return {
        v[len(_ENTITLEMENT_URN_PREFIX) :] if v.startswith(_ENTITLEMENT_URN_PREFIX) else v
        for v in values
    }


def billing_account_from_claims(claims: Dict[str, Any]) -> Optional[str]:
    """Extract the caller's billing account from the JWT `billing_account` claim.

    This used to read `scope`. It must not: `scope` is an OAuth permission list,
    written by register.opteryx as comma-separated permission words, and reading it
    as a tenant identity billed every self-service user to whatever value happened to
    be sitting there — in practice the literal "opteryx", which is simultaneously the
    house account and the name of the system workspace, so billing rows read as though
    the workspace were the payer. The issuer now emits the account as its own claim.

    A single value by definition: a caller bills to one account or to none. There is
    nothing to split and nothing to disambiguate, which is the point of the separate
    claim — the old multi-valued `scope` was silently unbillable.

    Falls back to `sub` when the claim is absent: a caller with no explicit account
    bills to themselves rather than to the house account. Note this puts identities
    and tenant account names in one namespace — a `sub` equal to a tenant's account
    name would inherit that tenant's billing rows through the Eq visibility filter.

    Returns:
        The billing account, or None if the token carries neither claim.
    """
    billing_account = claims.get("billing_account")
    if billing_account is None:
        billing_account = claims.get("sub")

    if billing_account is None:
        return None
    if not isinstance(billing_account, str):
        logger.warning(
            f"Ignoring billing account of unexpected type {type(billing_account).__name__}"
        )
        return None

    billing_account = billing_account.strip()
    return billing_account or None


# Every relation under the tenant-facing billing namespace. A PATTERN, not an
# enumeration: `apply_visibility_filters` in opteryx-core fnmatches keys that hold a
# glob metacharacter, so this covers relations that do not exist yet.
#
# That is the point. The enumerated list this replaces was fail-open by construction —
# a relation nobody remembered to add was served UNFILTERED, silently — and it had
# already drifted between these three services once, leaving the Flight door serving
# every account's rollup rows. A namespace cannot drift.
#
# The admission rule this relies on: EVERY relation in `platform.billing.*` carries a
# `billing_account` column. One that cannot raises ColumnNotFoundError rather than
# serving rows, so a new dataset that forgets the column breaks loudly on first query
# instead of leaking every tenant quietly.
BILLING_NAMESPACE = "platform.billing.*"

# Relations any AUTHENTICATED caller may read, regardless of the policies in their
# token. Deliberately NARROWER than the filter above: the filter is fail-safe and
# covers the whole namespace, the grant is what a tenant actually needs.
#
# `charges_daily` only. The namespace also holds the raw event stream
# (`platform.billing.events`, which carries submitted SQL and the submitting user),
# the account-state and attribution snapshots, and the intermediate usage rollups.
# None of those are a tenant's business; all of them stay behind an explicit reader
# policy or the data_admin exemption, while remaining row-filtered regardless.
AUTHENTICATED_READ_RELATIONS = (
    "platform.billing.charges_daily",
    # The per-workspace breakdown behind the billing page's filter chips. Same
    # rows as charges_daily, split by the workspace the usage happened in, with
    # each workspace taking a share of the account's cost in proportion to its
    # units. An allocation for reporting, not a charge — charges_daily remains
    # the invoice.
    "platform.billing.charges_daily_by_workspace",
)

# The same breakdown split by ACTOR — which identity ran the query — and NOT
# granted to every authenticated caller. Within a shared billing account that
# names individuals and their activity, so it is gated on `data_admin` rather
# than handed out with the account's own billing.
#
# `data_admin` and not the member's `billing_role`: api-v2.md is explicit that
# `billing_role` is ADVISORY ONLY ("deliberately *not* something a consumer
# should enforce from the token alone"), because roles change inside a token's
# lifetime and the authoritative check is a live re-read in billing.opteryx.
# Deciding relation access from it would be exactly the enforcement that claim
# says it is not for. Gating account owners properly needs either that claim
# promoted to enforced, or a live membership lookup on the query path; neither
# belongs in this change.
ACTOR_BREAKDOWN_RELATION = "platform.billing.charges_daily_by_actor"

# The UNPRICED usage layer, gated on `data_admin` for the same reason the actor
# breakdown is: it is platform-wide and it is nobody's invoice.
#
# `charges_daily` drops usage it cannot attribute to an account. That is
# deliberate — NOTE 3 in xb500.opteryx's platform_billing_relations.sql: a gap
# someone has to explain must surface as a missing row, never silently become
# £0. Right for an invoice, wrong for an operator dashboard, which needs to see
# that usage precisely BECAUSE it is unattributed. These two views are the same
# numbers before that drop, and they are what platform-health reads; the billing
# surfaces go on reading the charge views.
#
# NOT added to AUTHENTICATED_READ_RELATIONS: they carry every account's rows and
# are no tenant's business. A data_admin is already exempt from the row filter,
# so this widens which relations an admin may name — not what any account can
# learn about another.
PLATFORM_USAGE_RELATIONS = (
    "platform.billing.usage_daily",
    "platform.billing.usage_hourly",
)


def access_policies_for_query(claims: Dict[str, Any]) -> List[Dict[str, str]]:
    """Access policies for executing a query, including authenticated-read grants.

    This is the caller's own policies plus a reader grant on AUTHENTICATED_READ_RELATIONS
    when the caller is authenticated.

    Kept separate from `get_access_policies_from_claims` on purpose: these are grants
    for *executing* a query, not statements about what the caller owns, so they should
    not surface the named relations in the service document or $metadata listings.
    Both paths now fnmatch the full relation name (see `read_grant_for_relation`), so
    routing them through the listing paths would no longer widen access — it would just
    advertise relations the caller never asked for.
    """
    policies = list(get_access_policies_from_claims(claims))

    if claims.get("identity"):
        policies.extend({"pattern": r, "role": "reader"} for r in AUTHENTICATED_READ_RELATIONS)
        # The actor breakdown names individuals, so it is granted separately and
        # only to data_admin. Added here rather than to the tuple above so an
        # ordinary member of a shared billing account cannot read who ran what.
        if DATA_ADMIN_ENTITLEMENT in entitlements_from_claims(claims):
            policies.append({"pattern": ACTOR_BREAKDOWN_RELATION, "role": "reader"})
            policies.extend(
                {"pattern": r, "role": "reader"} for r in PLATFORM_USAGE_RELATIONS
            )

    return policies


# Deny-all predicate. Opteryx documents an empty list as "no rows visible", but that
# path builds an `Eq(True, False)` literal comparison which the c-native execution engine
# cannot compile — it raises NotSupportedError ("filter predicate outside the c-native
# kernel set") instead of returning nothing, so callers get a 500 rather than an empty
# result. Matching the account against a value it can never hold achieves the same thing
# through a kernel that does exist. A NUL byte cannot appear in a JWT claim or a Firestore
# collection name, so nothing can collide with this.
_DENY_ALL_ACCOUNT = "\x00__deny_all__"


def visibility_filters_from_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
    """Build Opteryx row-level visibility filters for the caller.

    Opteryx keys these by relation name and applies them only to relations present in
    the dict — an absent relation is returned UNFILTERED. That makes this fail-open, so
    every billing relation is always given an entry: either a real predicate, or one
    that matches nothing. Never return `{}` for a caller we cannot identify; that would
    serve every account's billing data rather than none of it.

    Both the base table and the materialized rollups are keyed, because which one a
    query scans depends on which one it names — the rollups no longer expand into the
    base table.

    Filters for relations the query never touches cost nothing, so this is applied to
    every request rather than being special-cased per endpoint.

    Callers holding the data_admin entitlement are exempt: they get an empty dict, which
    Opteryx treats as "no filtering", so they see every account's rows.

    Returns:
        dict: {relation_name: DNF predicate list}, suitable for
              `visibility_filters=` on the Opteryx planner entry points.
    """
    if DATA_ADMIN_ENTITLEMENT in entitlements_from_claims(claims):
        return {}

    billing_account = claims.get("billing_account") or _DENY_ALL_ACCOUNT

    return {BILLING_NAMESPACE: [("billing_account", "Eq", billing_account)]}


def convert_policies_for_opteryx(policies: Optional[list]) -> List[Dict[str, str]]:
    """Convert JWT "policies" claim to Opteryx access_policies format.

    Accepts policies in various formats:
      - list of dicts: [{"id": ..., "role": "reader", "pattern": "benchmarks.*"}, ...]
      - list of lists/tuples: [[policy_id, role, pattern], ...]

    Returns:
        list: List of dicts in Opteryx format with "pattern", "role", and
              "policy" (the policy id, or None if the input format doesn't
              carry one) keys. Returns empty list if policies is None or empty.
    """
    if not policies:
        return []

    access_policies = []

    try:
        for p in policies:
            # dict format
            if isinstance(p, dict):
                role = p.get("role")
                pattern = p.get("pattern") or p.get("workspace") or p.get("resource")
                policy_id = p.get("id")
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role, "policy": policy_id})
            # list/tuple format: [id, role, pattern, ...]
            elif isinstance(p, (list, tuple)) and len(p) >= 3:
                policy_id = p[0]
                role = p[1]
                pattern = p[2]
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role, "policy": policy_id})
            # legacy/two-element tuples (role, pattern)
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                role = p[0]
                pattern = p[1]
                if role and pattern:
                    access_policies.append({"pattern": pattern, "role": role, "policy": None})
    except Exception as exc:
        logger.warning(f"Failed to convert policies to Opteryx format: {exc}. Policies: {policies}")
        return []

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


# Roles that permit reading a relation, and therefore permit seeing it exist at all.
_READ_CAPABLE_ROLES = frozenset({"reader", "writer", "admin", "owner"})


def role_allows_read(role: Optional[str]) -> bool:
    """Whether a role string permits reading."""
    if not role:
        return False
    return role.lower() in _READ_CAPABLE_ROLES


def _pattern_for_matching(pattern: str) -> str:
    """Normalise a policy pattern to something matchable against a full relation name.

    A pattern with no dot names a whole workstream rather than a relation —
    `convert_policies_for_opteryx` derives patterns from a policy's `workspace` field
    when it has no `pattern` field, which yields exactly that shape. Expanding it to
    `<workstream>.*` keeps those grants working; without it they would match no
    three-part relation name at all.
    """
    return pattern if "." in pattern else f"{pattern}.*"


def workstream_from_pattern(pattern: str) -> str:
    """The workstream a policy pattern can reach — everything before the first dot.

    This is deliberately a SUPERSET of what the policy grants: `opteryx.ops.findings`
    reaches into the `opteryx` workstream but grants only one relation within it. Use
    it to decide which workspaces to *fetch* from the catalog, never to decide whether
    the caller may see a given dataset — `read_grant_for_relation` does that.
    """
    return pattern.split(".", 1)[0]


def accessible_workstreams(policies: List[Dict[str, str]]) -> set:
    """The set of workstreams any of `policies` reaches into (a superset — see above)."""
    return {
        workstream_from_pattern(p["pattern"])
        for p in policies
        if p.get("pattern") and p.get("role")
    }


def roles_in_workstream(policies: List[Dict[str, str]], workstream: str) -> List[str]:
    """The roles `policies` hold on patterns reaching `workstream`, in policy order.

    A superset in the same sense as `accessible_workstreams`: a role on one
    relation counts as a role held in its workstream. That is the right grain for
    deciding which information_schema tables to OFFER in a workspace - a caller
    who owns one dataset there can see that dataset's triggers, so the triggers
    table has rows for them - while the engine decides row by row what is in it.
    """
    return [
        p["role"]
        for p in policies
        if p.get("pattern") and p.get("role") and workstream_from_pattern(p["pattern"]) == workstream
    ]


def virtual_information_schema_role(
    policies: List[Dict[str, str]],
    workstream: str,
    name: str,
    entitlements: Optional[List[str]] = None,
) -> Optional[str]:
    """The role under which `<workstream>.information_schema.<name>` is offered to a
    caller holding `policies`, or None when it is not offered to them at all.

    Each virtual table has a required action (`VIRTUAL_INFORMATION_SCHEMA_ACTIONS`
    - the one the engine gates its rows on), and the table is offered where the
    caller holds a role in the workspace that confers it. Whether a role confers
    an action is asked of opteryx-access, the library the engine's own gate is
    answered from, so the listing cannot offer a table the query would return
    empty, nor withhold one it would fill.

    `personal` is the caller's own namespace, held as owner without any policy
    saying so; `public` is readable by everyone the same way. Both are decided
    here rather than left to the caller for the same reason the engine decides
    implicit grants in one place.

    The role reported is the FIRST qualifying one in policy order, as
    `read_grant_for_relation` reports the first qualifying grant.
    """
    from opteryx_access.actions import action_allowed_for_role

    from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_ACTIONS

    action = VIRTUAL_INFORMATION_SCHEMA_ACTIONS.get(name)
    if action is None:
        return None

    if workstream == "personal":
        roles = ["owner"]
    else:
        roles = roles_in_workstream(policies, workstream)
        if workstream == "public":
            roles.append("reader")

    for role in roles:
        if action_allowed_for_role(role, action):
            return role

    # An entitlement confers the action without a role: `automation_admin::public`
    # is AUTOMATE on `public.*`, held by an account no policy can grant it to.
    # Resolved by opteryx-access, the same library the engine's gate is answered
    # from, so this offers exactly the tables that query would fill. Reported as
    # `entitlement`, which is deliberately not a role name.
    if entitlements:
        from opteryx_access import entitlement_permits_workspace_action
        from opteryx_access import resolve_entitlements

        for entitlement in resolve_entitlements(entitlements):
            if entitlement_permits_workspace_action(entitlement, workstream, action):
                return "entitlement"
    return None


def visible_virtual_information_schema(
    policies: List[Dict[str, str]], workstream: str, entitlements: Optional[List[str]] = None
) -> List[Tuple[str, str]]:
    """Every virtual information_schema table offered in `workstream` to a caller
    holding `policies`, as `(name, role)` in registry order."""
    from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_TABLES

    offered = []
    for name in VIRTUAL_INFORMATION_SCHEMA_TABLES:
        role = virtual_information_schema_role(policies, workstream, name, entitlements=entitlements)
        if role is not None:
            offered.append((name, role))
    return offered


def read_grant_for_relation(
    policies: List[Dict[str, str]], relation: str
) -> Optional[Tuple[str, Optional[str]]]:
    """The first read-capable policy granting `relation`, as `(role, policy_id)`.

    `relation` is a fully-qualified `workstream.collection.dataset` name, and the
    policy's pattern is matched against the whole of it — the same fnmatch the query
    path applies. Matching on anything less (e.g. collapsing the pattern to its
    workstream) would let a grant on one relation disclose every relation beside it.

    Case-sensitive on purpose: `fnmatch.fnmatch` normalises case per-platform, which
    would make an authorization decision depend on the host OS.
    """
    for policy in policies:
        role = policy.get("role")
        pattern = policy.get("pattern")
        if not role or not pattern or not role_allows_read(role):
            continue
        if fnmatch.fnmatchcase(relation, _pattern_for_matching(pattern)):
            return role, policy.get("policy")
    return None
