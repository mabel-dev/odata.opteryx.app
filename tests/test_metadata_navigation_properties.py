"""Declared relationships as NavigationProperty in the service `$metadata`.

A NavigationProperty is a promise to a client that joining these two entity
sets returns rows. Only an asserted `maps` relationship carries that promise:
an unconfirmed proposal does not, and a `concept` edge -- two columns about the
same subject, whose values may not overlap at all -- actively does not.

The other half is visibility. An edge names the far end's collection, dataset
and column, so it is emitted only where the caller can see BOTH ends, and it is
built from the visible set rather than filtered afterwards.
"""

import xml.etree.ElementTree as ET

from tests._opteryx_stubs import load_with_stubs

metadata = load_with_stubs("metadata_mod", "app", "routes", "v4", "metadata.py")

NS_EDM = "http://docs.oasis-open.org/odata/ns/edm"


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    headers: dict = {}


def _dataset(resource):
    workspace, collection, name = resource.split(".", 2)
    return metadata.DatasetInfo(
        resource=resource,
        workspace=workspace,
        collection=collection,
        name=name,
        source="Table",
    )


def _relationship(**overrides):
    row = {
        "name": "tickets_customer_fk",
        "kind": "maps",
        "workspace": "public",
        "collection": "helpdesk",
        "dataset": "tickets",
        "column": "customer_ref",
        "references-workspace": "public",
        "references-collection": "crm",
        "references-dataset": "customers",
        "references-column": "id",
        "cardinality": "many_to_one",
        "origin": "asserted",
        "status": "active",
    }
    row.update(overrides)
    return row


