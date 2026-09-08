"""Tests for OData v4 minimal conformance §13.1.1 item 5: Accept (8.2.1) and
OData-MaxVersion (8.2.7) header semantics.

These checks happen in `app.odata.content_negotiation.require_acceptable`,
wired in as a route dependency ahead of query-option parsing -- see
`tests/test_unsupported_options.py` for the sibling "unsupported option ->
501" checks these headers are the request-level counterpart of.
"""

from app.odata.content_negotiation import _accepts, _parse_version


class TestAcceptHeaderUnit:
    """Unit-level coverage of the media-range matcher itself."""

    def test_no_wildcard_no_match_rejected(self):
        assert _accepts("application/xml", ("application/json",)) is False

    def test_exact_match_accepted(self):
        assert _accepts("application/json", ("application/json",)) is True

    def test_star_star_accepted(self):
        assert _accepts("*/*", ("application/json",)) is True

    def test_type_star_accepted(self):
        assert _accepts("application/*", ("application/json",)) is True

    def test_params_ignored(self):
        assert _accepts("application/json;odata.metadata=minimal", ("application/json",)) is True

    def test_browser_style_header_accepted(self):
        # A real browser's default Accept header -- must not 406 someone
        # hitting the URL by hand.
        header = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        assert _accepts(header, ("application/json",)) is True

    def test_multiple_ranges_first_no_match_second_does(self):
        assert _accepts("text/plain, application/json", ("application/json",)) is True


class TestMaxVersionParsingUnit:
    def test_parses_major_minor(self):
        assert _parse_version("4.0") == (4, 0)

    def test_parses_with_trailing_params(self):
        assert _parse_version("3.0;q=1") == (3, 0)

    def test_unparsable_returns_none(self):
        assert _parse_version("not-a-version") is None


class TestAcceptHeaderOnDataRoute:
    """GET .../{dataset} only ever returns application/json."""

    def test_no_accept_header_allowed(self, client):
        response = client.get("/api/v4/public/demo/users?$top=1")
        assert response.status_code != 406

    def test_accept_json_allowed(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"Accept": "application/json"}
        )
        assert response.status_code != 406

    def test_accept_star_star_allowed(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"Accept": "*/*"}
        )
        assert response.status_code != 406

    def test_accept_xml_only_rejected(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"Accept": "application/xml"}
        )
        assert response.status_code == 406
        data = response.json()
        assert "error" in data
        assert "Accept" in data["error"]["message"]

    def test_accept_rejection_error_format(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"Accept": "text/csv"}
        )
        assert response.status_code == 406
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "code" in data["error"]
        assert "message" in data["error"]


class TestAcceptHeaderOnMetadataRoute:
    """GET .../{dataset}/$metadata only ever returns application/xml."""

    def test_no_accept_header_allowed(self, client):
        response = client.get("/api/v4/public/demo/users/$metadata")
        assert response.status_code != 406

    def test_accept_xml_allowed(self, client):
        response = client.get(
            "/api/v4/public/demo/users/$metadata", headers={"Accept": "application/xml"}
        )
        assert response.status_code != 406

    def test_accept_json_only_rejected(self, client):
        response = client.get(
            "/api/v4/public/demo/users/$metadata", headers={"Accept": "application/json"}
        )
        assert response.status_code == 406


class TestODataMaxVersion:
    """OData-MaxVersion caps what version of the protocol the client can read;
    this service only speaks 4.0, so a lower ceiling can never be honoured."""

    def test_no_header_allowed(self, client):
        response = client.get("/api/v4/public/demo/users?$top=1")
        assert response.status_code != 400 or "MaxVersion" not in response.text

    def test_max_version_4_0_allowed(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "4.0"}
        )
        assert response.status_code != 400 or "MaxVersion" not in response.text

    def test_max_version_4_01_allowed(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "4.01"}
        )
        assert response.status_code != 400 or "MaxVersion" not in response.text

    def test_max_version_3_0_rejected(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "3.0"}
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "MaxVersion" in data["error"]["message"]

    def test_max_version_garbage_is_lenient(self, client):
        # A header we can't parse is ignored rather than failing the request --
        # see the docstring on _parse_version for why.
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "nonsense"}
        )
        assert response.status_code != 400 or "MaxVersion" not in response.text

    def test_rejection_error_format(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "2.0"}
        )
        assert response.status_code == 400
        assert response.headers.get("OData-Version") == "4.0"
        data = response.json()
        assert "code" in data["error"]
        assert "message" in data["error"]
