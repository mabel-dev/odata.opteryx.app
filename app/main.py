from brotli_asgi import BrotliMiddleware
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from orso.logging import get_logger
from orso.logging import set_log_name

from app.middleware.audit import AuditMiddleware
from app.middleware.cors import configure_cors
from app.routes import router as routes_router

set_log_name("opteryx.odata")
logger = get_logger()
logger.setLevel(5)

service = FastAPI(title="Opteryx OData Service", default_response_class=ORJSONResponse)

configure_cors(service)

service.add_middleware(BrotliMiddleware, mode="generic", quality=4, minimum_size=1024)
service.add_middleware(AuditMiddleware)

# include routes
service.include_router(routes_router)

__all__ = ["service"]
