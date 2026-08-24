"""Colliding resource names, and the annotation that carries the real name.

Underscoring the dots is lossy: "a.b.c_d" and "a.b_c.d" flatten to the same
CSDL identifier. Both datasets must still appear, and each must say which
dotted resource it stands for.
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


def _render(monkeypatch, resources):
    datasets = [_dataset(resource) for resource in resources]
    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: datasets)
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    monkeypatch.setattr(
        metadata,
        "_facts_for",
        lambda db, datasets: {
            dataset.resource: metadata._DatasetFacts(
                columns=[("id", "Edm.Int64", {})], last_modified_ms=None
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


def _annotations(root, tag, term, prefix="public.ns"):
    """Map each element's Name to the annotation value it carries for `term`.

    Restricted to the datasets a test supplied: every workspace also gets the
    virtual information_schema entries, which are noise here.
    """
    found = {}
    for element in root.iter(f"{{{NS_EDM}}}{tag}"):
        for annotation in element.findall(f"{{{NS_EDM}}}Annotation"):
            if annotation.get("Term") == term and annotation.get("String").startswith(prefix):
                found[element.get("Name")] = annotation.get("String")
    return found


def test_resource_annotation_carries_the_dotted_name(monkeypatch):
    _, root = _render(monkeypatch, ["public.ns.table"])

    for tag in ("EntityType", "EntitySet"):
        assert _annotations(root, tag, metadata.RESOURCE_TERM) == {
            "public_ns_table": "public.ns.table"
        }


def test_colliding_resources_both_appear_and_are_distinguishable(monkeypatch, caplog):
    resources = ["public.ns.a_b", "public.ns_a.b"]
    content, root = _render(monkeypatch, resources)

    sets = _annotations(root, "EntitySet", metadata.RESOURCE_TERM)
    types = _annotations(root, "EntityType", metadata.RESOURCE_TERM)

    # Neither dataset is dropped, and no two share an identifier.
    assert sorted(sets.values()) == sorted(resources)
    assert sorted(types.values()) == sorted(resources)
    assert len(sets) == 2

    # Every EntitySet still points at an EntityType that exists.
    type_names = {element.get("Name") for element in root.iter(f"{{{NS_EDM}}}EntityType")}
    for entity_set in root.iter(f"{{{NS_EDM}}}EntitySet"):
        assert entity_set.get("EntityType").removeprefix("OData.") in type_names

    # The collision is visible in the log, naming both sides.
    assert all(resource in caplog.text for resource in resources)

    # The suffixed identifiers still start from the flattened form.
    assert all(name.startswith("public_ns_a_b") for name in sets)
    assert "public_ns_a_b\"" not in content  # neither keeps the ambiguous name


def test_disambiguated_names_do_not_depend_on_the_other_datasets(monkeypatch):
    pair = ["public.ns.a_b", "public.ns_a.b"]
    _, both = _render(monkeypatch, pair)
    _, with_extra = _render(monkeypatch, [*pair, "public.ns.zzz"])

    by_resource = {v: k for k, v in _annotations(both, "EntitySet", metadata.RESOURCE_TERM).items()}
    later = {v: k for k, v in _annotations(with_extra, "EntitySet", metadata.RESOURCE_TERM).items()}

    assert all(by_resource[resource] == later[resource] for resource in pair)
