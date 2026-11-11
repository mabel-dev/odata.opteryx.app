from app.auth.permissions import accessible_workstreams
from app.auth.permissions import get_access_policies_from_claims
from opteryx_access import workspace_roles

from app.auth.permissions import grants_from_policies
from app.auth.permissions import read_grant_for_relation
from app.auth.permissions import role_allows_read
from app.auth.permissions import virtual_information_schema_role
from app.auth.permissions import visible_virtual_information_schema


def test_policies_list_tuple_converted():
    claims = {
        "policies": [
            ["72fabd56", "reader", "benchmarks.*"],
            ["a1b2c3", "writer", "opteryx.intel.*"],
        ]
    }

    policies = get_access_policies_from_claims(claims)
    assert policies == [
        {"pattern": "benchmarks.*", "role": "reader", "policy": "72fabd56"},
        {"pattern": "opteryx.intel.*", "role": "writer", "policy": "a1b2c3"},
    ]


def test_policies_dict_converted():
    claims = {
        "policies": [
            {"id": "72fabd56", "role": "reader", "pattern": "benchmarks.*"},
            {"id": "a1b2c3", "role": "writer", "pattern": "opteryx.intel.*"},
        ]
    }

    policies = get_access_policies_from_claims(claims)
    assert policies == [
        {"pattern": "benchmarks.*", "role": "reader", "policy": "72fabd56"},
        {"pattern": "opteryx.intel.*", "role": "writer", "policy": "a1b2c3"},
    ]


def test_get_access_policies_does_not_mutate_claims():
    claims = {"policies": [["72fabd56", "reader", "benchmarks.*"]]}

    _ = get_access_policies_from_claims(claims)
    assert "permissions" not in claims
    assert claims["policies"] == [["72fabd56", "reader", "benchmarks.*"]]


# A grant naming a single relation must not disclose the rest of the workstream.
# The listing paths used to key policies by `pattern.split(".")[0]`, so a writer grant
# on one relation listed -- and reported a role for -- every dataset beside it.
RELATION_SCOPED = [{"pattern": "opteryx.ops.sast_findings", "role": "writer", "policy": "6276465b"}]


def test_relation_scoped_grant_matches_only_that_relation():
    assert read_grant_for_relation(RELATION_SCOPED, "opteryx.ops.sast_findings") == (
        "writer",
        "6276465b",
    )


def test_relation_scoped_grant_does_not_leak_across_the_workstream():
    for sibling in (
        "opteryx.ops.other_findings",
        "opteryx.finance.salaries",
        "opteryx.hr.reviews",
    ):
        assert read_grant_for_relation(RELATION_SCOPED, sibling) is None


def test_relation_scoped_grant_still_reaches_its_workstream_for_fetching():
    # The catalog fetch is deliberately workstream-wide; per-dataset filtering is what
    # narrows it. If this ever returned nothing the granted relation would vanish too.
    assert accessible_workstreams(RELATION_SCOPED) == {"opteryx"}


def test_wildcard_patterns_still_grant_their_scope():
    policies = [{"pattern": "benchmarks.*", "role": "reader", "policy": "p1"}]
    assert read_grant_for_relation(policies, "benchmarks.data.tpch") == ("reader", "p1")
    assert read_grant_for_relation(policies, "other.data.tpch") is None


def test_collection_wildcard_does_not_span_collections():
    policies = [{"pattern": "opteryx.intel.*", "role": "writer", "policy": "p2"}]
    assert read_grant_for_relation(policies, "opteryx.intel.threats") == ("writer", "p2")
    assert read_grant_for_relation(policies, "opteryx.ops.secrets") is None


def test_dotless_pattern_grants_the_whole_workspace():
    # convert_policies_for_opteryx falls back to a policy's `workspace` field, which
    # yields a bare workstream name; that shape means "everything in this workspace".
    policies = [{"pattern": "benchmarks", "role": "reader", "policy": "p3"}]
    assert read_grant_for_relation(policies, "benchmarks.data.tpch") == ("reader", "p3")
    assert read_grant_for_relation(policies, "benchmarksX.data.tpch") is None


def test_non_read_roles_grant_nothing():
    policies = [{"pattern": "opteryx.ops.sast_findings", "role": "none", "policy": "p4"}]
    assert read_grant_for_relation(policies, "opteryx.ops.sast_findings") is None


def test_role_allows_read_covers_read_capable_roles():
    # The roles opteryx-access defines -- the engine's gate answers from the same
    # table. `admin` is not one of them, so it reads nothing here either.
    assert all(role_allows_read(r) for r in ("reader", "writer", "owner", "OWNER"))
    assert not any(role_allows_read(r) for r in (None, "", "none", "denied", "admin"))


# ---------------------------------------------------------------------------
# Which information_schema tables are OFFERED in a workspace follows the tier the
# engine filters their rows at: shape tables to any role, automation and grants
# to owners. Offering `tasks` to a reader would list an always-empty table whose
# columns describe what they may not see.
# ---------------------------------------------------------------------------

# `listeners` sits with the shape tables even though it is about automation:
# its rows are the CALLER'S OWN subscriptions and nobody else's, and LISTEN is
# gated on reading what the object writes rather than on owning it - so a
# reader can hold rows here, and it is the only table where a non-owner
# subscriber can see what they subscribed to.
#
# `forks` sits here for the same kind of reason: where a dataset's data came
# from is part of its shape, not a fact about who may reach it, and the engine
# gates each row on READ of the dataset in this workspace - so a reader holds
# exactly the rows for the datasets they can already see.
#
# `maintenance` sits here because the engine does not gate it: one row saying
# whether the workspace is kept compacted, naming no principal.
SHAPE_TABLES = [
    "tables",
    "columns",
    "views",
    "column_relationships",
    "schemata",
    "listeners",
    "forks",
    "maintenance",
]
OWNER_TABLES = ["triggers", "tasks", "grants", "secrets"]


