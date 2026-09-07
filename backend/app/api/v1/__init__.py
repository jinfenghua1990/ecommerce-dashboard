from fastapi import APIRouter

from app.api.v1 import (
    alibaba1688_imports,
    alibaba1688_browser,
    auth,
    automation,
    closing,
    consumables,
    dashboard,
    exceptions,
    finance,
    integrations,
    jackyun_files,
    jky_orders,
    jky_web,
    opening,
    procurement_board,
    procurement_chain,
    procurement_workbench,
    profit,
    purchase,
    reconciliation,
    sales_file,
    sales_outbound,
    system,
    tax_invoices,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(system.router)
api_router.include_router(integrations.router)
api_router.include_router(jackyun_files.router)
api_router.include_router(jky_orders.router)
api_router.include_router(sales_file.router)
api_router.include_router(alibaba1688_imports.router)
api_router.include_router(alibaba1688_browser.router)
api_router.include_router(jky_web.router)
api_router.include_router(procurement_chain.router)
api_router.include_router(procurement_workbench.router)
api_router.include_router(procurement_board.router)
api_router.include_router(exceptions.router)
api_router.include_router(finance.router)
api_router.include_router(purchase.router)
api_router.include_router(reconciliation.router)
api_router.include_router(sales_outbound.router)
api_router.include_router(profit.router)
api_router.include_router(dashboard.router)
api_router.include_router(opening.router)
api_router.include_router(closing.router)
api_router.include_router(consumables.router)
api_router.include_router(automation.router)
api_router.include_router(tax_invoices.router)
