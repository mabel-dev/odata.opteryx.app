"""Tests for the unified EDM type mapper.

The expectations here are pinned to what the service actually puts on the wire.
Rows are rendered natively (``_execute_and_serialize_rows`` -> rugo
``write_jsonl`` -> ``rugo/src/_text_render.hpp``), so the per-type emitter in
that file -- not the Python sanitizer -- is what each assertion below encodes.

Verifies:
1. Correct type mapping for every type opteryx can hand us
2. CSDL facets, which are load-bearing (a Decimal without Scale means Scale=0)
3. Cross-endpoint consistency (both $metadata endpoints use the same mapper)
4. Unrecognised and unrepresentable types report as unknown, never as string
"""

from app.odata.edm_types import EDM_UNKNOWN
from app.odata.edm_types import column_type_to_edm
from app.odata.edm_types import edm_type_facets
from app.odata.edm_types import is_lossy_mapping


class TestIntegerTypes:
    def test_signed_widths_map_to_matching_edm_types(self):
        # Edm.SByte is the signed 8-bit type; Edm.Byte is unsigned.
        assert column_type_to_edm("INT8") == "Edm.SByte"
        assert column_type_to_edm("INT16") == "Edm.Int16"
        assert column_type_to_edm("INT32") == "Edm.Int32"
        assert column_type_to_edm("INT64") == "Edm.Int64"

    def test_unsigned_widths_promote_to_a_type_that_holds_the_range(self):
        assert column_type_to_edm("UINT8") == "Edm.Byte"  # 0..255, exact
        assert column_type_to_edm("UINT16") == "Edm.Int32"  # 0..65535
        assert column_type_to_edm("UINT32") == "Edm.Int64"  # 0..4294967295

    def test_uint64_uses_decimal_because_int64_cannot_hold_it(self):
        # 2**64-1 overflows Edm.Int64; Edm.Decimal(20, 0) is the only OData
        # primitive that represents it exactly, and it is still a JSON number.
        assert column_type_to_edm("UINT64") == "Edm.Decimal"
        assert edm_type_facets("UINT64") == {"Precision": "20", "Scale": "0"}

    def test_legacy_integer_spellings(self):
        # orso-era names; opteryx keeps the same aliases, and schemas persisted
        # back then still say these.
        assert column_type_to_edm("TINYINT") == "Edm.SByte"
        assert column_type_to_edm("SMALLINT") == "Edm.Int16"
        for type_str in ("INTEGER", "INT", "BIGINT"):
            assert column_type_to_edm(type_str) == "Edm.Int64", f"Failed for {type_str}"


class TestFloatAndDecimalTypes:
    def test_float_widths(self):
        # rugo's ej_float renders FLOAT32 from the un-promoted value.
        assert column_type_to_edm("FLOAT32") == "Edm.Single"
        assert column_type_to_edm("FLOAT64") == "Edm.Double"

    def test_legacy_float_spellings(self):
        assert column_type_to_edm("REAL") == "Edm.Single"
        assert column_type_to_edm("DOUBLE") == "Edm.Double"
        assert column_type_to_edm("FLOAT") == "Edm.Double"  # opteryx aliases to FLOAT64

    def test_decimal_is_decimal_not_double(self):
        # ej_decimal writes the exact scaled value, so there is no reason to
        # declare a lossy Edm.Double.
        for type_str in ("DECIMAL", "DECIMAL128", "DECIMAL(15, 2)", "NUMERIC"):
            assert column_type_to_edm(type_str) == "Edm.Decimal", f"Failed for {type_str}"

    def test_decimal_facets_come_from_the_type_string(self):
        assert edm_type_facets("DECIMAL(15, 2)") == {"Precision": "15", "Scale": "2"}
        assert edm_type_facets("DECIMAL(38, 9)") == {"Precision": "38", "Scale": "9"}

    def test_decimal_facets_default_to_opteryx_defaults(self):
        # CSDL defaults Scale to 0, which would declare an integer -- never
        # leave a Decimal property without facets.
        assert edm_type_facets("DECIMAL") == {"Precision": "38", "Scale": "9"}


