"""Shared lightweight `opteryx` fakes for tests that load app modules in isolation.

Some tests exercise the metadata / service-document routes without a real engine
or catalog by installing stub modules under the `opteryx*` names.

Two things are easy to get wrong when doing this by hand, and both are handled
here:

1. `types.ModuleType("opteryx")` is a *module*, not a package, so a later
   `from opteryx.models import Node` fails with
   "No module named 'opteryx.models'; 'opteryx' is not a package".
   Stub packages therefore need a `__path__` -- see `_package()`.

2. Stubs installed at import time stay in `sys.modules` for the rest of the
   session, so every later test that imports the real opteryx dies. The stubs
   must be torn down once the module under test has been loaded; the loaded
   module keeps working afterwards because it has already bound the names it
   imported.

Use `load_with_stubs()` to load a module against the fakes, or `stubbed_opteryx()`
directly as a context manager.
"""

import contextlib
import importlib.util
import sys
import types
from pathlib import Path

# Every name the stubs replace; recorded so teardown can restore exactly.
_STUBBED_NAMES = [
    "opteryx",
    "opteryx.connectors",
    "opteryx.connectors.mabel_connector",
    "opteryx.connectors.postgres_connector",
    "opteryx.constants",
    "opteryx.expression",
    "opteryx.models",
    "opteryx.models.logical_column",
    "opteryx.planner",
    "opteryx.planner.logical_planner",
    "opteryx.utils",
    "opteryx_access",
    "opteryx_access.actions",
    "opteryx_access.adapters",
    "opteryx_access.adapters.firestore",
    "opteryx_catalog",
    "opteryx_catalog.catalog",
    "opteryx_catalog.catalog.sort_order",
    "opteryx_catalog.catalog.dataset",
]


def _package(name):
    """A ModuleType that can host submodules (`__path__` makes it a package)."""
    module = types.ModuleType(name)
    module.__path__ = []
    return module


