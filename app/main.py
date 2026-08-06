import os
import sys

from fastapi import FastAPI
from fastapi import HTTPException
from app.logging import get_logger
from app.logging import set_log_name

from app.middleware.audit import AuditMiddleware
from app.middleware.compression import CompressionMiddleware
from app.middleware.cors import configure_cors
from app.routes import router as routes_router
from app.routes.v4.errors import ODataError
from app.routes.v4.errors import generic_exception_handler
from app.routes.v4.errors import http_exception_handler
from app.routes.v4.errors import odata_error_handler

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../opteryx-core")))
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../pyiceberg-firestore-gcs"))
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
service = FastAPI(title="Opteryx OData v4 Service")

# Register exception handlers for OData v4 error format
service.add_exception_handler(ODataError, odata_error_handler)
service.add_exception_handler(HTTPException, http_exception_handler)
service.add_exception_handler(Exception, generic_exception_handler)

configure_cors(service)

service.add_middleware(CompressionMiddleware, quality=1, minimum_size=4096)
service.add_middleware(AuditMiddleware)

# include routes
service.include_router(routes_router)

__all__ = ["service"]
