from app.auth.permissions import accessible_workstreams
from app.auth.permissions import get_access_policies_from_claims
from app.auth.permissions import read_grant_for_relation
from app.auth.permissions import role_allows_read


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
    assert all(role_allows_read(r) for r in ("reader", "writer", "admin", "owner", "OWNER"))
    assert not any(role_allows_read(r) for r in (None, "", "none", "denied"))