def _build_stubs():
    stubs = {}

    opteryx_mod = _package("opteryx")
    opteryx_mod.set_default_connector = lambda *args, **kwargs: None
    # Installed at import by app.routes.v4.interface. A no-op here: these stubs
    # exist to load that module without the engine, and there is no engine for a
    # capability to decide anything for.
    opteryx_mod.register_permissions_capability = lambda *args, **kwargs: None
    stubs["opteryx"] = opteryx_mod

    access_mod = _package("opteryx_access")
    # Takes the policy store app.routes.v4.interface builds and hands it: a bare
    # `capability()` can only answer about the asking session, so the real call
    # is `capability(store)`. Accepts anything -- these stubs exist to load the
    # module without an engine, and there is nothing here for a capability to
    # decide about.
    access_mod.capability = lambda *args, **kwargs: object()
    stubs["opteryx_access"] = access_mod

    # The role -> action table app.auth.permissions asks when deciding which
    # virtual information_schema tables to offer. The real one is
    # opteryx_access.actions.ACTION_ROLES; this is the shape of it these tests
    # need - a reader reads, a writer also writes, an owner does everything.
    _STUB_ROLE_ACTIONS = {
        "reader": {"READ"},
        "writer": {"READ", "WRITE", "UPDATE", "DELETE", "CREATE", "REFRESH"},
        "owner": {
            "READ", "WRITE", "UPDATE", "DELETE", "CREATE", "REFRESH",
            "DROP", "ALTER", "MANIFEST", "AUTOMATE", "GRANT", "REVOKE",
        },
    }
    access_actions = types.ModuleType("opteryx_access.actions")
    access_actions.action_allowed_for_role = (
        lambda role, action: action in _STUB_ROLE_ACTIONS.get(role, set())
    )
    stubs["opteryx_access.actions"] = access_actions

    stubs["opteryx_access.adapters"] = _package("opteryx_access.adapters")

    # The Firestore-backed policy store. Constructed at import by
    # app.routes.v4.interface, which passes a `db_factory` and nothing else;
    # never consulted under the stubs, since no query runs.
    access_firestore = types.ModuleType("opteryx_access.adapters.firestore")
    access_firestore.FirestorePolicyStore = type(
        "FirestorePolicyStore", (), {"__init__": lambda self, *args, **kwargs: None}
    )
    stubs["opteryx_access.adapters.firestore"] = access_firestore

    connectors = types.ModuleType("opteryx.connectors")
    connectors.OpteryxConnector = type("OpteryxConnector", (), {})
    # `app.catalog_resolver` installs itself into opteryx-core's resolution
    # chain at import, so these have to exist for the app to import at all.
    # No-ops here: what the resolver RESOLVES is covered by its own tests, and
    # a stub that actually registered would leak one test's connector into the
    # next.
    connectors.set_workspace_resolver = lambda *a, **k: None
    connectors.set_workspace_settings_resolver = lambda *a, **k: None
    connectors.Resolution = type("Resolution", (), {})
    stubs["opteryx.connectors"] = connectors

    mabel_connector = types.ModuleType("opteryx.connectors.mabel_connector")
    mabel_connector.create_gcs_mabel_connector = lambda *a, **k: None
    stubs["opteryx.connectors.mabel_connector"] = mabel_connector

    postgres_connector = types.ModuleType("opteryx.connectors.postgres_connector")
    postgres_connector.PostgresConnector = type("PostgresConnector", (), {})
    stubs["opteryx.connectors.postgres_connector"] = postgres_connector

    constants = types.ModuleType("opteryx.constants")
    constants.ResultType = type("ResultType", (), {"TABULAR": "TABULAR"})
    stubs["opteryx.constants"] = constants

    expression = types.ModuleType("opteryx.expression")
    expression.NodeType = type("NodeType", (), {})
    stubs["opteryx.expression"] = expression

    models = _package("opteryx.models")
    models.Node = type("Node", (), {})
    stubs["opteryx.models"] = models

    logical_column = types.ModuleType("opteryx.models.logical_column")
    logical_column.LogicalColumn = type("LogicalColumn", (), {})
    stubs["opteryx.models.logical_column"] = logical_column

    stubs["opteryx.planner"] = _package("opteryx.planner")

    logical_planner = types.ModuleType("opteryx.planner.logical_planner")
    logical_planner.LogicalPlan = type("LogicalPlan", (), {})
    logical_planner.LogicalPlanNode = type("LogicalPlanNode", (), {})
    logical_planner.LogicalPlanStepType = type("LogicalPlanStepType", (), {})
    stubs["opteryx.planner.logical_planner"] = logical_planner

    utils = types.ModuleType("opteryx.utils")
    utils.random_string = lambda *args, **kwargs: "stub"
    stubs["opteryx.utils"] = utils

    catalog = _package("opteryx_catalog")
    catalog.OpteryxCatalog = type("OpteryxCatalog", (), {"load_dataset": lambda self, path: None})
    stubs["opteryx_catalog"] = catalog

    stubs["opteryx_catalog.catalog"] = _package("opteryx_catalog.catalog")

    compaction = types.ModuleType("opteryx_catalog.catalog.sort_order")
    compaction.normalize_sort_order = lambda *args, **kwargs: None
    stubs["opteryx_catalog.catalog.sort_order"] = compaction

    # The real `_stored_type_display` renders DECIMAL precision/scale and ARRAY
    # element types into the canonical display string; the bare name is enough
    # for tests, which use simple scalar types.
    dataset = types.ModuleType("opteryx_catalog.catalog.dataset")
    dataset._stored_type_display = lambda column: str(column.get("type") or "VARCHAR").upper()
    stubs["opteryx_catalog.catalog.dataset"] = dataset

    return stubs


@contextlib.contextmanager
def stubbed_opteryx():
    """Install the opteryx/opteryx_catalog stubs, restoring sys.modules on exit."""
    saved = {name: sys.modules.get(name) for name in _STUBBED_NAMES}
    # Deliberately NOT evicting `app.*` from sys.modules here. Doing so makes the
    # next `import app.odata.apply_builder` build a *second* module object, while
    # classes other test modules already imported stay bound to the first -- so
    # monkeypatching one has no effect on the other. Any app module already
    # imported for real is fine to reuse; one imported for the first time under
    # these stubs picks them up naturally.
    sys.modules.update(_build_stubs())
    try:
        yield
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def load_app_module(module_name, *relative_parts):
    """Load an app module by file path, bypassing package __init__ imports.

    Call inside `stubbed_opteryx()`. The loaded module binds the names it
    imported directly, so it keeps working after the stubs are torn down.
    """
    spec = importlib.util.spec_from_file_location(
        module_name,
        str(Path(__file__).resolve().parents[1].joinpath(*relative_parts)),
    )
    module = importlib.util.module_from_spec(spec)
    loader = spec.loader
    assert loader is not None
    loader.exec_module(module)
    return module


# Every module handed out by load_with_stubs. These are distinct module objects,
# not the ones under `app.` in sys.modules, so each carries its own copy of any
# module-level state -- including the process caches that conftest resets between
# tests. Without this registry those copies would be unreachable from conftest and
# would leak state across tests.
LOADED_MODULES = []


def load_with_stubs(module_name, *relative_parts):
    """Load an app module against the stubs and restore sys.modules immediately."""
    with stubbed_opteryx():
        module = load_app_module(module_name, *relative_parts)
    LOADED_MODULES.append(module)
    return module
