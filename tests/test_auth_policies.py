from app.auth.permissions import convert_policies_for_opteryx, get_access_policies_from_claims


def test_policies_list_tuple_converted():
    claims = {
        "policies": [["72fabd56", "reader", "benchmarks.*"], ["a1b2c3", "writer", "opteryx.intel.*"]]
    }

    policies = get_access_policies_from_claims(claims)
    assert policies == [{"pattern": "benchmarks.*", "role": "reader"}, {"pattern": "opteryx.intel.*", "role": "writer"}]


def test_policies_dict_converted():
    claims = {
        "policies": [
            {"id": "72fabd56", "role": "reader", "pattern": "benchmarks.*"},
            {"id": "a1b2c3", "role": "writer", "pattern": "opteryx.intel.*"},
        ]
    }

    policies = get_access_policies_from_claims(claims)
    assert policies == [{"pattern": "benchmarks.*", "role": "reader"}, {"pattern": "opteryx.intel.*", "role": "writer"}]


def test_get_access_policies_does_not_mutate_claims():
    claims = {
        "policies": [["72fabd56", "reader", "benchmarks.*"]]
    }

    _ = get_access_policies_from_claims(claims)
    assert "permissions" not in claims
    assert claims["policies"] == [["72fabd56", "reader", "benchmarks.*"]]
