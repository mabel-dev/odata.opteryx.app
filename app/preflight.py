"""What this service must be able to reach before it is allowed to serve.

    python -m app.preflight && exec <server> ...

Run from the Dockerfile's CMD, ahead of the server, so an instance that cannot
reach its dependencies exits the container non-zero without ever opening a
port. On Cloud Run that fails the revision, so a broken build cannot swap out a
working one. See `opteryx_shared_services.preflight` for why that guarantee
cannot live in the ASGI lifespan alone.

Two declarations, because two questions are asked:

* `preflight` - the STARTUP gate, run on every container start. Only what the
  deploy canary (.github/workflows/deploy.yaml, scripts/canary_check.py) cannot
  prove for itself: Secret Manager, because the canary's requests are anonymous
  and never verify a token; and the `(default)` Firestore database, which holds
  the configuration document every request depends on.

* `health_checks` - every dependency, behind `/health`. The canary calls
  `/health` on the new revision before it takes traffic, so the catalog and the
  data bucket are still proven from inside that revision before it serves; and
  the status page polls `/health`, so losing either after a deploy still shows.
  The canary also reads a real table, which goes through both end to end.

This file is only the declaration -- the engine, the probe shapes and the
alerting are shared across the fleet.
"""

import sys

from opteryx_shared_services.preflight import Preflight
from opteryx_shared_services.preflight import firestore_read
from opteryx_shared_services.preflight import gcs_list
from opteryx_shared_services.preflight import secret_manager

_SECRET_MANAGER = secret_manager()
# Datasets, which a $metadata request enumerates, live in the catalog database.
_FIRESTORE_CATALOG = firestore_read(
    "firestore-catalog",
    collection="$catalog",
    database_key="FIRESTORE_DATABASE",
)
# This service holds no storage client of its own -- the bucket is handed to
# the engine, which reads data out of it. Read, so LIST.
_GCS_DATA = gcs_list("gcs-data", bucket_key="GCS_BUCKET")

preflight = Preflight(
    component="odata",
    checks=(_SECRET_MANAGER, _FIRESTORE_CATALOG),
)

health_checks = Preflight(
    component="odata",
    checks=(_SECRET_MANAGER, _FIRESTORE_CATALOG, _GCS_DATA),
)

__all__ = ["health_checks", "preflight"]


if __name__ == "__main__":
    sys.exit(preflight.main())
