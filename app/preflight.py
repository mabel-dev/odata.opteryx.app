"""What this service must be able to reach before it is allowed to serve.

    python -m app.preflight && exec <server> ...

Run from the Dockerfile's CMD, ahead of the server, so an instance that cannot
reach its dependencies exits the container non-zero without ever opening a
port. On Cloud Run that fails the revision, so a broken build cannot swap out a
working one. See `opteryx_shared_services.preflight` for why that guarantee
cannot live in the ASGI lifespan alone.

This file is only the declaration -- the engine, the probe shapes and the
alerting are shared across the fleet.
"""

import sys

from opteryx_shared_services.preflight import Preflight
from opteryx_shared_services.preflight import firestore_read
from opteryx_shared_services.preflight import gcs_list
from opteryx_shared_services.preflight import secret_manager

preflight = Preflight(
    component="odata",
    checks=(
        secret_manager(),
        # `datasets` is what a $metadata request enumerates; the configuration
        # document shares the `(default)` database.
        firestore_read("firestore-datasets", collection="datasets"),
        firestore_read(
            "firestore-catalog",
            collection="$catalog",
            database_key="FIRESTORE_DATABASE",
            covered_by="firestore-datasets",
        ),
        # This service holds no storage client of its own -- the bucket is
        # handed to the engine, which reads data out of it. Read, so LIST.
        gcs_list("gcs-data", bucket_key="GCS_BUCKET"),
    ),
)

__all__ = ["preflight"]


if __name__ == "__main__":
    sys.exit(preflight.main())
