"""Materialized views surface as their own source type: "MaterializedView".

A materialized view is a *dataset* (it lives in the `datasets` subcollection and
is physically a table) that the catalog tags with `dataset-type:
"materialized_view"`. It therefore has to report a distinct label while still
taking the Table code path everywhere else -- snapshots, sort orders, a real
schema. Both halves of that are asserted here.
"""

from fastapi import Response

from app.routes.v4 import interface
from app.routes.v4 import service_document as sd
from tests._opteryx_stubs import load_with_stubs

# Load against the stubs so the heavy engine/catalog imports are skipped; the
# stubs are torn down immediately so later tests still get the real opteryx.
metadata = load_with_stubs("metadata_mv_mod", "app", "routes", "v4", "metadata.py")

MV_TYPE = "materialized_view"
MV_SOURCE = "MaterializedView"


class DummyState:
    pass


class DummyRequest:
    state = DummyState()
    # Real requests always carry headers; the endpoint reads If-None-Match.
    headers: dict = {}


# ---------------------------------------------------------------------------
# Minimal Firestore stand-ins for the listing helpers
# ---------------------------------------------------------------------------


class FakeParentDoc:
    def __init__(self, doc_id):
        self.id = doc_id


class FakeSubcollection:
    """The `datasets`/`views` subcollection a document lives in."""

    def __init__(self, parent_id):
        self.parent = FakeParentDoc(parent_id)


class FakeDocRef:
    def __init__(self, parent_id, path):
        self.parent = FakeSubcollection(parent_id)
        self.path = path


class FakeDoc:
    def __init__(self, doc_id, data, parent_id="examples", path=""):
        self.id = doc_id
        self._data = data
        self.reference = FakeDocRef(parent_id, path or f"public/examples/datasets/{doc_id}")

    def to_dict(self):
        return self._data


class FakeQuery:
    def __init__(self, docs):
        self._docs = docs

    def where(self, **kwargs):
        return self

    def limit(self, n):
        return self

    def stream(self):
        return iter(self._docs)


class FakeCollectionGroupDB:
    def __init__(self, docs):
        self._docs = docs

    def collection_group(self, name):
        return FakeQuery(self._docs)

    def get_all(self, refs):  # pragma: no cover - no pending sort keys in these tests
        return []


class _PersonalIdentity:
    def __init__(self, docs_by_child):
        self._docs_by_child = docs_by_child

    def collection(self, child):
        return FakeQuery(self._docs_by_child.get(child, []))


class PersonalDB:
    """db.collection("personal").document(id).collection(child).stream()"""

    def __init__(self, docs_by_child):
        self._identity = _PersonalIdentity(docs_by_child)

    def collection(self, name):
        assert name == "personal"
        return self

    def document(self, identity):
        return self._identity

    def get_all(self, refs):  # pragma: no cover
        return []


def _plain_doc(name):
    return FakeDoc(name, {"workspace": "public", "collection": "examples", "name": name})


def _mv_doc(name):
    return FakeDoc(
        name,
        {
            "workspace": "public",
            "collection": "examples",
            "name": name,
            "dataset-type": MV_TYPE,
        },
    )


# ---------------------------------------------------------------------------
# Service document
# ---------------------------------------------------------------------------


def test_service_document_workspace_path_labels_materialized_view():
    db = FakeCollectionGroupDB([_plain_doc("plain"), _mv_doc("mv")])

    results = sd._query_collection_group(db, "datasets", workspace_filter="public")

    sources = {d.name: d.source for d in results}
    assert sources == {"plain": "Table", "mv": MV_SOURCE}


def test_service_document_personal_path_labels_materialized_view():
    db = PersonalDB({"datasets": [_plain_doc("plain"), _mv_doc("mv")], "views": []})

    results = sd._get_personal_datasets(db, "tester")

    sources = {d.name: d.source for d in results}
    assert sources == {"plain": "Table", "mv": MV_SOURCE}


def test_service_document_views_are_still_views():
    """A doc in the `views` subcollection stays a View even if oddly tagged."""
    db = FakeCollectionGroupDB([_mv_doc("v")])

    results = sd._query_collection_group(db, "views", workspace_filter="public")

    assert [d.source for d in results] == ["View"]


