"""Pytest configuration for odata.opteryx tests.

Sets up sys.path so tests can import:
  - the app package itself (repo root)
  - the vendored odata_query parser (app/vendored/odata-query)
  - local dev checkouts of opteryx-core / opteryx-catalog, when present

The local dev checkouts are siblings of the repo root and only exist on a
developer machine. In CI they are absent and the pip-installed distributions
are used instead, so the paths are only prepended when they actually exist --
a non-existent sys.path entry is silently ignored by the import machinery,
but skipping it keeps sys.path honest and makes CI behaviour explicit.
"""

import os
import sys

_root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _prepend(path: str) -> None:
    """Put an existing directory at the front of sys.path, once."""
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)


# The repo root, so `import app...` resolves without an editable install.
_prepend(_root_dir)

# The vendored odata-query parser. app/odata/__init__.py adds this itself, but
# only once it is imported -- tests that import odata_query directly (the
# parser-level tests) need it on the path before any app import happens.
_prepend(os.path.join(_root_dir, "app", "vendored", "odata-query"))

# Local dev checkouts, if the developer has them alongside this repo.
_prepend(os.path.abspath(os.path.join(_root_dir, "..", "opteryx-core")))
_prepend(os.path.abspath(os.path.join(_root_dir, "..", "opteryx-catalog")))


import pytest  # noqa: E402  (must follow the sys.path setup above)

_CATALOG_AVAILABLE = None


def _catalog_available() -> bool:
    """True when Google credentials are resolvable, so catalog-backed tests can run.

    Cached: resolving credentials is not free, and this is consulted once per
    collected item.

    NO_GCE_CHECK stops google-auth falling back to probing the GCE metadata
    server at 169.254.169.254 when no local credentials are configured. That
    probe is a real outbound network call -- three retries, ~6s -- on every CI
    run, and CI must not reach GCP at all. With it disabled the same question is
    answered from local configuration in ~0.05s. Only the *fallback* is
    suppressed; a machine with ADC or GOOGLE_APPLICATION_CREDENTIALS still
    resolves normally, so catalog tests keep running for developers who have
    credentials. Set NO_GCE_CHECK=false to allow the probe when actually running
    on a GCE instance.
    """
    global _CATALOG_AVAILABLE
    if _CATALOG_AVAILABLE is None:
        os.environ.setdefault("NO_GCE_CHECK", "true")
        try:
            import google.auth

            google.auth.default()
            _CATALOG_AVAILABLE = True
        except Exception:
            _CATALOG_AVAILABLE = False
    return _CATALOG_AVAILABLE


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_catalog: needs a live Firestore/GCS catalog; skipped without credentials",
    )


def pytest_collection_modifyitems(config, items):
    """Skip catalog-backed tests when there are no credentials to reach it with.

    These query real datasets (public.github.events, public.demo.users). Without
    credentials they don't just fail, they fail *slowly* -- each one retries the
    GCE metadata server -- so they are skipped rather than left to time out.
    """
    if _catalog_available():
        return
    skip = pytest.mark.skip(reason="no catalog credentials available (set up ADC to run)")
    for item in items:
        if "requires_catalog" in item.keywords:
            item.add_marker(skip)


class FakePlatformModeFirestore:
    """Stands in for Firestore under `platform_mode`: one config document, held in memory.

    `platform_mode` is None for a document with no switch set (operational).
    """

    def __init__(self, platform_mode=None):
        self.platform_mode = platform_mode
        self.reads = 0

    def collection(self, _name):
        return self

    def document(self, _name):
        return self

    def get(self, timeout=None):
        from types import SimpleNamespace

        self.reads += 1
        data = {} if self.platform_mode is None else {"platform_mode": dict(self.platform_mode)}
        return SimpleNamespace(exists=True, to_dict=lambda: data)


@pytest.fixture(autouse=True)
def platform_mode_store(monkeypatch):
    """Every request through app.main consults the platform mode; never let it reach Firestore.

    The shared module's default reader is replaced by one over an in-memory
    document with no switch set, so the service is operational unless a test
    sets `platform_mode_store.platform_mode`. No cache: a change is seen by the
    very next request.
    """
    from opteryx_shared_services import platform_mode as pm

    store = FakePlatformModeFirestore()
    monkeypatch.delenv(pm.ENV_OVERRIDE, raising=False)
    monkeypatch.setattr(
        pm,
        "_default",
        pm.PlatformMode(
            environment="test",
            client_provider=lambda: store,
            cache_ttl=0,
            promoted=lambda key, value: False,
        ),
    )
    return store


