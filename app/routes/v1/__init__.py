from fastapi import APIRouter

from .interface import router as interface_router
from .metadata import router as metadata_router

router = APIRouter()
router.include_router(interface_router)
router.include_router(metadata_router)

__all__ = ["router"]
