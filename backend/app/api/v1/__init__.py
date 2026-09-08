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
    purchase_consistency,
    reconciliation,
    sales_file,
    sales_outbound,
    supply_chain,
    supply_chain_finished_flow,
    supply_chain_material_flow,
    system,
    tax_accounting,
    tax_invoices,
    warehouses,
)
from app.services.platform_purchase_guard import install_platform_purchase_guards
from app.services.procurement_consistency import install_purchase_guards

# V1.6.3：保持历史 API 不变，统一安装采购闭环强校验与跨渠道同号隔离。
install_platform_purchase_guards()
install_purchase_guards()

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
api_router.include_router(purchase_consistency.router)
api_router.include_router(reconciliation.router)
api_router.include_router(sales_outbound.router)
api_router.include_router(profit.router)
api_router.include_router(dashboard.router)
api_router.include_router(supply_chain.router)
api_router.include_router(supply_chain_material_flow.router)
api_router.include_router(supply_chain_finished_flow.router)
api_router.include_router(opening.router)
api_router.include_router(closing.router)
api_router.include_router(consumables.router)
api_router.include_router(warehouses.router)
api_router.include_router(automation.router)
api_router.include_router(tax_invoices.router)
api_router.include_router(tax_accounting.router)