def test_service_document_reports_materialized_view_source(monkeypatch):
    mv = sd.DatasetInfo(
        resource="public.examples.mv",
        workspace="public",
        collection="examples",
        name="mv",
        source=MV_SOURCE,
    )

    monkeypatch.setattr(sd, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(sd, "_get_personal_datasets", lambda db, identity: [])
    monkeypatch.setattr(sd, "_get_workspace_datasets", lambda db, ids: [mv])
    monkeypatch.setattr(sd, "_drop_soft_deleted_workspaces", lambda db, ids: ids)

    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    result = sd.get_service_document(req, Response())

    entries = {entry["name"]: entry for entry in result["value"]}
    assert entries["public.examples.mv"]["source"] == MV_SOURCE


# ---------------------------------------------------------------------------
# Service-wide $metadata
# ---------------------------------------------------------------------------


def test_service_metadata_workspace_path_labels_materialized_view():
    db = FakeCollectionGroupDB([_plain_doc("plain"), _mv_doc("mv")])

    results = metadata._query_collection_group(db, "datasets", workspace_filter="public")

    sources = {d.name: d.source for d in results}
    assert sources == {"plain": "Table", "mv": MV_SOURCE}


def test_service_metadata_personal_path_labels_materialized_view():
    db = PersonalDB({"datasets": [_plain_doc("plain"), _mv_doc("mv")]})

    results = metadata._get_personal_datasets(db, "tester")

    sources = {d.name: d.source for d in results}
    assert sources == {"plain": "Table", "mv": MV_SOURCE}


def test_service_metadata_emits_materialized_view_source_annotation(monkeypatch):
    ds = metadata.DatasetInfo(
        resource="public.ns.mv",
        workspace="public",
        collection="ns",
        name="mv",
        source=MV_SOURCE,
    )

    monkeypatch.setattr(metadata, "_get_firestore_client", lambda **kwargs: object())
    monkeypatch.setattr(metadata, "_get_personal_datasets", lambda db, identity: [ds])
    monkeypatch.setattr(metadata, "_get_workspace_datasets", lambda db, ids: [])
    # Stand in for the batched schema read with a minimal column, so an
    # EntityType is created and the annotations below have somewhere to hang.
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

    req = DummyRequest()
    req.state.claims = {
        "identity": "tester",
        "policies": [{"pattern": "public.*", "role": "reader"}],
    }

    res = metadata.get_edmx_metadata(req)
    content = res.body.decode() if hasattr(res, "body") else res.content.decode()

    assert 'Term="Custom.SourceType" String="MaterializedView"' in content


# ---------------------------------------------------------------------------
# Per-dataset $metadata: label changes, behaviour stays Table-shaped
# ---------------------------------------------------------------------------


class FakeMVMetadata:
    dataset_type = MV_TYPE
    description = "mv desc"
    describer = "Llama-xyz"
    sort_orders = ["id"]


class FakeMaterializedView:
    """Physically a table: schema, stats and a snapshot, plus a sort order."""

    def schema(self):
        return type(
            "S",
            (),
            {
                "columns": [
                    type("C", (), {"name": "id", "type": "INTEGER", "nullable": False, "id": 1})
                ]
            },
        )

    def describe(self):
        return {"id": {"min": 1, "max": 10}}

    def snapshot(self):
        return type(
            "Snap",
            (),
            {
                "timestamp_ms": 1700000000000,
                "snapshot_id": "snap-mv-1",
                "summary": {"total-records": 7, "total-data-size": 512},
                "commit_message": "refresh mv",
                "author": "refresher",
            },
        )()

    @property
    def metadata(self):
        return FakeMVMetadata()


class FakeMVCatalog:
    def __init__(self, *args, **kwargs):
        pass

    def load_dataset(self, path):
        return FakeMaterializedView()

    def load_view(self, path):  # pragma: no cover - must never be reached
        raise AssertionError("a materialized view must load as a dataset, not a view")

    def get_materialized_view(self, identifier):
        return {
            "sql": "SELECT id FROM public.examples.base",
            "source-tables": ["examples.base"],
            "last-refreshed-at-ms": 1700000000000,
            "last-refresh-status": "success",
        }


def _mv_metadata_document(monkeypatch, dataset="my_mv"):
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeMVCatalog)
    req = DummyRequest()
    req.state.claims = {"identity": "test-user"}
    resp = Response()
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset=dataset
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()
    return content, resp


