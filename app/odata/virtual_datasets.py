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
        # What the trigger HANGS OFF, as `collection.name`: the dataset whose
        # commits fire a commit trigger, or the task a schedule or signal
        # trigger fires.
        _col("trigger_holder", "VARCHAR"),
        # The dataset the fired run's window is bound from: the holder for a
        # commit trigger, the OVER table for a schedule or signal trigger, and
        # null when such a trigger has none.
        _col("event_object_table", "VARCHAR"),
        # "commit" | "schedule" | "signal". The three columns after it describe
        # a clock: null for a commit trigger, whose event is the commit itself.
        _col("event_kind", "VARCHAR"),
        _col("schedule", "VARCHAR"),
        _col("time_zone", "VARCHAR"),
        _col("next_due_at", "TIMESTAMP"),
        _col("action_kind", "VARCHAR"),
        # `target`, not `target_view`. A trigger has exactly one target and it
        # may be a task, so the engine collapsed the pair into one column; this
        # mirror kept the old name, and a client that trusted it and asked for
        # `$select=target_view` got ColumnNotFound from the engine rather than a
        # column of nulls. That is what test_virtual_schemas_match_the_engine
        # now catches.
        _col("target", "VARCHAR"),
        # WHOSE AUTHORITY the trigger's unattended runs carry. It was missing
        # here entirely, so the one identity behind work that happens with
        # nobody present was invisible to every OData client.
        _col("runs_as", "VARCHAR"),
        _col("created_by", "VARCHAR"),
        _col("created_at", "TIMESTAMP"),
        _col("last_fired_at", "TIMESTAMP"),
        _col("last_fired_status", "VARCHAR"),
        # Set by ALTER TRIGGER ... SUSPEND, cleared by RESUME. Without them a
        # paused trigger reads exactly like a running one until something writes
        # to its source and the refused fire stamps last_fired_status.
        _col("suspended_at", "TIMESTAMP"),
        _col("suspended_by", "VARCHAR"),
        # The floor between two firings, set by ALTER TRIGGER ... SET MINIMUM
        # INTERVAL TO. Null for a trigger created before the setting existed,
        # which fires on every commit; 0 where the floor was removed. Inside the
        # interval a commit stamps `last_fired_status: throttled` and fires
        # nothing - the setting working, not a fault.
        _col("minimum_interval_seconds", "BIGINT"),
        # When a signal trigger's token was last rotated; null for the other
        # kinds. The token itself is never reported here.
        _col("signal_token_rotated_at", "TIMESTAMP"),
    ],
    # Every task registered in the workspace. Mirrors the engine's
    # `InformationSchemaTasksTable`; `writes` is what makes a task a node with
    # an edge going OUT, so a pipeline that passes through one can be followed
    # end to end. Comma-separated, because a relation name cannot contain a
    # comma.
    "tasks": [
        _col("task_catalog", "VARCHAR"),
        _col("task_collection", "VARCHAR"),
        _col("task_name", "VARCHAR"),
        _col("description", "VARCHAR"),
        _col("statement", "VARCHAR"),
        _col("statement_id", "VARCHAR"),
        _col("writes", "VARCHAR"),
        _col("created_by", "VARCHAR"),
        _col("created_at", "TIMESTAMP"),
        _col("last_updated_by", "VARCHAR"),
        _col("last_updated_at", "TIMESTAMP"),
        _col("suspended_at", "TIMESTAMP"),
        _col("suspended_by", "VARCHAR"),
        _col("last_fired_at", "TIMESTAMP"),
        _col("last_fired_status", "VARCHAR"),
        _col("last_window_to", "BIGINT"),
        # The CALLER's own subscription to the task ("ERROR" / "SUCCESS" /
        # "EVERYTHING"), or null. Not a subscriber count - that would tell
        # everyone who can read this table how many people watch a task.
        _col("listening", "VARCHAR"),
    ],
    "listeners": [
        # Object columns, not task columns: a subscription is to a task OR a
        # materialized view, and `kind` says which ("task" |
        # "materialized_view"). The engine records the kind at subscription
        # time; the caller never writes it, since the three share one namespace.
        _col("object_catalog", "VARCHAR"),
        _col("object_collection", "VARCHAR"),
        _col("object_name", "VARCHAR"),
        _col("kind", "VARCHAR"),
        _col("outcome", "VARCHAR"),
        _col("created_at", "TIMESTAMP"),
    ],
    "forks": [
        # Two ends, one table: a row appears whether this workspace holds the
        # fork or the dataset it was cloned from. Both are fully qualified, and
        # exactly one of them is local to the workspace being read.
        _col("fork", "VARCHAR"),
        _col("upstream", "VARCHAR"),
        _col("base_snapshot", "VARCHAR"),
        # UPPER BOUNDS, not counts - a sequence number advances on every
        # commit, maintenance included. Null on a row seen from the upstream's
        # side, where the fork's own sequence is in another workspace.
        _col("revisions_behind", "INTEGER"),
        _col("revisions_ahead", "INTEGER"),
        _col("last_sync", "TIMESTAMP"),
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
        # engine's projection, in another repository -- but no longer an
        # unchecked one: tests/test_virtual_schemas_match_engine.py compares
        # every list here against `information_schema._TABLE_CLASSES[...]._COLUMNS`
        # and fails on any divergence. It was unchecked, and `triggers` drifted
        # by two columns before anybody noticed.
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
    # The workspace's access as a relation: for the workspace, each collection
    # and each table and view, every stored policy that reaches it. Mirrors
    # the engine's `InformationSchemaGrantsTable`; `origin` is what the table
    # is for -- `explicit` where the policy is stored AT the object (what
    # `SHOW GRANTS ON` reports), `inherited` where it covers the object from
    # above (what `SHOW EFFECTIVE GRANTS ON` adds). The engine gates each row
    # on the caller's authority to administer the object, so what a reader
    # gets here is what they could have asked the statements for.
    "grants": [
        _col("grant_catalog", "VARCHAR"),
        _col("grant_collection", "VARCHAR"),
        _col("object_kind", "VARCHAR"),
        _col("object_name", "VARCHAR"),
        _col("grantee", "VARCHAR"),
        _col("role", "VARCHAR"),
        _col("pattern", "VARCHAR"),
        _col("level", "VARCHAR"),
        _col("origin", "VARCHAR"),
    ],
    # The workspace's customer secrets, WITHOUT their values - the engine's
    # catalog strips key material before its table sees a record. Mirrors
    # `InformationSchemaSecretsTable`, which refuses as a WHOLE (PermissionError,
    # served as 403) without ALTER on the whole workspace rather than returning
    # an empty list that would read as "none". `last_used_at` is the last
    # ATTEMPTED use.
    "maintenance": [
        # ONE row, for this workspace: the setting is held at the workspace and
        # everything inside inherits it, so there is no per-object value to list.
        _col("catalog_name", "VARCHAR"),
        _col("maintenance", "BOOL"),
    ],
    "secrets": [
        _col("secret_catalog", "VARCHAR"),
        _col("secret_name", "VARCHAR"),
        _col("secret_type", "VARCHAR"),
        _col("scope", "VARCHAR"),
        _col("created_by", "VARCHAR"),
        _col("created_at", "TIMESTAMP"),
        _col("updated_by", "VARCHAR"),
        _col("updated_at", "TIMESTAMP"),
        _col("last_used_at", "TIMESTAMP"),
        _col("use_count", "BIGINT"),
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
    # What the statement WRITES, derived from its own AST when the task was
    # registered -- never declared, so it cannot disagree with the statement.
    # Comma-separated; empty both for a task that writes nothing and for one
    # registered before the field existed.
    _col("writes", "VARCHAR"),
    _col("created_by", "VARCHAR"),
    _col("created_at", "TIMESTAMP"),
    # Who last changed the STATEMENT. Sourced from the statement document, not
    # the task document, for the reason get_task separates them.
    _col("last_updated_by", "VARCHAR"),
    _col("last_updated_at", "TIMESTAMP"),
    # No `suspended_at`/`suspended_by`, and there were: nothing writes them on a
    # task document because a task cannot be suspended. A task is WHAT runs; its
    # trigger is WHEN, and the when is what gets suppressed. Both columns could
    # only ever be null, so they described a state this object does not have.
    # Read suspension from information_schema.triggers instead.
    _col("last_fired_at", "TIMESTAMP"),
    _col("last_fired_status", "VARCHAR"),
    # The `current_version` the last SUCCESSFUL run consumed to -- a version
    # number, not a timestamp, and stamped only on success so that a gap behind
    # a failed run stays visible.
    _col("last_window_to", "BIGINT"),
]

TASK_DESCRIPTION = (
    "A **task** is a statement the platform runs on its own. It holds no rows, "
    "so it reads as the one row describing itself."
)

# A property graph holds no rows either -- its vertices and edges are the rows of
# the tables it names, queried with GQL. Its name answers to a read with its
# STRUCTURE: one row per element table, which is what a client needs to draw the
# graph, and never the contents of those tables. The rows come from the engine
# (opteryx-core `connectors/property_graph_structure.py`, `STRUCTURE_COLUMNS`);
# this is the mirror of that column list, checked against it by
# tests/test_virtual_schemas_match_engine.py.
#
# Lists are comma-separated, as `information_schema.tasks.writes` is. A property
# whose name differs from its column reads `column AS name`. The four
# `source_*`/`destination_*` columns are null on a vertex row.
PROPERTY_GRAPH_STRUCTURE_COLUMNS: list[EdmProperty] = [
    _col("element_kind", "VARCHAR"),  # 'vertex' | 'edge'
    _col("element_name", "VARCHAR"),
    _col("label", "VARCHAR"),
    _col("table_name", "VARCHAR"),
    _col("key_columns", "VARCHAR"),
    _col("properties", "VARCHAR"),
    _col("source_vertex", "VARCHAR"),
    _col("source_key", "VARCHAR"),
    _col("source_references", "VARCHAR"),
    _col("destination_vertex", "VARCHAR"),
    _col("destination_key", "VARCHAR"),
    _col("destination_references", "VARCHAR"),
]

PROPERTY_GRAPH_DESCRIPTION = (
    "A **property graph** over tables in the catalog. It holds no rows of its own, "
    "so it reads as its structure: one row per vertex or edge table.\n"
    "\n"
    "- `element_kind` is `vertex` or `edge`; an edge row names its "
    "`source_vertex` and `destination_vertex` by their `element_name`\n"
    "- Query the graph's contents with GQL, or read its tables directly"
)


# Default Custom.Description annotation per virtual dataset, used where the real
# catalog would otherwise supply a description/comment. The UI renders these
# with the limited markdown described at
# https://docs.opteryx.app/docs/core-concepts/markdown-in-descriptions -- bold,
# italic, inline code, links, flat lists, headings. Keep the opening sentence to
# one line; put anything a reader needs to know about a specific column in a
# bullet naming that column.
VIRTUAL_INFORMATION_SCHEMA_DESCRIPTIONS: dict[str, str] = {
    "tables": "Tables and views visible in this workspace, with their latest snapshot and size.",
    "columns": "Columns of every table and view visible in this workspace.",
    "views": "Views visible in this workspace, with their SQL definition.",
    "triggers": (
        "Triggers on the tables visible in this workspace.\n"
        "\n"
        "- `event_kind` is what fires it: `commit` on `event_object_table`, "
        "`schedule` (see `schedule`, `time_zone`, `next_due_at`) or `signal`\n"
        "- `trigger_holder` is what it hangs off: the source dataset, or the task "
        "for a schedule or signal trigger\n"
        "- `target` is what fires: a view or a task\n"
        "- `runs_as` is whose authority the run carries\n"
        "- `suspended_at` is set while the trigger is paused\n"
        "- `minimum_interval_seconds` is the floor between two firings; a commit "
        "inside it stamps `last_fired_status: throttled` and fires nothing"
    ),
    "tasks": (
        "Tasks registered in this workspace: statements the platform runs on its own.\n"
        "\n"
        "- `writes` lists the tables the statement writes to, comma-separated\n"
        "- `last_window_to` is the version the last successful run consumed to\n"
        "- `listening` is YOUR subscription to the task's outcomes, or null"
    ),
    "forks": (
        "Datasets created from another by CREATE TABLE ... CLONE, and the "
        "datasets they were cloned from - one row per relationship this "
        "workspace is an end of. `revisions_behind` and `revisions_ahead` are "
        "upper bounds, not counts: a sequence number advances on every commit, "
        "compaction included, so report them as \"at most N\". Zero is exact."
    ),
    "listeners": (
        "The tasks and materialized views YOU are subscribed to for run "
        "notifications - one row per subscription, yours only. `kind` is "
        "`task` or `materialized_view`; `outcome` is which outcomes it covers: "
        "`ERROR`, `SUCCESS` or `EVERYTHING`. Subscribe with `LISTEN TO <name> "
        "[FOR ...]`, unsubscribe with `UNLISTEN <name>`."
    ),
    "column_relationships": (
        "Relationships declared between columns of the tables visible in this "
        "workspace. A relationship is a declaration, not a constraint: **nothing "
        "enforces it**.\n"
        "\n"
        "- A row appears only where both tables are visible\n"
        "- `origin` of `inferred` with `status` of `unverified` is an unconfirmed "
        "proposal, never emitted as a navigable relationship"
    ),
    "schemata": "Schemas (collections) visible in this workspace.",
    "grants": (
        "Access policies reaching the workspace and each collection, table and view "
        "in it. Rows are shown only for objects the caller may administer.\n"
        "\n"
        "- `origin` of `explicit`: stored at the object, as `SHOW GRANTS ON` reports\n"
        "- `origin` of `inherited`: covers it from the collection or workspace above, "
        "as `SHOW EFFECTIVE GRANTS ON` adds"
    ),
    "maintenance": (
        "Whether the platform keeps this workspace's data compacted - one row, "
        "for the workspace. Everything in the workspace inherits it.\n"
        "\n"
        "- `maintenance` is `true` when the platform's maintenance identity holds "
        "its WRITE grant here, which is exactly what the compactor checks"
    ),
    "secrets": (
        "Credentials stored in this workspace for `READ_*` `credentials =>`, never "
        "with their values. What `SHOW SECRETS` returns.\n"
        "\n"
        "- Readable only with owner on the whole workspace; otherwise refused, not empty\n"
        "- `last_used_at` is the last attempted use, including refused reads"
    ),
}


# The action a caller must hold on SOMETHING in a workspace for the table to be
# offered there. It is the action the engine gates the table's ROWS on (see the
# information_schema module docstring in opteryx-core), so a table is listed only
# where a query of it could return anything. Offering `tasks` to a reader would
# hand them an always-empty table whose column list and description say what
# they are not allowed to see - which is a disclosure, just a small one.
#
#   READ      existence and shape: what a reader needs to write a query
#   AUTOMATE  tasks and triggers: standing automation, owner tier
#   GRANT     grants: who holds what, owner tier
#
# Which ROLES confer each action is opteryx-access's to say (`ACTION_ROLES`),
# and app.auth.permissions asks it rather than restating the answer here.
VIRTUAL_INFORMATION_SCHEMA_ACTIONS: dict[str, str] = {
    "tables": "READ",
    "columns": "READ",
    "views": "READ",
    "triggers": "AUTOMATE",
    "tasks": "AUTOMATE",
    # READ, and deliberately not AUTOMATE like `tasks`: LISTEN is gated on
    # reading what the task WRITES, so a subscriber need not own the task -
    # and this table is the only place a non-owner subscriber can see what
    # they subscribed to, since `tasks` is closed to them. Its rows are the
    # caller's own, so any reader could have some.
    "listeners": "READ",
    "column_relationships": "READ",
    # READ, like `listeners` and not GRANT like `grants`: a fork relationship
    # is a fact about where data came from, not about who may reach it. The
    # engine gates each row on READ of the dataset in THIS workspace, so a row
    # only ever names a far end beside something the caller can already see.
    "forks": "READ",
    "schemata": "READ",
    "grants": "GRANT",
    # READ: the engine does not gate it at all - it says whether the
    # workspace is maintained, names no principal, and is one row about the
    # workspace itself. READ is the lowest tier this file offers at, so it is
    # listed wherever the caller can read anything in the workspace.
    "maintenance": "READ",
    #   ALTER     secrets: the engine's gate is ALTER on the whole workspace
    #             (owner of `ws.*`). Offered at this file's usual grain - an owner
    #             of anything in the workspace - and the engine refuses the rest
    #             as a whole, which the data path serves as a 403.
    "secrets": "ALTER",
}

# A table with a schema but no tier would be offered to nobody; one with a tier
# but no schema could not be described. Either is a registration mistake.
assert VIRTUAL_INFORMATION_SCHEMA_ACTIONS.keys() == VIRTUAL_INFORMATION_SCHEMA_TABLES.keys()


def is_virtual_information_schema(collection: str, name: str) -> bool:
    """Whether (collection, name) refers to a virtual information_schema dataset."""
    return collection == "information_schema" and name in VIRTUAL_INFORMATION_SCHEMA_TABLES
