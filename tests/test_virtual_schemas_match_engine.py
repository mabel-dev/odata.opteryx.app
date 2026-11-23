"""The virtual information_schema mirror must match the engine's projection.

`VIRTUAL_INFORMATION_SCHEMA_TABLES` is a hand-written copy, in this repository,
of column lists that live in opteryx-core. Nothing compared the two, and they
drifted: the engine collapsed a trigger's `target_view`/`target_task` pair into
one `target` column and added `runs_as`, while this mirror kept saying
`target_view` and never mentioned `runs_as`.

That is not a cosmetic divergence. `$metadata` and the service document are
built from the mirror, so the drift published a wrong contract; a client that
believed it and asked for `$select=target_view` got a ColumnNotFound from the
engine, because the DATA path serializes whatever the engine returns and never
consults this list at all. One half of the service described a schema the other
half could not serve.

This test is deliberately version-coupled. It compares against the INSTALLED
opteryx-core, so it fails when this service's pinned floor is behind the engine
that owns these columns -- which is exactly the state that produces the bug
above, and is a thing to fix rather than a thing to tolerate.

Names and ORDER only. Comparing EDM types would need a DrakenType-to-EDM
translation table maintained here, which is a second unchecked mirror of the
kind this test exists to abolish.
"""

import pytest

from opteryx.connectors.information_schema import _TABLE_CLASSES

from app.odata.virtual_datasets import VIRTUAL_INFORMATION_SCHEMA_TABLES


def test_every_engine_table_is_mirrored():
    """A table the engine serves and this service does not describe is a name
    `$metadata` cannot answer for."""
    assert set(VIRTUAL_INFORMATION_SCHEMA_TABLES) == set(_TABLE_CLASSES)


@pytest.mark.parametrize("name", sorted(VIRTUAL_INFORMATION_SCHEMA_TABLES))
def test_virtual_schema_matches_the_engine(name):
    mirrored = [column.name for column in VIRTUAL_INFORMATION_SCHEMA_TABLES[name]]
    engine = list(_TABLE_CLASSES[name]._COLUMNS)

    assert mirrored == engine, (
        f"information_schema.{name} has drifted from opteryx-core.\n"
        f"  engine:   {engine}\n"
        f"  mirrored: {mirrored}\n"
        f"  missing here: {[c for c in engine if c not in mirrored]}\n"
        f"  not in engine: {[c for c in mirrored if c not in engine]}"
    )


def test_property_graph_structure_matches_the_engine():
    """A property graph reads as its structure, and the rows are the engine's; the
    columns `$metadata` publishes for it must be the ones the engine serves."""
    from opteryx.connectors.property_graph_structure import STRUCTURE_COLUMNS

    from app.odata.virtual_datasets import PROPERTY_GRAPH_STRUCTURE_COLUMNS

    assert [column.name for column in PROPERTY_GRAPH_STRUCTURE_COLUMNS] == list(STRUCTURE_COLUMNS)