def _render(monkeypatch, resources, relationships):
    datasets = [_dataset(resource) for resource in resources]
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: datasets)
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(metadata, "_get_relationships", lambda db, ids: relationships)
    monkeypatch.setattr(
        metadata,
        "_facts_for",
        lambda db, datasets: {
            dataset.resource: metadata._DatasetFacts(
                columns=[("id", "Edm.Int64", {}), ("customer_ref", "Edm.Int64", {})],
                last_modified_ms=None,
            )
            for dataset in datasets
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
    return content, ET.fromstring(content)


def _navigation(root):
    """{EntityType name: [(nav name, target type, property, referenced property)]}"""
    found = {}
    for entity_type in root.iter(f"{{{NS_EDM}}}EntityType"):
        for nav in entity_type.findall(f"{{{NS_EDM}}}NavigationProperty"):
            constraint = nav.find(f"{{{NS_EDM}}}ReferentialConstraint")
            found.setdefault(entity_type.get("Name"), []).append(
                (
                    nav.get("Name"),
                    nav.get("Type"),
                    constraint.get("Property") if constraint is not None else None,
                    constraint.get("ReferencedProperty") if constraint is not None else None,
                )
            )
    return found


_BOTH = ["public.helpdesk.tickets", "public.crm.customers"]


def test_an_asserted_maps_edge_becomes_a_navigation_property(monkeypatch):
    _, root = _render(monkeypatch, _BOTH, [_relationship()])

    assert _navigation(root) == {
        "public_helpdesk_tickets": [
            (
                "tickets_customer_fk",
                "OData.public_crm_customers",
                "customer_ref",
                "id",
            )
        ]
    }


def test_an_edge_is_dropped_when_the_far_end_is_not_visible(monkeypatch):
    """The row names the far end's collection, dataset and column -- showing it
    to someone who can read only the near side is the disclosure."""
    _, root = _render(monkeypatch, ["public.helpdesk.tickets"], [_relationship()])

    assert _navigation(root) == {}


def test_an_edge_is_dropped_when_the_near_end_is_not_visible(monkeypatch):
    _, root = _render(monkeypatch, ["public.crm.customers"], [_relationship()])

    assert _navigation(root) == {}


def test_only_asserted_active_maps_edges_are_emitted(monkeypatch):
    """Each of these would tell a client a join returns rows when it may not."""
    for override in (
        {"kind": "concept"},
        {"kind": "derives"},
        {"origin": "inferred"},
        {"status": "unverified"},
        {"status": "broken"},
    ):
        _, root = _render(monkeypatch, _BOTH, [_relationship(**override)])
        assert _navigation(root) == {}, override


def test_an_edge_pointing_at_a_type_with_no_schema_is_removed(monkeypatch):
    """A NavigationProperty whose Type names a type the document never emitted
    is not a missing link, it is an invalid document -- a strict client rejects
    the whole thing rather than the one element."""
    datasets = [_dataset(resource) for resource in _BOTH]
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: datasets)
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(metadata, "_get_relationships", lambda db, ids: [_relationship()])
    # The far dataset is visible but has no readable schema, so no EntityType.
    monkeypatch.setattr(
        metadata,
        "_facts_for",
        lambda db, datasets: {
            "public.helpdesk.tickets": metadata._DatasetFacts(
                columns=[("id", "Edm.Int64", {}), ("customer_ref", "Edm.Int64", {})],
                last_modified_ms=None,
            ),
            "public.crm.customers": metadata._DatasetFacts(columns=[], last_modified_ms=None),
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
    root = ET.fromstring(content)

    assert _navigation(root) == {}


def test_relationships_never_fail_the_document(monkeypatch):
    """A missing collection-group index, an older catalog, or an unreachable
    subcollection all mean 'no navigation properties', never a failed metadata
    request that is otherwise complete."""

    class _Boom:
        def collection_group(self, name):
            raise RuntimeError("no index")

    assert metadata._get_relationships(_Boom(), {"public"}) == []


# --- the projection is itself a queryable entity set -------------------------


def test_column_relationships_is_a_virtual_information_schema_table():
    """Registered in the one place that feeds the service document, the
    service-wide $metadata and the per-dataset $metadata alike, so it cannot be
    listed in one and missing from another."""
    from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS
    from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_TABLES
    from app.odata.virtual_datasets import is_virtual_information_schema

    assert is_virtual_information_schema("information_schema", "column_relationships")
    assert "column_relationships" in VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS

    columns = [column.name for column in VIRTUAL_INFORMATION_SCHEMA_TABLES["column_relationships"]]
    # The engine's projection is the authority on this shape, and this list is a
    # HAND-MAINTAINED MIRROR of it -- nothing compares the two repos, so this
    # test pins the copy rather than proving it right. Drift means a client is
    # told about columns a query will not return, which is why the list is
    # spelled out here instead of being asserted by length.
    assert columns == [
        "constraint_catalog",
        "constraint_collection",
        "constraint_name",
        "table_name",
        "column_name",
        "referenced_table_name",
        "referenced_column_name",
        "relationship_kind",
        "cardinality",
        "origin",
        "status",
        "confidence",
        "evidence",
        "asserted_by",
        "asserted_at",
        "verified_at",
    ]


def test_column_relationships_appears_as_an_entity_set(monkeypatch):
    """A client has to be able to SEE the table to query it -- the navigation
    properties above say two entity sets are related, this is what lets someone
    ask the catalog what else is."""
    content, root = _render(monkeypatch, ["public.ns.table"], [])

    assert 'EntitySet Name="public_information_schema_column_relationships"' in content

    names = {
        entity_type.get("Name")
        for entity_type in root.iter(f"{{{NS_EDM}}}EntityType")
    }
    assert "public_information_schema_column_relationships" in names


def test_a_missing_index_is_reported_as_an_error_not_swallowed_quietly(caplog):
    """The failure mode is 'the feature is silently absent', which is the worst
    kind: the document comes back looking complete, forever, and nothing in it
    says a whole class of content is missing."""
    import logging

    from google.api_core.exceptions import FailedPrecondition

    class _NoIndex:
        def collection_group(self, name):
            raise FailedPrecondition("The query requires a COLLECTION_GROUP_ASC index")

    with caplog.at_level(logging.ERROR):
        assert metadata._get_relationships(_NoIndex(), {"public"}) == []

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a missing index must be logged at ERROR, not swallowed"
    # getMessage(), not .message: the latter is unpopulated until a formatter
    # has run, so asserting on it passes for the wrong reason.
    assert any("collection-group index" in r.getMessage() for r in errors)
