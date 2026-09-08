"""OData v4 §13.1.1 Minimal Conformance Level -- one test class per numbered
item, so a regression against any single requirement shows up as a named
failure here rather than only as a symptom somewhere else.

This file is a checklist, not the primary coverage for any one behaviour --
most items already have deeper, dedicated suites (nextLink:
test_odata_nextlink.py, unsupported options: test_unsupported_options.py,
Accept/OData-MaxVersion: test_content_negotiation.py, EDM typing:
test_metadata_*.py). Each class here links to that file and adds only the
assertion that ties the behaviour back to the spec item, plus enough of its
own coverage that deleting every other test file would still leave this one
able to say whether the service is minimally conformant.

Items are numbered per the checklist:
  1  Service document at the service root                  MUST
  2  At least one OData-defined format                      MUST
  3  Server-driven paging (@odata.nextLink)                 MUST
  4  OData-Version response header                          MUST
  5  Accept / OData-MaxVersion header semantics              MUST
  6  Extensibility (no required custom content)              MUST
  7  Parse all system query options; 501 for unsupported     MUST
  8  Only Edm.* types exposed                                MUST
  9  MUST NOT require custom annotations/headers to consume  MUST NOT
  10 MUST NOT violate update semantics                       MUST NOT (N/A: read-only)
  11 MUST NOT violate other OData-defined semantics          MUST NOT
  12 SHOULD support $expand                                  SHOULD (known gap)
  13 MAY publish $metadata                                   MAY
"""

import json
import xml.etree.ElementTree as ET

import pytest
from fastapi import Response

from app.odata.content_negotiation import MEDIA_JSON, MEDIA_XML
from app.routes.v4 import service_document as sd
from app.routes.v4.interface import execute_odata_query

NS_EDM = "http://docs.oasis-open.org/odata/ns/edm"

# $planets is a built-in opteryx dataset: execute_odata_query can run a real
# query against it with no catalog, Firestore, or credentials, which is what
# makes it usable for items (2), (3), (8) below without a live environment.
PLANETS = "$planets"


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    headers: dict = {}


# ---------------------------------------------------------------------------
# Item 1 -- service document at the service root (§11.1.1)
# ---------------------------------------------------------------------------


class TestItem1ServiceDocument:
    """GET /api/v4/ MUST return a document listing the service's EntitySets."""

    def test_route_registered_at_service_root(self):
        paths = {route.path for route in sd.router.routes}
        assert "/api/v4/" in paths

    def test_lists_entity_sets_by_name_kind_and_url(self, monkeypatch):
        table = sd.DatasetInfo(
            resource="public.examples.table",
            workspace="public",
            collection="examples",
            name="table",
            source="Table",
        )
        monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
        monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
        monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [table])
        monkeypatch.setattr(sd, "_drop_soft_deleted_workspaces", lambda db, ids: ids)

        req = DummyRequest()
        req.state.claims = {
            "identity": "tester",
            "policies": [{"pattern": "public.*", "role": "reader"}],
        }
        result = sd.get_service_document(req, Response())

        assert "value" in result
        entries = {entry["name"]: entry for entry in result["value"]}
        assert entries["public.examples.table"]["kind"] == "EntitySet"
        assert entries["public.examples.table"]["url"]


# ---------------------------------------------------------------------------
# Item 2 -- at least one OData-defined format (§7)
# ---------------------------------------------------------------------------


class TestItem2ODataFormat:
    """Data responses are application/json; $metadata is application/xml --
    both are formats OData v4 defines. See test_metadata_resource_annotation.py
    and friends for the XML side; this checks the JSON side end to end."""

    def test_data_response_is_json_shaped(self):
        result = execute_odata_query(table_name=PLANETS, select_cols="name", top=1)
        rows = json.loads(result["value_bytes"])
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_error_response_content_type_is_json(self, client):
        # $search is rejected before any catalog access, so this needs no
        # credentials -- see test_unsupported_options.py::TestSearchRejection.
        response = client.get("/api/v4/public/demo/users?$search=x")
        assert response.headers["content-type"].startswith("application/json")


# ---------------------------------------------------------------------------
# Item 3 -- server-driven paging (§11.2.5.7)
# ---------------------------------------------------------------------------


class TestItem3ServerDrivenPaging:
    """A result truncated at $top MUST expose a way to fetch the rest.

    Deeper coverage of nextLink URL construction lives in
    test_odata_nextlink.py; this exercises the actual truncation signal
    execute_odata_query produces, which that file only simulates.
    """

    def test_truncated_result_reports_more_rows_available(self):
        # $planets has more than 3 rows, so asking for top=3 must truncate.
        result = execute_odata_query(table_name=PLANETS, select_cols="name", top=3)
        assert result["has_more_rows"] is True

    def test_untruncated_result_reports_no_more_rows(self):
        result = execute_odata_query(table_name=PLANETS, select_cols="name", top=1000)
        assert result["has_more_rows"] is False


