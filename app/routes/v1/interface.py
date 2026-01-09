"""OData API endpoints for the OData service.

This module provides OData protocol endpoints for querying Opteryx data.
"""

from fastapi import APIRouter
from fastapi import Depends

from app.auth.deps import require_bearer_token

router = APIRouter(
    prefix="/api/v1", tags=["OData"], dependencies=[Depends(require_bearer_token)]
)

# OData endpoints will be implemented here

__all__ = ["router"]
