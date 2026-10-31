"""A query the engine refuses because of a column is a 400, whichever option named it.

`$apply=groupby((region),aggregate(population with sum as total))` against
public.geopolitics.countries returned 500. `population` is a real column there,
but a VARCHAR one, and opteryx refuses SUM over VARCHAR with NotSupportedError --
which, unlike ColumnNotFoundError, is not a SqlError, so it fell past the 400
mapping to the generic handler.

These run the real engine: the request goes through the route, and
execute_odata_query is pointed at the built-in `$planets` dataset, so each case
asserts on what the engine actually raises for that query rather than on an
exception the test constructed.
"""

import pytest

from app.routes.v4 import interface

_PATH = "/api/v4/test/planets/planets"


@pytest.fixture
def planets(authenticated_client, monkeypatch):
    real = interface.execute_odata_query

    def against_planets(**kwargs):
        return real(**{**kwargs, "table_name": "$planets"})

    monkeypatch.setattr(interface, "execute_odata_query", against_planets)
    return authenticated_client


def _bad_request(response):
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "BadRequest"
    return error["message"]


@pytest.mark.parametrize(
    "params",
    (
        {"$apply": "groupby((name),aggregate(nope with sum as total))"},
        {"$apply": "aggregate(nope with sum as total)"},
        {"$apply": "groupby((nope),aggregate(id with sum as total))"},
        {"$apply": "groupby((nope))"},
        {"$apply": "filter(nope eq 1)"},
        {"$select": "nope"},
        {"$orderby": "nope desc"},
        {"$filter": "nope eq 1"},
        {"$compute": "nope mul 2 as doubled"},
    ),
    ids=lambda params: "&".join(f"{k}={v}" for k, v in params.items()),
)
def test_an_unknown_column_is_a_bad_request_naming_it(planets, params):
    message = _bad_request(planets.get(_PATH, params=params))

    assert "nope" in message


@pytest.mark.parametrize("method", ("sum", "average"))
def test_a_numeric_aggregate_over_a_string_column_is_a_bad_request(planets, method):
    """The case that was a 500: the column exists, but the aggregate cannot apply to it."""
    response = planets.get(
        _PATH, params={"$apply": f"groupby((id),aggregate(name with {method} as total))"}
    )

    message = _bad_request(response)
    assert "VARCHAR" in message


def test_the_same_aggregate_over_a_numeric_column_runs(planets):
    """The counterpart: the shape of the query is fine, only the column type was not."""
    response = planets.get(_PATH, params={"$apply": "aggregate(mass with sum as total)"})

    assert response.status_code == 200, response.text
    assert response.json()["value"][0]["total"] > 0