def test_dataset_metadata_labels_materialized_view(monkeypatch):
    content, resp = _mv_metadata_document(monkeypatch, dataset="mv_label")

    assert 'Term="Custom.SourceType" String="MaterializedView"' in content
    assert resp.headers.get("X-Dataset-Source") == MV_SOURCE


def test_materialized_view_gets_table_style_treatment(monkeypatch):
    """The label changes; the behaviour must not degrade to the View path."""
    content, resp = _mv_metadata_document(monkeypatch, dataset="mv_table_path")

    # Snapshot annotations (Table path) -- a View never gets these.
    assert "Custom.Snapshot.Id" in content
    assert "snap-mv-1" in content
    assert "Custom.Snapshot.TotalRecords" in content
    assert "Custom.Snapshot.TotalDataSize" in content
    assert "Custom.Snapshot.CommitMessage" in content
    assert "Custom.Snapshot.Author" in content
    # Sort order annotations (Table path only).
    assert 'Term="Custom.SortOrder.Column" String="id"' in content
    assert "Custom.SortOrder.Direction" in content
    # Table-level metadata and snapshot-derived last-modified.
    assert "Custom.Description" in content
    assert "Custom.LLMDescribed" in content
    assert resp.headers.get("X-Dataset-LastModified") == str(1700000000000)
    assert resp.headers.get("X-Dataset-Snapshot-Id") == "snap-mv-1"
    assert "Custom.LastModified" in content
    # The real schema is used, not the View placeholder.
    assert 'Name="id" Type="Edm.Int64"' in content


def test_materialized_view_exposes_refresh_annotations(monkeypatch):
    content, _ = _mv_metadata_document(monkeypatch, dataset="mv_annotations")

    assert "Custom.Definition" in content
    assert "SELECT id FROM public.examples.base" in content
    assert 'Term="Custom.MaterializedView.SourceTables" String="examples.base"' in content
    assert "Custom.MaterializedView.LastRefreshed" in content
    assert 'Term="Custom.MaterializedView.LastRefreshStatus" String="success"' in content
    assert 'Term="Custom.MaterializedView.Materialized" Bool="true"' in content
    # A running view says so explicitly rather than by omission -- see
    # test_a_suspended_materialized_view_says_so.
    assert 'Term="Custom.MaterializedView.Suspended" Bool="false"' in content
    assert "Custom.MaterializedView.SuspendedAt" not in content
    assert "Custom.MaterializedView.SuspendedBy" not in content


class FakeSuspendedMVCatalog(FakeMVCatalog):
    def get_materialized_view(self, identifier):
        return {
            "sql": "SELECT id FROM public.examples.base",
            "source-tables": ["examples.base"],
            # The last real refresh SUCCEEDED and stays reported as such: a
            # suspension does not overwrite the outcome of the last refresh that
            # actually ran. Which is exactly why "paused" needs its own
            # annotation -- without one this view reads as entirely healthy.
            "last-refreshed-at-ms": 1700000000000,
            "last-refresh-status": "success",
            "suspended-at-ms": 1787262500000,
            "suspended-by": "justin",
        }


def test_a_suspended_materialized_view_says_so(monkeypatch):
    """ALTER MATERIALIZED VIEW ... SUSPEND stops refreshes without dropping the
    triggers, so "deliberately off" and "quietly broken" stop looking alike --
    but only if something reports it. Nothing did: no information_schema
    projection carries it (an MV lists there as a plain BASE TABLE), so this
    document is where a client learns it."""
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeSuspendedMVCatalog)
    req = DummyRequest()
    req.state.claims = {"identity": "test-user"}
    resp = Response()
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="mv_suspended"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    assert 'Term="Custom.MaterializedView.Suspended" Bool="true"' in content
    assert 'Term="Custom.MaterializedView.SuspendedAt" Int="1787262500000"' in content
    assert 'Term="Custom.MaterializedView.SuspendedBy" String="justin"' in content
    # Untouched, and still the truth about the last refresh that ran.
    assert 'Term="Custom.MaterializedView.LastRefreshStatus" String="success"' in content


