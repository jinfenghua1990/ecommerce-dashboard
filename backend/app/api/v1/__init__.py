from fastapi import APIRouter

from app.api.v1 import (
    automation,
    closing,
    dashboard,
    exceptions,
    finance,
    integrations,
    opening,
    profit,
    purchase,
    reconciliation,
    system,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(system.router)
api_router.include_router(integrations.router)
api_router.include_router(exceptions.router)
api_router.include_router(finance.router)
api_router.include_router(purchase.router)
api_router.include_router(reconciliation.router)
api_router.include_router(profit.router)
api_router.include_router(dashboard.router)
api_router.include_router(opening.router)
api_router.include_router(closing.router)
api_router.include_router(automation.router)
