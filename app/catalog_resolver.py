"""Workspace -> catalog resolution for the OData surfaces.

**Third sibling of `worker.opteryx/app/catalog_resolver.py` and
`jobs.opteryx/app/catalog_resolver.py`. All three MUST stay in step**, and the
reason this one exists is sharper than "consistency": OData ALREADY ADVERTISES
bound workspaces. `$metadata` and the service document are built from the stub
dataset documents a catalog refresh projects (see `routes/v4/metadata.py`'s
`STUB_MARKER`), so a PostgreSQL- or Iceberg-bound workspace's tables appear to
Excel and Power BI with their columns and types.

Before this module, that advertisement was a promise this service could not
keep. It registered ONLY a default connector (slot 3 of opteryx-core's
resolution chain), so every workspace resolved to the native catalog whatever
its binding said - a bound workspace's entity set listed its columns and then
returned nothing when a client read it. Listing a table you cannot serve is
worse than not listing it.

That this is a copy rather than a shared import is deliberate but not
comfortable, and the discomfort now outweighs the reasoning that justified it.
The original argument (jobs' copy) was that the mapping needs an opteryx-core
connector AND an opteryx-iceberg catalog, so a shared home must sit above both:
opteryx-catalog cannot host it (opteryx-iceberg imports opteryx-catalog, so it
would be circular) and opteryx-core deliberately holds no catalog, Firestore or
KMS knowledge. A new package for ~150 lines was judged worse than a second
copy. That judgement was made at TWO copies and TWO kinds; this is the third
copy and the third kind, and the drift it predicted has already happened once -
this service was the drift. Worth revisiting.

Installed into slot 2 by `routes/v4/interface.py` alongside the default
connector registration. Slot 2 is consulted BEFORE slot 3, so a bound workspace
resolves through here and an unbound one still falls through to the default
registration exactly as before.

Security boundary: a request chooses WHICH workspace, never what a workspace
means. `kind` is a name looked up in KINDS - the registry never carries an
import path or class name, so a compromised or malformed binding cannot make
this process load arbitrary code. Error messages and log lines carry the
workspace name and kind only, never config or auth values.
"""

from __future__ import annotations

import os
import re

from google.cloud import firestore
from opteryx.connectors import OpteryxConnector, Resolution
from opteryx.connectors.mabel_connector import create_gcs_mabel_connector
from opteryx.connectors.postgres_connector import PostgresConnector
from opteryx_catalog import OpteryxCatalog
from opteryx_catalog.binding import read_catalog_binding
from opteryx_iceberg import IcebergMetastore
from opteryx_shared_services.config import get_config
from opteryx_shared_services.logging import get_logger

logger = get_logger()


class CatalogResolutionError(Exception):
    """A workspace's catalog binding could not be resolved.

    Propagates out of `connector_factory` and fails the request - by design,
    never a fallback. Messages carry the workspace name and kind only.
    """


# The code-side allowlist: binding `kind` -> (connector, catalog). The only
# place a registry value becomes code. Grows one deliberate line at a time, and
# in three files at once - see the module docstring.
#
# A `None` catalog means the connector is not catalog-backed and must not have
# a `catalog` kwarg forced on it.
KINDS = {
    "iceberg": (OpteryxConnector, IcebergMetastore),
    "mabel": (create_gcs_mabel_connector, None),
    "postgres": (PostgresConnector, None),
}

# Mirrors opteryx_catalog.binding.RESERVED_CONFIG_KEYS (validated there at
# write time; re-checked here because this process must not trust that every
# document in the registry went through that writer).
_RESERVED_CONFIG_KEYS = ("workspace", "connector", "prefix")

# opteryx-core only consults the resolver for identifier-shaped segments, so
# this is defense in depth, not a reachable path in normal operation.
_WORKSPACE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_clients: dict = {}


def project_id() -> str:
    """The GCP project, resolved the way the rest of this service resolves it."""
    return get_config("GCP_PROJECT_ID") or os.environ.get("GCP_PROJECT_ID")


def _catalogs_firestore():
    project = project_id()
    database = get_config("FIRESTORE_DATABASE")
    key = (project, database)
    client = _clients.get(key)
    if client is None:
        client = firestore.Client(project=project, database=database)
        _clients[key] = client
    return client


def _native_resolution() -> Resolution:
    """The native Firestore/GCS catalog, with configuration resolved NOW.

    version is left None so opteryx-core versions this by config fingerprint:
    a changed FIRESTORE_DATABASE/GCS_BUCKET rotates the cached connector on the
    next lookup. Matches what `register_default_connector` registers in slot 3,
    so a workspace with no binding resolves identically either way.
    """
    return Resolution(
        OpteryxConnector,
        {
            "catalog": OpteryxCatalog,
            "firestore_project": project_id(),
            "firestore_database": get_config("FIRESTORE_DATABASE"),
            "gcs_bucket": get_config("GCS_BUCKET"),
        },
    )


