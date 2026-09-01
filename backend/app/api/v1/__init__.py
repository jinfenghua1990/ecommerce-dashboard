from fastapi import APIRouter

from app.api.v1 import exceptions, integrations, system

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(integrations.router)
api_router.include_router(exceptions.router)