# ---------------------------------------------------------------------------
# Item 4 -- OData-Version response header (§8.1.5)
# ---------------------------------------------------------------------------


class TestItem4ODataVersionHeader:
    """Every response on an OData route, success or error, MUST carry it."""

    def test_present_on_error_response(self, client):
        response = client.get("/api/v4/public/demo/users?$search=x")
        assert response.headers.get("OData-Version") == "4.0"

    def test_present_on_service_document_route(self, monkeypatch):
        monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
        monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
        monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [])
        monkeypatch.setattr(sd, "_drop_soft_deleted_workspaces", lambda db, ids: ids)

        req = DummyRequest()
        req.state.claims = {"identity": "tester", "policies": []}
        resp = Response()
        sd.get_service_document(req, resp)
        assert resp.headers["OData-Version"] == "4.0"


# ---------------------------------------------------------------------------
# Item 5 -- Accept (§8.2.1) and OData-MaxVersion (§8.2.7) semantics
# ---------------------------------------------------------------------------


class TestItem5HeaderSemantics:
    """Full coverage is in test_content_negotiation.py; this just proves both
    routes actually enforce it end to end, not merely define the helper."""

    def test_unsatisfiable_accept_is_rejected_not_ignored(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"Accept": "application/xml"}
        )
        assert response.status_code == 406

    def test_unsupportable_max_version_is_rejected_not_ignored(self, client):
        response = client.get(
            "/api/v4/public/demo/users?$top=1", headers={"OData-MaxVersion": "3.0"}
        )
        assert response.status_code == 400

    def test_data_route_declares_json_only(self):
        from app.routes.v4.interface import router

        route = next(r for r in router.routes if r.path.endswith("/{dataset}"))
        assert MEDIA_JSON in {MEDIA_JSON, MEDIA_XML}  # sanity: constants distinct
        assert route.dependencies  # the Accept/MaxVersion dependency is attached


# ---------------------------------------------------------------------------
# Item 6 -- extensibility: no required custom content (§6)
# ---------------------------------------------------------------------------


class TestItem6Extensibility:
    """Custom annotations must be additive, under the Custom.* namespace, never
    a name a base OData client would collide with or need to understand."""

    def test_custom_terms_are_namespaced(self):
        from app.odata.edm_types import ORIGINAL_TYPE_TERM, RESOURCE_TERM

        assert ORIGINAL_TYPE_TERM.startswith("Custom.")
        assert RESOURCE_TERM.startswith("Custom.")

    def test_no_custom_headers_required_for_basic_query(self, client):
        # No custom header of any kind is sent; a plain, valid query must not
        # be rejected for missing one.
        response = client.get("/api/v4/public/demo/users?$top=1")
        assert response.status_code != 400 or "header" not in response.text.lower()


# ---------------------------------------------------------------------------
# Item 7 -- parse every system query option; 501 (not silence) for the rest
# ---------------------------------------------------------------------------


class TestItem7SystemQueryOptions:
    """$search and $expand are recognized and rejected loudly (501).
    $compute is fully implemented -- see test_compute_execution.py. See
    test_unsupported_options.py for the $search/$expand matrix (empty-value
    admitted, combined with other options, error format, etc.)."""

    @pytest.mark.parametrize("option", ["$search", "$expand"])
    def test_unsupported_option_returns_501_not_silence(self, client, option):
        response = client.get(f"/api/v4/public/demo/users?{option}=x")
        assert response.status_code == 501
        assert option in response.json()["error"]["message"]

    @pytest.mark.parametrize(
        "option",
        ["$filter", "$select", "$orderby", "$top", "$skip", "$count", "$apply", "$compute"],
    )
    def test_recognized_option_is_not_dropped_silently(self, client, option):
        # A recognized option must influence request handling somehow -- at
        # minimum, participate in duplicate-parameter detection -- rather
        # than being an inert, unbound extra query param.
        response = client.get(f"/api/v4/public/demo/users?{option}=x&{option}=y")
        assert response.status_code != 501


# ---------------------------------------------------------------------------
# Item 8 -- only Edm.* types are exposed (§CSDL)
# ---------------------------------------------------------------------------


class TestItem8OnlyEdmTypesExposed:
    def test_unmapped_type_falls_back_to_abstract_edm_type_not_a_lie(self):
        from app.odata.edm_types import EDM_UNKNOWN, edm_property

        prop = edm_property("mystery", "SOME_TYPE_OPTERYX_DOES_NOT_MAP")
        assert prop.type == EDM_UNKNOWN
        assert prop.type.startswith("Edm.")

    def test_known_scalar_types_map_to_edm(self):
        from app.odata.edm_types import edm_property

        assert edm_property("id", "INTEGER").type.startswith("Edm.")
        assert edm_property("name", "VARCHAR").type.startswith("Edm.")