class FakeUnrefreshedMaterializedView(FakeMaterializedView):
    """A registered but never-refreshed MV, as CREATE MATERIALIZED VIEW leaves it.

    Matches the shape observed in the live catalog (opteryx.tests_temp.
    mv_e2e_target): the dataset document exists and carries the SQL, sources and
    trigger, but the refresh that would produce a schema and a first snapshot has
    not run. describe() raises rather than returning None -- there is no manifest
    to describe.
    """

    def schema(self):
        return None

    def describe(self):
        raise ValueError("No manifest available for this dataset/snapshot")

    def snapshot(self):
        return None


class FakeUnrefreshedMVCatalog(FakeMVCatalog):
    def load_dataset(self, path):
        return FakeUnrefreshedMaterializedView()

    def get_materialized_view(self, identifier):
        return {
            "sql": "SELECT id FROM public.examples.base",
            "source-tables": ["examples.base"],
            "last-refreshed-at-ms": None,
            "last-refresh-status": None,
        }


def test_never_refreshed_materialized_view_still_has_metadata(monkeypatch):
    """A schema-less MV must not 404 -- that hid the details of every MV for the
    whole window between creation and its first refresh."""
    monkeypatch.setattr(interface, "OpteryxCatalog", FakeUnrefreshedMVCatalog)
    req = DummyRequest()
    req.state.claims = {"identity": "test-user"}
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="mv_unrefreshed"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    # It is still identifiably a materialized view, and still says what it is
    # derived from and how -- which is all a details page needs.
    assert 'Term="Custom.SourceType" String="MaterializedView"' in content
    assert "Custom.Definition" in content
    assert 'Term="Custom.MaterializedView.SourceTables" String="examples.base"' in content
    # The one fact that separates this from an MV really holding a single
    # nullable string column: the Property list is a placeholder, not a schema.
    assert 'Term="Custom.MaterializedView.Materialized" Bool="false"' in content
    # No refresh has happened, so neither refresh annotation is emitted.
    assert "Custom.MaterializedView.LastRefreshed" not in content
    assert "Custom.MaterializedView.LastRefreshStatus" not in content
    # Suspension is emitted unconditionally even here: a view that has never
    # refreshed is a different thing from one somebody paused, and both states
    # are reachable at once.
    assert 'Term="Custom.MaterializedView.Suspended" Bool="false"' in content


class FakePlainDataset(FakeMaterializedView):
    @property
    def metadata(self):
        return type(
            "M",
            (),
            {
                "dataset_type": None,
                "description": "plain",
                "describer": None,
                "sort_orders": None,
            },
        )()


def test_plain_dataset_still_reports_table(monkeypatch):
    class Catalog(FakeMVCatalog):
        def load_dataset(self, path):
            return FakePlainDataset()

    monkeypatch.setattr(interface, "OpteryxCatalog", Catalog)
    req = DummyRequest()
    req.state.claims = {"identity": "test-user"}
    resp = Response()
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="plain_ds"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    assert 'Term="Custom.SourceType" String="Table"' in content
    assert resp.headers.get("X-Dataset-Source") == "Table"
    assert "Custom.MaterializedView." not in content


class FakeOldWheelDataset(FakeMaterializedView):
    """A catalog wheel predating `dataset_type` on DatasetMetadata."""

    @property
    def metadata(self):
        return type("M", (), {"description": "old", "describer": None, "sort_orders": None})()


def test_old_catalog_wheel_without_dataset_type_reports_table(monkeypatch):
    class Catalog(FakeMVCatalog):
        def load_dataset(self, path):
            return FakeOldWheelDataset()

    monkeypatch.setattr(interface, "OpteryxCatalog", Catalog)
    req = DummyRequest()
    req.state.claims = {"identity": "test-user"}
    resp = Response()
    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="old_wheel_ds"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    assert 'Term="Custom.SourceType" String="Table"' in content
