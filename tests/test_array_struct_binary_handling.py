import base64
import json

from fastapi import Response

from tests._opteryx_stubs import load_with_stubs

# Load interface.py against lightweight fakes so this test needs no engine or
# catalog. load_with_stubs tears the fakes back out of sys.modules as soon as
# the module is loaded -- leaving them installed poisons every later test that
# imports the real opteryx ("cannot import name ... (unknown location)").
interface = load_with_stubs("interface_mod", "app", "routes", "v4", "interface.py")


class DummyState:
    # dataset_metadata rejects an unauthenticated caller with a 401 before it
    # reaches the type-mapping logic these tests are actually about.
    claims = {"identity": "test-user"}


class DummyRequest:
    state = DummyState()


class FakeDataset:
    def __init__(self, col_type_str, rows=None):
        self._schema = type(
            "S",
            (),
            {"columns": [type("C", (), {"name": "col", "type": col_type_str, "nullable": True})]},
        )
        self._rows = rows or [{"col": [1, 2]}, {"col": {"a": 1}}, {"col": b"bytes"}]

    def schema(self):
        return self._schema

    def describe(self):
        return {}

    def snapshot(self):
        return None


class FakeCatalog:
    def __init__(self, col_type_str, rows=None):
        self.col_type_str = col_type_str
        self.rows = rows

    def load_dataset(self, path):
        return FakeDataset(self.col_type_str, self.rows)

    def load_view(self, path):
        raise RuntimeError("not a view")


def get_metadata_and_rows(col_type_str):
    interface.OpteryxCatalog = lambda *args, **kwargs: FakeCatalog(col_type_str)
    req = DummyRequest()
    resp = Response()

    res = interface.dataset_metadata(
        req, resp, workstream="public", collection="examples", dataset="my_table"
    )
    content = res.content.decode() if hasattr(res, "content") else res.body.decode()

    # Return metadata content only; row serialization is tested via sanitizer
    return content, None


def test_array_emits_collection_type_and_array_rows():
    # Column type represented as object with __str__ returning 'ARRAY<INTEGER>'
    class T:
        name = "ARRAY"

        def __str__(self):
            return "ARRAY<INTEGER>"

    content, _ = get_metadata_and_rows(T())
    assert "Collection(Edm.Int64)" in content
    # Ensure sanitize returns arrays not string
    assert interface._sanitize_value_for_odata([1, 2]) == [1, 2]


def test_struct_jsonb_emits_binary_and_base64_rows():
    class T2:
        name = "JSONB"

        def __str__(self):
            return "JSONB"

    content, _ = get_metadata_and_rows(T2())
    # Should declare property as Edm.Binary
    assert "Edm.Binary" in content
    assert "Custom.OriginalType" in content

    # serialization: dict -> base64 string via sanitizer
    encoded = interface._sanitize_value_for_odata({"a": 1})
    assert isinstance(encoded, str)
    decoded = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    assert json.loads(decoded) == {"a": 1}


def test_blob_varbinary_mapped_to_binary():
    class TBLOB:
        name = "BLOB"

        def __str__(self):
            return "BLOB"

    content, _ = get_metadata_and_rows(TBLOB())
    assert "Edm.Binary" in content
    # bytes -> base64 string via sanitizer
    v = interface._sanitize_value_for_odata(b"hello")
    assert isinstance(v, str)
    assert base64.b64decode(v.encode("ascii")) == b"hello"
