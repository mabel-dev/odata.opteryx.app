"""Tests for row-level visibility filtering of billing data.

These guard behaviour that fails silently and expensively if it regresses: Opteryx
leaves unlisted relations UNFILTERED, so a mistake here serves every tenant's billing
data rather than raising. Note that asserting "account A sees A's rows" passes even
when filtering is entirely broken — the assertions that matter are the ones checking
that a filter is present and that it is keyed correctly.
"""

from app.auth.permissions import (
    AUTHENTICATED_READ_RELATIONS,
    BILLING_RELATION,
    BILLING_RELATIONS,
    BILLING_ROLLUP_RELATIONS,
    DATA_ADMIN_ENTITLEMENT,
    billing_account_from_claims,
    entitlements_from_claims,
    visibility_filters_from_claims,
)


def _predicate(claims):
    return visibility_filters_from_claims(claims).get(BILLING_RELATION)


def test_every_readable_billing_relation_is_filtered():
    """The rollups are materialized: a query against one scans the rollup and never
    touches the base table, so a filter on the base table alone reaches nothing.

    Derived from AUTHENTICATED_READ_RELATIONS rather than a hand-written list, so a
    rollup added to the allowlist is covered here without anyone remembering to add it —
    the failure mode being guarded (unlisted relation => unfiltered => every tenant's
    rows) is silent, so it must not depend on that memory.
    """
    filters = visibility_filters_from_claims({"identity": "u", "billing_account": "acme"})

    assert BILLING_RELATION == "opteryx.ops.billing"
    assert BILLING_ROLLUP_RELATIONS, "expected at least one rollup over the billing table"

    for relation in AUTHENTICATED_READ_RELATIONS:
        assert filters.get(relation) == [("billing_account", "Eq", "acme")], (
            f"{relation} is readable by any authenticated caller but not row-filtered"
        )


def test_reader_grants_and_row_filters_cover_the_same_relations():
    """A relation readable but unfiltered leaks every tenant; filtered but unreadable
    is merely broken. Every relation the blanket grant covers must also be filtered —
    but the reverse need not hold, since BILLING_RELATION is filtered defensively for
    whoever holds an explicit reader policy on it without being in the blanket grant."""
    filters = visibility_filters_from_claims({"identity": "u", "billing_account": "acme"})

    assert set(AUTHENTICATED_READ_RELATIONS) == set(BILLING_ROLLUP_RELATIONS)
    assert BILLING_RELATION not in AUTHENTICATED_READ_RELATIONS, (
        "the raw event table must not be blanket-readable by every authenticated caller"
    )
    assert set(AUTHENTICATED_READ_RELATIONS) <= set(filters)
    assert set(filters) == set(BILLING_RELATIONS)


def test_account_is_scoped_to_the_callers_own_account():
    assert _predicate({"identity": "u", "billing_account": "acme"}) == [
        ("billing_account", "Eq", "acme")
    ]


def test_unknown_account_denies_rather_than_leaking():
    """A caller we cannot identify must get no rows, not every row."""
    for claims in (
        {"identity": "u", "billing_account": None},
        {"identity": None, "billing_account": None},
        {},
    ):
        predicate = _predicate(claims)
        assert predicate, "billing relation must always carry a filter (absent == unfiltered)"
        column, op, value = predicate[0]
        assert (column, op) == ("billing_account", "Eq")
        assert value not in (None, ""), "must not match rows with a null/empty account"


def test_deny_all_is_not_the_empty_list():
    """Opteryx documents `[]` as deny-all, but it compiles to an Eq(True, False) literal
    the c-native engine cannot execute — it raises NotSupportedError instead of returning
    nothing, turning a denial into a 500."""
    assert _predicate({"identity": "u", "billing_account": None}) != []


def test_data_admin_is_exempt_from_row_filtering():
    for claims in (
        {"identity": "b", "billing_account": "opteryx", "entitlement": "user_admin,data_admin"},
        {"identity": "b", "billing_account": "opteryx", "entitlements": ["data_admin"]},
        {"identity": "b", "billing_account": None, "entitlement": "data_admin"},
    ):
        assert visibility_filters_from_claims(claims) == {}


def test_other_entitlements_do_not_grant_exemption():
    claims = {"identity": "u", "billing_account": "acme", "entitlement": "user_admin"}
    assert _predicate(claims) == [("billing_account", "Eq", "acme")]


def test_entitlements_accept_both_claim_shapes_and_namespacing():
    flattened = {"entitlement": "user_admin,data_admin"}
    listed = {"entitlements": ["user_admin", "data_admin"]}
    namespaced = {"entitlement": "urn:opteryx:entitlement:data_admin"}

    assert entitlements_from_claims(flattened) == {"user_admin", "data_admin"}
    assert entitlements_from_claims(listed) == {"user_admin", "data_admin"}
    assert DATA_ADMIN_ENTITLEMENT in entitlements_from_claims(namespaced)
    assert entitlements_from_claims({}) == set()


def test_billing_account_comes_from_its_own_claim():
    """Many users bill to one account, so the account is carried explicitly rather
    than inferred from identity."""
    claims = {"sub": "bastian", "identity": "bastian", "billing_account": "acme"}
    assert billing_account_from_claims(claims) == "acme"


def test_scope_is_never_read_as_a_billing_account():
    """Regression: `scope` is an OAuth permission list, not a tenant identity.

    register.opteryx writes `scopes: ["opteryx"]` for every self-service signup and
    admin-supplied permission words ("read,write") for the rest. Reading that as the
    billing account charged every such user to "opteryx" -- which is both the house
    account and the system workspace name, so the usage read back as though the
    workspace were the payer.
    """
    # The exact shape production was emitting for a self-service user.
    assert billing_account_from_claims({"sub": "bastian", "scope": "opteryx"}) == "bastian"
    # A permission word must never become a tenant to charge.
    assert billing_account_from_claims({"sub": "bastian", "scope": "public"}) == "bastian"
    assert billing_account_from_claims({"sub": "bastian", "scope": "read,write"}) == "bastian"
    # An explicit account is not overridden by whatever scope happens to carry.
    claims = {"sub": "bastian", "scope": "opteryx", "billing_account": "acme"}
    assert billing_account_from_claims(claims) == "acme"


def test_absent_account_bills_to_the_caller_themselves():
    """An unscoped individual bills to themselves, not to the house account."""
    assert billing_account_from_claims({"sub": "bastian"}) == "bastian"
    assert billing_account_from_claims({"sub": "bastian", "billing_account": None}) == "bastian"


def test_unidentifiable_caller_yields_no_account():
    """No account and no subject must not resolve to some usable string -- callers
    rely on None to deny billing rows rather than leak them."""
    assert billing_account_from_claims({}) is None
    assert billing_account_from_claims({"sub": None}) is None
    assert billing_account_from_claims({"sub": "   "}) is None
    assert billing_account_from_claims({"billing_account": ""}) is None
    # A non-string account is ignored rather than coerced into a bogus tenant name.
    assert billing_account_from_claims({"billing_account": ["acme"]}) is None


def test_only_data_admin_ever_produces_an_empty_filter_set():
    """An empty dict means 'no filtering at all'. Nothing but the admin exemption may
    produce one, or that becomes a route to unfiltered billing data."""
    for claims in (
        {"identity": "u", "billing_account": "acme"},
        {"identity": "u", "billing_account": None},
        {"identity": None},
        {"identity": "u", "entitlement": "user_admin"},
        {},
    ):
        assert visibility_filters_from_claims(claims) != {}