def _names(offered):
    return [name for name, _role in offered]


def test_a_reader_is_offered_the_shape_tables_only():
    policies = [{"pattern": "ws.*", "role": "reader", "policy": "p1"}]

    offered = visible_virtual_information_schema(policies, "ws", identity="alice")

    assert sorted(_names(offered)) == sorted(SHAPE_TABLES)
    assert {role for _name, role in offered} == {"reader"}


def test_a_writer_is_offered_no_more_than_a_reader():
    policies = [{"pattern": "ws.*", "role": "writer", "policy": "p1"}]

    assert sorted(_names(visible_virtual_information_schema(policies, "ws", identity="alice"))) == sorted(SHAPE_TABLES)


def test_an_owner_is_offered_every_table():
    policies = [{"pattern": "ws.*", "role": "owner", "policy": "p1"}]

    offered = visible_virtual_information_schema(policies, "ws", identity="alice")

    assert sorted(_names(offered)) == sorted(SHAPE_TABLES + OWNER_TABLES)
    assert {role for _name, role in offered} == {"owner"}


def test_owner_on_one_dataset_still_offers_the_automation_tables():
    """The tier is held per workspace, not per relation: an owner of one dataset
    can see that dataset's triggers, so the triggers table has rows for them.
    Which rows is the engine's decision."""
    policies = [{"pattern": "ws.sales.orders", "role": "owner", "policy": "p1"}]

    assert virtual_information_schema_role(policies, "ws", "triggers", identity="alice") == "owner"
    assert virtual_information_schema_role(policies, "ws", "tasks", identity="alice") == "owner"
    assert virtual_information_schema_role(policies, "ws", "grants", identity="alice") == "owner"
    # Offered, but the engine refuses it as a whole (ALTER on ws.*): a dataset
    # owner reaches the table and gets a 403, not an empty list.
    assert virtual_information_schema_role(policies, "ws", "secrets", identity="alice") == "owner"


def test_secrets_are_never_offered_below_owner():
    for role in ("reader", "writer"):
        policies = [{"pattern": "ws.*", "role": role, "policy": "p1"}]
        assert virtual_information_schema_role(policies, "ws", "secrets", identity="alice") is None


def test_a_role_in_another_workspace_offers_nothing_here():
    policies = [{"pattern": "elsewhere.*", "role": "owner", "policy": "p1"}]

    assert visible_virtual_information_schema(policies, "ws", identity="alice") == []
    assert workspace_roles("ws", "alice", grants_from_policies(policies)) == []


def test_the_role_reported_is_the_first_qualifying_one():
    policies = [
        {"pattern": "ws.a.*", "role": "reader", "policy": "p1"},
        {"pattern": "ws.b.*", "role": "owner", "policy": "p2"},
    ]

    assert virtual_information_schema_role(policies, "ws", "tables", identity="alice") == "reader"
    assert virtual_information_schema_role(policies, "ws", "tasks", identity="alice") == "owner"


def test_personal_is_offered_everything_as_owner_without_a_policy():
    offered = visible_virtual_information_schema([], "personal", identity="alice")

    assert sorted(_names(offered)) == sorted(SHAPE_TABLES + OWNER_TABLES)
    assert {role for _name, role in offered} == {"owner"}


def test_public_is_offered_the_shape_tables_as_reader_without_a_policy():
    offered = visible_virtual_information_schema([], "public", identity="alice")

    assert sorted(_names(offered)) == sorted(SHAPE_TABLES)
    assert {role for _name, role in offered} == {"reader"}


def test_an_unrecognised_role_is_offered_nothing():
    # "admin" is a live role in services not yet migrated; opteryx-access
    # confers nothing on it, so the engine would return no rows, so no table.
    policies = [{"pattern": "ws.*", "role": "admin", "policy": "p1"}]

    assert visible_virtual_information_schema(policies, "ws", identity="alice") == []


def test_a_name_that_is_not_a_virtual_table_is_not_offered():
    policies = [{"pattern": "ws.*", "role": "owner", "policy": "p1"}]

    assert virtual_information_schema_role(policies, "ws", "orders", identity="alice") is None


def test_scoped_entitlement_offers_automation_tables_where_no_policy_can():
    """`automation_admin::public` is AUTOMATE on `public.*`, held by an account no
    policy can grant it to. The tier gate resolves it through opteryx-access, the
    same library the engine's gate is answered from, so odata offers exactly the
    tables the query would fill. Reported as `entitlement`, which is not a role."""
    held = ["data_admin", "automation_admin::public"]
    assert virtual_information_schema_role([], "public", "triggers", identity="alice") is None
    assert virtual_information_schema_role([], "public", "triggers", entitlements=held, identity="alice") == "entitlement"
    assert virtual_information_schema_role([], "public", "tasks", entitlements=held, identity="alice") == "entitlement"
    # Scope is in the name: nothing in a workspace it does not name.
    assert virtual_information_schema_role([], "opteryx", "triggers", entitlements=held, identity="alice") is None
    # A role that already confers the action still wins, and is reported as itself.
    owner = [{"pattern": "opteryx.*", "role": "owner"}]
    assert virtual_information_schema_role(owner, "opteryx", "triggers", entitlements=held, identity="alice") == "owner"
    # Unrecognized names confer nothing.
    assert virtual_information_schema_role([], "public", "triggers", entitlements=["platform_admin"], identity="alice") is None
    offered = dict(visible_virtual_information_schema([], "public", entitlements=held, identity="alice"))
    assert offered["triggers"] == "entitlement" and offered["tasks"] == "entitlement"
