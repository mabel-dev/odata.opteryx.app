# routes package
from fastapi import APIRouter

from .health import router as health_router
from .v4 import interface_router as v4_interface_router
from .v4 import metadata_router as v4_metadata_router
from .v4 import service_document_router as v4_service_document_router

router = APIRouter()
router.include_router(health_router)
router.include_router(v4_service_document_router)
router.include_router(v4_metadata_router)
router.include_router(v4_interface_router)

__all__ = ["router"]
