"""Fixed schemas for virtual, non-catalog information_schema datasets.

These datasets aren't backed by a catalog entry -- there's nothing in Firestore/GCS to
look up -- so their schema is hardcoded here and shared by every endpoint that needs to
know about them (service document, service-wide $metadata, per-dataset $metadata).
"""

from app.odata.edm_types import EdmProperty, edm_property


def _col(name: str, source_type: str) -> EdmProperty:
    """A fixed virtual column, described exactly as a real catalog column of
    `source_type` would be."""
    return edm_property(name, source_type)


# name -> [EdmProperty, ...], in column order.
VIRTUAL_INFORMATION_SCHEMA_TABLES: dict[str, list[EdmProperty]] = {
    "tables": [
        _col("table_catalog", "VARCHAR"),
        _col("table_schema", "VARCHAR"),
        _col("table_name", "VARCHAR"),
        _col("table_type", "VARCHAR"),
        _col("table_sort_order", "VARCHAR"),
        _col("snapshot_id", "BIGINT"),
        _col("snapshot_sequence_id", "BIGINT"),
        _col("table_updated_at", "TIMESTAMP"),
        _col("table_file_count", "BIGINT"),
        _col("table_bytes", "BIGINT"),
        _col("table_record_count", "BIGINT"),
    ],
    "columns": [
        _col("table_catalog", "VARCHAR"),
        _col("table_schema", "VARCHAR"),
        _col("table_name", "VARCHAR"),
        _col("column_name", "VARCHAR"),
        _col("ordinal_position", "INTEGER"),
        _col("data_type", "VARCHAR"),
        _col("is_nullable", "VARCHAR"),
    ],
    "views": [
        _col("table_catalog", "VARCHAR"),
        _col("table_schema", "VARCHAR"),
        _col("table_name", "VARCHAR"),
        _col("view_definition", "VARCHAR"),
        _col("view_owner", "VARCHAR"),
        _col("view_updated_at", "TIMESTAMP"),
    ],
    "schemata": [
        _col("catalog_name", "VARCHAR"),
        _col("schema_name", "VARCHAR"),
    ],
}

# Default Custom.Description annotation per virtual dataset, used where the real
# catalog would otherwise supply a description/comment.
VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS: dict[str, str] = {
    "tables": "System view listing the tables and views visible in this workspace.",
    "columns": "System view listing the columns of the tables and views visible in this workspace.",
    "views": "System view listing the views visible in this workspace, including their SQL definition.",
    "schemata": "System view listing the schemas (collections) visible in this workspace.",
}


def is_virtual_information_schema(collection: str, name: str) -> bool:
    """Whether (collection, name) refers to a virtual information_schema dataset."""
    return collection == "information_schema" and name in VIRTUAL_INFORMATION_SCHEMA_TABLES
