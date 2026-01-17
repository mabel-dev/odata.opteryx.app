"""OData v4 API routes."""

from app.routes.v4.interface import router as interface_router
from app.routes.v4.metadata import router as metadata_router
from app.routes.v4.service_document import router as service_document_router

__all__ = ["interface_router", "metadata_router", "service_document_router"]
