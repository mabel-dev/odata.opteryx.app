"""CSV output on the data route, for spreadsheet tools that can only read CSV
(Google Sheets' IMPORTDATA, Excel's Data > From Web). Not an OData format:
see `wants_csv` and `rows_to_csv` in app/routes/v4/interface.py."""

import csv
import io
import json

from app.routes.v4.interface import rows_to_csv
from app.routes.v4.interface import wants_csv


class TestWantsCsv:
    def test_format_csv(self):
        assert wants_csv(["csv"], None) is True

    def test_format_media_type_and_case(self):
        assert wants_csv(["TEXT/CSV"], None) is True

    def test_format_json_wins_over_accept(self):
        assert wants_csv(["json"], "text/csv") is False

    def test_unknown_format_keeps_json(self):
        # Long-standing behaviour: any other $format is ignored.
        assert wants_csv(["atom"], None) is False

    def test_accept_csv_only(self):
        assert wants_csv([], "text/csv") is True

    def test_accept_csv_and_json_prefers_json(self):
        assert wants_csv([], "text/csv, application/json") is False

    def test_wildcard_stays_json(self):
        # A browser, curl, Power Query: none of them should suddenly get CSV.
        assert wants_csv([], "*/*") is False
        assert wants_csv([], None) is False


def _parse(body: bytes):
    text = body.decode("utf-8")
    assert text.startswith("﻿"), "Excel needs the BOM to read UTF-8"
    return list(csv.reader(io.StringIO(text[1:])))


class TestRowsToCsv:
    def test_header_and_order_follow_columns(self):
        rows = json.dumps([{"b": 2, "a": "x"}]).encode()
        assert _parse(rows_to_csv(rows, ["a", "b"])) == [["a", "b"], ["x", "2"]]

    def test_nulls_bools_and_nested(self):
        rows = json.dumps(
            [{"n": None, "t": True, "f": False, "arr": [1, 2], "obj": {"k": "v"}}]
        ).encode()
        out = _parse(rows_to_csv(rows, ["n", "t", "f", "arr", "obj"]))
        assert out[1] == ["", "true", "false", "[1,2]", '{"k":"v"}']

    def test_quotes_commas_newlines_and_unicode(self):
        rows = json.dumps([{"s": 'a, "b"\nc', "u": "Zürich 🇨🇭"}]).encode()
        out = _parse(rows_to_csv(rows, ["s", "u"]))
        assert out[1] == ['a, "b"\nc', "Zürich 🇨🇭"]

    def test_empty_page_still_has_header(self):
        assert _parse(rows_to_csv(b"[]", ["a", "b"])) == [["a", "b"]]

    def test_columns_fall_back_to_first_row(self):
        rows = json.dumps([{"a": 1}]).encode()
        assert _parse(rows_to_csv(rows, [])) == [["a"], ["1"]]


class TestDataRoute:
    def test_format_csv_not_rejected_before_identity(self, client):
        response = client.get("/api/v4/public/demo/users?$format=csv&$top=1")
        assert response.status_code != 406