def _inject(config: dict, dotted_path: str, value: str, workspace: str) -> None:
    """Place a decrypted credential at `dotted_path` inside `config`.

    "token" lands at config["token"]; "auth.credential" lands at
    config["auth"]["credential"], creating the intermediate mapping if the
    binding didn't carry one. A path segment that collides with a non-mapping
    config value is an error - silently overwriting it would mask a
    misconfigured binding.
    """
    parts = dotted_path.split(".")
    node = config
    for part in parts[:-1]:
        step = node.get(part)
        if step is None:
            step = {}
            node[part] = step
        if not isinstance(step, dict):
            raise CatalogResolutionError(
                f"workspace {workspace!r}: inject-as path does not name a config mapping"
            )
        node = step
    node[parts[-1]] = value


def resolve_workspace(workspace: str) -> Resolution:
    """One workspace name in, one Resolution out - or CatalogResolutionError."""
    if not _WORKSPACE_NAME.match(workspace or ""):
        raise CatalogResolutionError(f"workspace name {workspace!r} is not resolvable")

    try:
        binding = read_catalog_binding(_catalogs_firestore(), workspace)
    except Exception as exc:
        raise CatalogResolutionError(
            f"catalog binding for workspace {workspace!r} could not be read; failing the "
            "request rather than guessing a catalog"
        ) from exc

    if binding is None:
        return _native_resolution()

    kind_entry = KINDS.get(binding.kind)
    if kind_entry is None:
        raise CatalogResolutionError(
            f"workspace {workspace!r} is bound to unknown catalog kind {binding.kind!r}"
        )
    connector, catalog = kind_entry

    config = dict(binding.config)
    reserved = [key for key in _RESERVED_CONFIG_KEYS if key in config]
    if reserved:
        raise CatalogResolutionError(
            f"workspace {workspace!r}: binding config carries reserved key(s) {reserved}"
        )
    # A catalog-backed kind gets its metastore injected; a kind whose entry
    # carries None does not, because its connector takes no `catalog` kwarg and
    # passing one would be a TypeError at construction rather than a clear
    # configuration error here.
    if catalog is not None:
        config["catalog"] = catalog
    # Where this DEPLOYMENT keeps workspace metadata - the same bucket
    # `_native_resolution` hands a native workspace. Deliberately NOT part of
    # the customer's binding: they say which server to read, the deployment says
    # where its own metadata lives. The connector reads a statistics manifest
    # from there, and plans without one when it is unset.
    if binding.kind == "postgres":
        config["gcs_bucket"] = get_config("GCS_BUCKET")
    # Not for Iceberg: OpteryxConnector hands its config to the metastore, which
    # has no use for it, and opteryx-iceberg (0.2.0) refuses any catalog property
    # outside its allow-list - so passing it would fail every query.
    if binding.preserve_sql_case and binding.kind != "iceberg":
        config["preserve_sql_case"] = True

    if binding.auth_mode == "stored":
        # Lazy: only deployments that actually serve a stored-credential
        # binding need the kms extra installed.
        from opteryx_catalog.security.kms import decrypt_secret

        try:
            plaintext = decrypt_secret(binding.ciphertext, binding.kms_key)
        except Exception as exc:
            # decrypt_secret's own message is already material-free; keep this
            # one the same way.
            raise CatalogResolutionError(
                f"stored credential for workspace {workspace!r} "
                f"(kind {binding.kind!r}) could not be decrypted"
            ) from exc
        _inject(config, binding.inject_as, plaintext.decode("utf-8"), workspace)

    logger.debug(
        "resolved workspace %s to catalog kind %s (binding version %s)",
        workspace,
        binding.kind,
        binding.version,
    )
    return Resolution(connector, config, version=binding.version)


def resolve_workspace_settings(workspace: str) -> Resolution:
    """Which catalog holds `workspace`'s settings and lifecycle.

    Always the native one, whatever the workspace's DATA is bound to: a
    workspace's properties and lifecycle live on its `$properties` document in
    this Firestore database, and an external binding does not move them - it is
    itself a `catalog` BLOCK INSIDE that same document.

    Reads no binding and decrypts nothing, deliberately. Keep in step with the
    worker's and jobs' copies.
    """
    if not _WORKSPACE_NAME.match(workspace or ""):
        raise CatalogResolutionError(f"workspace name {workspace!r} is not resolvable")
    return _native_resolution()