@pytest.fixture(autouse=True)
def _isolate_process_caches():
    """Empty the process-wide caches around every test.

    $metadata responses, column statistics and basic-auth exchanges are cached
    by (dataset, role, policy), by (dataset, snapshot id, schema id) and by
    credentials respectively. Tests reuse those keys while swapping the catalog
    or auth service underneath them, so without this a test would be served the
    previous test's document, statistics or claims.
    """
    from app.auth.external_auth import clear_credential_cache
    from app.routes.v4.interface import clear_describe_cache
    from app.routes.v4.interface import clear_metadata_cache

    from tests._opteryx_stubs import LOADED_MODULES

    def _clear():
        clear_metadata_cache()
        clear_describe_cache()
        clear_credential_cache()
        # Stub-loaded copies of interface.py hold their own caches (see
        # _opteryx_stubs.LOADED_MODULES), so clearing the app's is not enough.
        for module in LOADED_MODULES:
            module_clear = getattr(module, "clear_metadata_cache", None)
            if callable(module_clear):
                module_clear()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _static_workspaces_own_their_settings():
    """Let a `register_workspace` mount answer the settings chain too.

    The e2e tests mount `testdata` with `opteryx.register_workspace(...,
    DiskConnector)`. That static registration wins the DATA chain (slot 1 of
    `connector_factory`), but opteryx-core's view probe resolves through
    `view_store_connector` -> `workspace_settings_connector`, which asks the
    installed settings resolver and never looks at static registrations.
    `app.routes.v4.interface` installs `resolve_workspace_settings`, which
    always answers "the native catalog", so once the app is imported the
    probe asks Firestore about `testdata` and fails with WorkspaceNotFound
    before the disk mount is ever consulted. Whether a test saw that depended
    on whether anything had imported the app yet.

    Production registers no static workspaces, so this is only wrapped here:
    a statically registered name resolves to its own registration on the
    settings axis as well, and everything else goes to the app's resolver.
    """
    from opteryx import connectors

    # Importing the interface is what installs the app's resolvers; do it now
    # so a later first import cannot reinstall the unwrapped one mid-test.
    import app.routes.v4.interface  # noqa: F401
    from app.catalog_resolver import resolve_workspace_settings

    def resolve(workspace):
        entry = connectors._storage_prefixes.get(workspace)
        if entry is not None:
            config = {k: v for k, v in entry.items() if k != "connector"}
            return connectors.Resolution(entry["connector"], config)
        return resolve_workspace_settings(workspace)

    connectors.set_workspace_settings_resolver(resolve)
    yield
    connectors.set_workspace_settings_resolver(resolve_workspace_settings)


def _build_app(authenticate):
    """Build a FastAPI app wired up the way app.main builds the real service.

    Mounting only the router is not enough: the OData error format comes from
    the exception handlers registered on the *app*, so a bare FastAPI() returns
    FastAPI's default {"detail": ...} and any test asserting the OData
    {"error": {...}} shape fails against a service that is actually correct.
    """
    from fastapi import FastAPI, HTTPException

    from app.routes.v4.errors import (
        ODataError,
        generic_exception_handler,
        http_exception_handler,
        odata_error_handler,
    )
    from app.routes.v4.interface import require_authentication, router

    app = FastAPI()
    app.add_exception_handler(ODataError, odata_error_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
    app.dependency_overrides[require_authentication] = authenticate
    app.include_router(router)
    return app


@pytest.fixture
def client():
    """TestClient whose requests satisfy the auth *dependency* but carry no identity.

    Use this for checks that are rejected before identity matters (unsupported
    options, malformed parameters). Requests that get past those checks stop at
    the endpoint's own identity check rather than reaching the catalog, which
    keeps these tests offline.
    """
    from fastapi.testclient import TestClient

    return TestClient(_build_app(lambda: None))


@pytest.fixture
def authenticated_client():
    """TestClient with a full identity stamped onto request.state.claims.

    Use this when the behaviour under test lives past the endpoint's identity
    check. Note that requests reaching query execution will try to hit the real
    catalog, so pair this with a monkeypatched execute_odata_query.
    """
    from fastapi import Request
    from fastapi.testclient import TestClient

    def authenticate(request: Request):
        claims = {"identity": "test-user"}
        request.state.claims = claims
        return claims

    return TestClient(_build_app(authenticate))
