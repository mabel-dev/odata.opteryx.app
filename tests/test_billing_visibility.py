"""Tests for row-level visibility filtering of billing data.

These guard behaviour that fails silently and expensively if it regresses: Opteryx
leaves unlisted relations UNFILTERED, so a mistake here serves every tenant's billing
data rather than raising. Note that asserting "account A sees A's rows" passes even
when filtering is entirely broken — the assertions that matter are the ones checking
that a filter is present and that it is keyed correctly.
"""

from app.auth.permissions import (
    BILLING_RELATION,
    DATA_ADMIN_ENTITLEMENT,
    billing_account_from_claims,
    entitlements_from_claims,
    visibility_filters_from_claims,
)


def _predicate(claims):
    return visibility_filters_from_claims(claims).get(BILLING_RELATION)


def test_filter_is_keyed_on_the_base_table_not_the_views():
    """Opteryx expands views before applying filters, so a filter keyed on a view name
    matches no scan and silently returns everything."""
    filters = visibility_filters_from_claims({"identity": "u", "billing_account": "acme"})

    assert BILLING_RELATION in filters
    assert BILLING_RELATION == "opteryx.ops.billing"
    for view in ("query_count_billing", "storage_billing", "processed_billing"):
        assert f"opteryx.ops.{view}" not in filters


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


def test_billing_account_comes_from_scope_not_identity():
    """Many users bill to one account, so identity is not the billing account."""
    claims = {"sub": "bastian", "identity": "bastian", "scope": "opteryx"}
    assert billing_account_from_claims(claims) == "opteryx"


def test_multi_valued_scope_is_refused_rather_than_guessed():
    """Guessing a delimiter would attribute usage to the wrong tenant."""
    assert billing_account_from_claims({"scope": "acme other"}) is None
    assert billing_account_from_claims({"scope": "acme,other"}) is None
    assert billing_account_from_claims({"scope": ["acme"]}) == "acme"
    assert billing_account_from_claims({"scope": "  acme  "}) == "acme"
    assert billing_account_from_claims({"scope": ""}) is None
    assert billing_account_from_claims({}) is None


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
