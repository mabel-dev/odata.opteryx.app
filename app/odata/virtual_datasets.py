"""Fixed schemas for virtual, non-catalog information_schema datasets.

These datasets aren't backed by a catalog entry -- there's nothing in Firestore/GCS to
look up -- so their schema is hardcoded here and shared by every endpoint that needs to
know about them (service document, service-wide $metadata, per-dataset $metadata).
"""

from app.odata.edm_types import column_type_to_edm

# name -> [(column_name, edm_type), ...], in column order.
VIRTUAL_INFORMATION_SCHEMA_TABLES: dict[str, list[tuple[str, str]]] = {
    "tables": [
        ("table_catalog", column_type_to_edm("VARCHAR")),
        ("table_schema", column_type_to_edm("VARCHAR")),
        ("table_name", column_type_to_edm("VARCHAR")),
        ("table_type", column_type_to_edm("VARCHAR")),
    ],
    "columns": [
        ("table_catalog", column_type_to_edm("VARCHAR")),
        ("table_schema", column_type_to_edm("VARCHAR")),
        ("table_name", column_type_to_edm("VARCHAR")),
        ("column_name", column_type_to_edm("VARCHAR")),
        ("ordinal_position", column_type_to_edm("INTEGER")),
        ("data_type", column_type_to_edm("VARCHAR")),
        ("is_nullable", column_type_to_edm("VARCHAR")),
    ],
}

# Default Custom.Description annotation per virtual dataset, used where the real
# catalog would otherwise supply a description/comment.
VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS: dict[str, str] = {
    "tables": "System view listing the tables and views visible in this workspace.",
    "columns": "System view listing the columns of the tables and views visible in this workspace.",
}


def is_virtual_information_schema(collection: str, name: str) -> bool:
    """Whether (collection, name) refers to a virtual information_schema dataset."""
    return collection == "information_schema" and name in VIRTUAL_INFORMATION_SCHEMA_TABLES
