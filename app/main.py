from contextlib import asynccontextmanager
import os
import sys

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from opteryx_shared_services.logging import get_logger
from opteryx_shared_services.logging import set_log_name

from opteryx_shared_services.audit import AuditMiddleware
from app.preflight import preflight
from app.middleware.compression import CompressionMiddleware
from app.middleware.cors import configure_cors
from app.routes import router as routes_router
from app.routes.v4.errors import ODataError
from app.routes.v4.errors import generic_exception_handler
from app.routes.v4.errors import http_exception_handler
from app.routes.v4.errors import odata_error_handler
from app.routes.v4.errors import request_validation_exception_handler

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../opteryx-core")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../opteryx-catalog"))
)


set_log_name("opteryx.odata")
logger = get_logger()
logger.setLevel(5)

# No default_response_class: nothing that matters is serialized through one.
# Every response with a payload worth the name is already built as bytes and
# returned as a bare Response — query results (read_dataset splices rugo's
# write_jsonl output straight into the body) and both EDMX documents (XML).
# Those bypass the response class entirely: no jsonable_encoder, no render().
# What is left going through it is the service document and /health, which are
# small dicts where the serializer is unmeasurable. ORJSONResponse was carrying
# a deprecation warning and an orjson dependency to accelerate a path that is
# effectively never taken, so both are gone and FastAPI's default stands.


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Prove this instance can serve before it is allowed to accept anything.

    A second line of defence only. `python -m app.preflight` in the Dockerfile
    is what fails a Cloud Run revision; a lifespan that raises can at most kill
    one child of a server that respawns it behind an already-open port. It
    still covers a child respawned long after startup, and running the server
    by hand.

    Deliberately in the lifespan rather than at import: importing this module is
    something tests and tooling do, and it must not require a reachable GCP
    project. Set `HEALTH_DEPENDENCY_CHECKS=false` to run without the probes.
    """
    preflight.run_startup_checks()
    if os.environ.get("WORKER_DIAGNOSTICS") == "1":
        # TEMPORARY - see app/worker_diagnostics.py.
        from app import worker_diagnostics

        worker_diagnostics.start()
    yield


service = FastAPI(title="Opteryx OData v4 Service", lifespan=lifespan)

# Register exception handlers for OData v4 error format
service.add_exception_handler(ODataError, odata_error_handler)
service.add_exception_handler(HTTPException, http_exception_handler)
service.add_exception_handler(RequestValidationError, request_validation_exception_handler)
service.add_exception_handler(Exception, generic_exception_handler)

configure_cors(service)

service.add_middleware(CompressionMiddleware, quality=1, minimum_size=4096)
service.add_middleware(AuditMiddleware)

# include routes
service.include_router(routes_router)

__all__ = ["service"]