class TestTemporalTypes:
    def test_each_temporal_gets_its_own_edm_type(self):
        # rugo has three distinct renderings: "YYYY-MM-DD" (ej_date), RFC 3339
        # with a +00:00 offset (ej_timestamp), and "HH:MM:SS[.ffffff]" (ej_time).
        assert column_type_to_edm("DATE") == "Edm.Date"
        assert column_type_to_edm("TIMESTAMP") == "Edm.DateTimeOffset"
        assert column_type_to_edm("TIME") == "Edm.TimeOfDay"

    def test_physical_temporal_spellings(self):
        assert column_type_to_edm("DATE32") == "Edm.Date"
        assert column_type_to_edm("TIMESTAMP64") == "Edm.DateTimeOffset"
        assert column_type_to_edm("TIME32") == "Edm.TimeOfDay"
        assert column_type_to_edm("TIME64") == "Edm.TimeOfDay"

    def test_unit_suffix_does_not_stop_the_base_type_resolving(self):
        # str(ColumnType) writes TIMESTAMP[us] / TIME[us].
        assert column_type_to_edm("TIMESTAMP[us]") == "Edm.DateTimeOffset"
        assert column_type_to_edm("TIME[ns]") == "Edm.TimeOfDay"

    def test_unit_becomes_the_precision_facet(self):
        # CSDL defaults temporal Precision to 0 (whole seconds), but rugo writes
        # fractional seconds -- the facet has to say so.
        assert edm_type_facets("TIMESTAMP[s]") == {"Precision": "0"}
        assert edm_type_facets("TIMESTAMP[ms]") == {"Precision": "3"}
        assert edm_type_facets("TIMESTAMP[us]") == {"Precision": "6"}
        assert edm_type_facets("TIME[ns]") == {"Precision": "9"}

    def test_bare_temporal_defaults_to_microseconds(self):
        assert edm_type_facets("TIMESTAMP") == {"Precision": "6"}

    def test_date_carries_no_precision_facet(self):
        assert edm_type_facets("DATE") == {}


class TestTextAndBinaryTypes:
    def test_string_types(self):
        for type_str in ("VARCHAR", "NVARCHAR", "STRING", "TEXT", "CHAR"):
            assert column_type_to_edm(type_str) == "Edm.String", f"Failed for {type_str}"

    def test_variant_is_text_on_the_wire(self):
        # VARIANT means "type not narrowed", but DrakenType.VARIANT is rendered
        # by ej_string -- the payload really is text.
        assert column_type_to_edm("VARIANT") == "Edm.String"

    def test_ipv4_is_a_dotted_decimal_string(self):
        # Physically UINT32, but the logical descriptor makes rugo pick ej_ipv4,
        # which writes a quoted address -- so it is not an integer to a client.
        assert column_type_to_edm("IPV4") == "Edm.String"

    def test_varbinary_is_string_not_binary(self):
        # rugo routes VARBINARY through ej_string, the same emitter VARCHAR
        # uses. Declaring Edm.Binary would tell clients to base64-decode a
        # payload that was never base64-encoded.
        for type_str in ("VARBINARY", "BINARY", "BLOB", "BYTES"):
            assert column_type_to_edm(type_str) == "Edm.String", f"Failed for {type_str}"

    def test_struct_and_json_types_are_text(self):
        # STRUCT and JSONB alias to NVARCHAR in opteryx.
        for type_str in ("STRUCT", "JSON", "JSONB"):
            assert column_type_to_edm(type_str) == "Edm.String", f"Failed for {type_str}"


class TestCollectionTypes:
    def test_array_element_uses_the_same_rules_as_scalars(self):
        assert column_type_to_edm("ARRAY<INT32>") == "Collection(Edm.Int32)"
        assert column_type_to_edm("ARRAY<BOOL>") == "Collection(Edm.Boolean)"
        assert column_type_to_edm("ARRAY<VARCHAR>") == "Collection(Edm.String)"
        assert column_type_to_edm("ARRAY<TIMESTAMP>") == "Collection(Edm.DateTimeOffset)"
        assert column_type_to_edm("ARRAY<DECIMAL(15,2)>") == "Collection(Edm.Decimal)"

    def test_array_paren_notation(self):
        assert column_type_to_edm("ARRAY(VARCHAR)") == "Collection(Edm.String)"

    def test_array_of_variant_is_the_catalog_default(self):
        # The catalog writes ARRAY<VARIANT> when the element type is unknown.
        assert column_type_to_edm("ARRAY<VARIANT>") == "Collection(Edm.String)"

    def test_array_with_unknown_or_absent_element(self):
        assert column_type_to_edm("ARRAY<CUSTOM>") == f"Collection({EDM_UNKNOWN})"
        assert column_type_to_edm("ARRAY") == f"Collection({EDM_UNKNOWN})"

    def test_array_element_type_argument(self):
        """SchemaColumn.element_type is used when the type string has no element."""
        assert column_type_to_edm("ARRAY", "BOOL") == "Collection(Edm.Boolean)"
        assert column_type_to_edm("ARRAY", "INT32") == "Collection(Edm.Int32)"

    def test_vector_is_a_collection_of_doubles(self):
        # ej_fp16 writes a JSON array of fp16-widened doubles.
        assert column_type_to_edm("VECTOR") == "Collection(Edm.Double)"
        assert column_type_to_edm("VECTOR(768)") == "Collection(Edm.Double)"