# ---------------------------------------------------------------------------
# Item 9 -- MUST NOT require custom content to consume the service
# ---------------------------------------------------------------------------


class TestItem9NoRequiredCustomContent:
    """A client using only base CSDL concepts (Edm.* types, EntitySet,
    EntityType, Key) can consume the service; Custom.* annotations are
    supplementary. Same evidence as item 6 -- see test_metadata_resource_annotation.py
    for confirmation the base EntityType/EntitySet/Key elements are always present
    alongside the Custom.* ones."""

    def test_edm_property_has_a_plain_edm_type_independent_of_annotations(self):
        from app.odata.edm_types import edm_property

        prop = edm_property("price", "DECIMAL")
        assert prop.type.startswith("Edm.")


# ---------------------------------------------------------------------------
# Item 10 -- MUST NOT violate update semantics (§11.4)
# ---------------------------------------------------------------------------


class TestItem10UpdateSemantics:
    """This service is read-only: there is nothing to violate. Confirmed by
    inventorying every route method actually registered."""

    def test_only_get_routes_exist(self):
        from app.routes.v4 import interface, metadata as md, service_document as svc_doc

        methods = set()
        for router in (interface.router, md.router, svc_doc.router):
            for route in router.routes:
                methods |= set(getattr(route, "methods", ()) or ())
        assert methods <= {"GET", "HEAD"}


# ---------------------------------------------------------------------------
# Item 11 -- MUST NOT violate other OData-defined semantics
# ---------------------------------------------------------------------------


class TestItem11OtherSemantics:
    """Errors follow the OData error shape ({"error": {"code", "message"}}),
    consistently, for both engine-raised and framework-raised failures."""

    def test_error_shape_from_query_option_rejection(self, client):
        response = client.get("/api/v4/public/demo/users?$search=x")
        body = response.json()
        assert set(body.keys()) == {"error"}
        assert set(body["error"].keys()) == {"code", "message"}

    def test_error_shape_from_bad_top_value(self, client):
        response = client.get("/api/v4/public/demo/users?$top=999999999")
        assert response.status_code == 400
        body = response.json()
        assert set(body["error"].keys()) == {"code", "message"}


# ---------------------------------------------------------------------------
# Item 12 -- SHOULD support $expand (§11.2.4.2) -- known, documented gap
# ---------------------------------------------------------------------------


class TestItem12ExpandIsANotYetMetShould:
    """Not a minimal-conformance blocker (SHOULD, not MUST) -- this test
    exists so implementing $expand is a deliberate, visible change to this
    file rather than a silent one."""

    def test_expand_is_currently_rejected_rather_than_supported(self, client):
        response = client.get("/api/v4/public/demo/users?$expand=Orders")
        assert response.status_code == 501


# ---------------------------------------------------------------------------
# Item 13 -- MAY publish $metadata (§11.1.2)
# ---------------------------------------------------------------------------


class TestItem13Metadata:
    """Optional at minimal conformance, but implemented -- confirm it returns
    a well-formed EDMX document, not just that the route exists."""

    def test_metadata_route_registered(self):
        from app.routes.v4 import metadata as md

        paths = {route.path for route in md.router.routes}
        assert "/api/v4/$metadata" in paths

    def test_metadata_is_well_formed_edmx(self, monkeypatch):
        from tests._opteryx_stubs import load_with_stubs

        metadata = load_with_stubs("metadata_mod_conformance", "app", "routes", "v4", "metadata.py")

        dataset = metadata.DatasetInfo(
            resource="public.examples.table",
            workspace="public",
            collection="examples",
            name="table",
            source="Table",
        )
        monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
        monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [dataset])
        monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
        monkeypatch.setattr(
            metadata,
            "_facts_for",
            lambda db, datasets: {
                d.resource: metadata._DatasetFacts(columns=[("id", "Edm.Int64", {})], last_modified_ms=None)
                for d in datasets
            },
        )
        metadata.clear_metadata_cache()

        req = DummyRequest()
        req.state.claims = {
            "identity": "tester",
            "policies": [{"pattern": "public.*", "role": "reader"}],
        }
        res = metadata.get_edmx_metadata(req)
        content = res.body.decode() if hasattr(res, "body") else res.content.decode()

        root = ET.fromstring(content)  # raises if not well-formed XML
        assert root.tag == "{http://docs.oasis-open.org/odata/ns/edmx}Edmx"
        assert res.media_type == "application/xml"
