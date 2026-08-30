"""Fixed schemas for datasets no catalog schema document describes.

These datasets aren't backed by a catalog schema -- there's nothing in Firestore/GCS to
look up -- so their schema is hardcoded here and shared by every endpoint that needs to
know about them (service document, service-wide $metadata, per-dataset $metadata).

Two kinds live here: the virtual information_schema datasets, which exist in every
workspace and are synthesized entirely at query time; and a task, which IS a catalog
object with a Firestore document of its own but holds no rows of its own -- it is
presented as the single-row table of its own definition (TASK_DEFINITION_COLUMNS).
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
    "triggers": [
        _col("trigger_catalog", "VARCHAR"),
        _col("trigger_collection", "VARCHAR"),
        _col("trigger_name", "VARCHAR"),
        _col("event_object_table", "VARCHAR"),
        _col("action_kind", "VARCHAR"),
        _col("target_view", "VARCHAR"),
        _col("created_by", "VARCHAR"),
        _col("created_at", "TIMESTAMP"),
        _col("last_fired_at", "TIMESTAMP"),
        _col("last_fired_status", "VARCHAR"),
    ],
    "column_relationships": [
        _col("constraint_catalog", "VARCHAR"),
        _col("constraint_collection", "VARCHAR"),
        _col("constraint_name", "VARCHAR"),
        _col("table_name", "VARCHAR"),
        _col("column_name", "VARCHAR"),
        _col("referenced_table_name", "VARCHAR"),
        _col("referenced_column_name", "VARCHAR"),
        _col("relationship_kind", "VARCHAR"),
        _col("cardinality", "VARCHAR"),
        _col("origin", "VARCHAR"),
        _col("status", "VARCHAR"),
        # Inferred rows only. This list is a hand-maintained mirror of the
        # engine's projection, in another repository, with nothing comparing
        # the two - so a column added there has to be added here or the entity
        # set silently under-reports what SELECT * returns.
        _col("confidence", "FLOAT64"),
        _col("evidence", "VARCHAR"),
        _col("asserted_by", "VARCHAR"),
        _col("asserted_at", "TIMESTAMP"),
        _col("verified_at", "TIMESTAMP"),
    ],
    "schemata": [
        _col("catalog_name", "VARCHAR"),
        _col("schema_name", "VARCHAR"),
    ],
}

# A task holds no rows, so unlike a table there is nothing to give it a schema
# from. It is not therefore unreadable: `workspace.collection.<object>` is one
# namespace, a task occupies a name in it exactly as a table does, and a name in
# that namespace has to answer to a read. What it answers with is the one row
# describing itself -- the same fields `OpteryxCatalog.get_task` returns, spelled
# the way information_schema.triggers spells its equivalents (`*_catalog`,
# `*_collection`, `created_at`, `last_fired_status`) so the two read alike.
#
# Column order is the document order of the definition: what it is, then what it
# runs, then who owns it, then how the last run went.
#
# There is deliberately NO `runs_as`. A task is a unit of work that anyone may
# trigger, and whether it runs follows the permissions of whoever triggered it.
# An act-as identity belongs to a TRIGGER -- the thing that fires work when a
# dataset is updated, where that work needs rights the updater may not have (an
# update to `sales` firing work against `inventory`). Reporting one on the task
# lends the task's authority to anyone who can reach it, which is the failure
# the task/trigger split exists to prevent. The catalog still stores `runs-as`
# on the task document; it is deliberately not read here.
TASK_DEFINITION_COLUMNS: list[EdmProperty] = [
    _col("task_catalog", "VARCHAR"),
    _col("task_collection", "VARCHAR"),
    _col("task_name", "VARCHAR"),
    _col("description", "VARCHAR"),
    # The task's current statement, resolved through `statement-id` from the
    # `statement` subcollection that versions it -- exactly as a view's and a
    # materialized view's definition is stored.
    _col("statement", "VARCHAR"),
    _col("statement_id", "VARCHAR"),
    _col("created_by", "VARCHAR"),
    _col("created_at", "TIMESTAMP"),
    # Who last changed the STATEMENT. Sourced from the statement document, not
    # the task document, for the reason get_task separates them.
    _col("last_updated_by", "VARCHAR"),
    _col("last_updated_at", "TIMESTAMP"),
    _col("suspended_at", "TIMESTAMP"),
    _col("suspended_by", "VARCHAR"),
    _col("last_fired_at", "TIMESTAMP"),
    _col("last_fired_status", "VARCHAR"),
    # The `current_version` the last SUCCESSFUL run consumed to -- a version
    # number, not a timestamp, and stamped only on success so that a gap behind
    # a failed run stays visible.
    _col("last_window_to", "BIGINT"),
]

TASK_DESCRIPTION = (
    "A task: a statement the platform runs on its own. A task holds no rows of its "
    "own, so it reads as the single row describing itself."
)


# Default Custom.Description annotation per virtual dataset, used where the real
# catalog would otherwise supply a description/comment.
VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS: dict[str, str] = {
    "tables": "System view listing the tables and views visible in this workspace.",
    "columns": "System view listing the columns of the tables and views visible in this workspace.",
    "views": "System view listing the views visible in this workspace, including their SQL definition.",
    "triggers": "System view listing the triggers defined on the tables visible in this workspace, including the view each one refreshes.",
    "column_relationships": (
        "System view listing the relationships declared between columns of the tables "
        "visible in this workspace. A relationship records that two columns hold "
        "corresponding values; nothing enforces it, and a write that breaks one "
        "succeeds. A row appears only where both of its tables are visible. "
        "Rows with origin='inferred' and status='unverified' are unconfirmed "
        "proposals carrying the evidence they were proposed on; they are never "
        "emitted as navigable relationships."
    ),
    "schemata": "System view listing the schemas (collections) visible in this workspace.",
}


def is_virtual_information_schema(collection: str, name: str) -> bool:
    """Whether (collection, name) refers to a virtual information_schema dataset."""
    return collection == "information_schema" and name in VIRTUAL_INFORMATION_SCHEMA_TABLES