class TestUnknownAndUnrepresentableTypes:
    def test_unknown_type_is_a_valid_csdl_40_property_type(self):
        # Edm.PrimitiveType is the built-in abstract primitive type (CSDL v4.0
        # §4.4). Edm.Untyped would be more expressive but is 4.01-only.
        assert EDM_UNKNOWN == "Edm.PrimitiveType"

    def test_unrecognised_types_are_unknown_not_string(self):
        assert column_type_to_edm("CUSTOM_TYPE") == EDM_UNKNOWN
        assert column_type_to_edm("UNKNOWN") == EDM_UNKNOWN
        assert column_type_to_edm("") == EDM_UNKNOWN

    def test_all_null_column_is_unknown(self):
        # OData v4 has no Edm.Null: an all-null column is a nullable property
        # whose type was never narrowed.
        assert column_type_to_edm("NULL") == EDM_UNKNOWN

    def test_interval_has_no_wire_representation(self):
        # INTERVAL has no case in rugo's resolve_col, so it falls through to
        # ej_null and every value renders as JSON null. Edm.Duration would
        # promise an ISO-8601 string that never arrives.
        assert column_type_to_edm("INTERVAL") == EDM_UNKNOWN

    def test_none_type(self):
        assert column_type_to_edm(None) == EDM_UNKNOWN

    def test_unknown_python_type(self):
        class Custom:
            pass

        assert column_type_to_edm(Custom) == EDM_UNKNOWN


class TestPythonTypeLegacyPath:
    def test_python_scalars(self):
        assert column_type_to_edm(bool) == "Edm.Boolean"  # before int: bool subclasses int
        assert column_type_to_edm(int) == "Edm.Int64"
        assert column_type_to_edm(float) == "Edm.Double"
        assert column_type_to_edm(str) == "Edm.String"
        assert column_type_to_edm(bytes) == "Edm.Binary"

    def test_python_decimal_and_uuid(self):
        import decimal
        import uuid

        assert column_type_to_edm(decimal.Decimal) == "Edm.Decimal"
        assert column_type_to_edm(uuid.UUID) == "Edm.Guid"

    def test_python_temporals(self):
        import datetime

        assert column_type_to_edm(datetime.datetime) == "Edm.DateTimeOffset"
        assert column_type_to_edm(datetime.date) == "Edm.Date"
        assert column_type_to_edm(datetime.time) == "Edm.TimeOfDay"


class TestParsing:
    def test_case_insensitivity(self):
        assert column_type_to_edm("int64") == "Edm.Int64"
        assert column_type_to_edm("Int64") == "Edm.Int64"
        assert column_type_to_edm("varchar") == "Edm.String"

    def test_whitespace_handling(self):
        assert column_type_to_edm("  INT64  ") == "Edm.Int64"
        assert column_type_to_edm("ARRAY  <  VARCHAR  >") == "Collection(Edm.String)"

    def test_type_like_object_with_name_attribute(self):
        class FakeType:
            def __init__(self, name):
                self.name = name

        assert column_type_to_edm(FakeType("INT64")) == "Edm.Int64"
        assert column_type_to_edm(FakeType("VARCHAR")) == "Edm.String"

    def test_compound_spelling(self):
        assert column_type_to_edm("DOUBLE PRECISION") == "Edm.Double"


class TestLossyMappingAnnotation:
    """`Custom.OriginalType` is emitted exactly when the EDM type loses detail."""

    def test_exactly_described_types_need_no_annotation(self):
        for type_str in ("INT64", "INT32", "FLOAT64", "BOOL", "VARCHAR", "DECIMAL(15, 2)"):
            assert not is_lossy_mapping(type_str), f"{type_str} should not be annotated"

    def test_widened_and_reinterpreted_types_are_annotated(self):
        # A client seeing Edm.String cannot tell IPV4 from VARBINARY from JSONB;
        # one seeing Edm.Int64 cannot tell UINT32 from INT64.
        for type_str in ("UINT32", "UINT64", "IPV4", "VARBINARY", "JSONB", "VARIANT",
                         "INTERVAL", "NULL", "VECTOR(768)", "ARRAY<VARCHAR>"):
            assert is_lossy_mapping(type_str), f"{type_str} should be annotated"


class TestConsistencyAcrossEndpoints:
    """Both $metadata endpoints must declare identical types for identical columns."""

    def test_every_result_is_a_valid_edm_type_reference(self):
        test_types = [
            "INT8", "UINT64", "FLOAT32", "DECIMAL(15, 2)", "DATE", "TIMESTAMP[us]",
            "TIME[us]", "IPV4", "VARIANT", "VARBINARY", "NULL", "INTERVAL",
            "ARRAY<VARCHAR>", "VECTOR(768)", "STRUCT<a INT, b VARCHAR>",
        ]
        for type_str in test_types:
            edm_type = column_type_to_edm(type_str)
            assert isinstance(edm_type, str), f"{type_str} returned non-string: {edm_type}"
            assert (
                edm_type.startswith("Edm.") or edm_type.startswith("Collection(Edm.")
            ), f"{type_str} returned invalid EDM type: {edm_type}"

    def test_facets_are_strings_so_they_can_be_xml_attributes(self):
        for type_str in ("DECIMAL(15, 2)", "UINT64", "TIMESTAMP[us]", "TIME[ms]"):
            facets = edm_type_facets(type_str)
            assert facets, f"{type_str} should carry facets"
            for key, value in facets.items():
                assert isinstance(key, str) and isinstance(value, str), (
                    f"{type_str} facet {key}={value!r} is not a string"
                )
