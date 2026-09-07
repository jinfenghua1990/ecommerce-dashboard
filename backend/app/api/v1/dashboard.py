from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import dashboard
from app.api.deps import current_actor
from app.core.audit import audit
from app.models.catalog import Product, ProductSku
from app.models.consumable import Consumable
from app.utils.money import to_decimal

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/sales-trend")
def sales_trend(
    days: int = 30,
    start: date | None = Query(None, description="区间起（含），传了优先于 days"),
    end: date | None = Query(None, description="区间止（含当天）"),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.sales_trend(db, days=min(max(days, 7), 365), start=start, end=end)


@router.get("/platform-ranking")
def platform_ranking(
    start: date | None = Query(None),
    end: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.platform_ranking(db, start=start, end=end)


@router.get("/sku-ranking")
def sku_ranking(
    limit: int = 20,
    start: date | None = Query(None),
    end: date | None = Query(None),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return dashboard.sku_ranking(db, limit=min(max(limit, 1), 100), start=start, end=end)


@router.get("/inventory")
def inventory(db: Session = Depends(get_db)) -> dict[str, Any]:
    return dashboard.inventory_summary(db)


@router.get("/inventory/skus")
def inventory_skus(search: str = "", limit: int = 1000,
                   db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.inventory_skus(db, search=search, limit=min(max(limit, 1), 2000))


@router.get("/products")
def products(search: str = "", limit: int = 200,
             db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_products(db, search=search, limit=min(max(limit, 1), 500))


@router.get("/catalog-unified")
def catalog_unified(kind: str = "all", search: str = "", limit: int = 500,
                    db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    """统一货品档案：正品（吉客云）+ 耗材（本平台）合列表；kind=all/goods/consumable。"""
    if kind not in {"all", "goods", "consumable"}:
        kind = "all"
    return dashboard.catalog_unified(db, kind=kind, search=search, limit=min(max(limit, 1), 2000))


class ProductSkuBody(BaseModel):
    sku_id: int | None = None
    jackyun_sku_id: str | None = None
    sku_code: str
    product_type: str | None = None
    sku_name: str = ""
    barcode: str = ""
    unit: str = ""
    sale_price: str | None = None
    default_cost: str | None = None
    cost_mode: str = "fixed"
    cost_tolerance_pct: str = "0.0200"
    tax_code: str = ""
    goods_category: str = ""
    status: str = "active"


@router.post("/products/save")
def save_product(body: ProductSkuBody, request: Request,
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    code = body.sku_code.strip()
    external_id = (body.jackyun_sku_id or code).strip()
    if not code or not external_id:
        raise HTTPException(400, "SKU 编码不能为空")
    if body.cost_mode not in {"fixed", "dynamic"}:
        raise HTTPException(400, "成本方式必须是 fixed 或 dynamic")
    product_type = body.product_type or ("virtual_bundle" if code.upper().startswith("ES") else "single")
    if product_type not in {"single", "bundle", "virtual_bundle"}:
        raise HTTPException(400, "货品类型必须是 single 或 bundle")
    try:
        sale_price = to_decimal(body.sale_price) if body.sale_price not in (None, "") else None
        default_cost = to_decimal(body.default_cost) if body.default_cost not in (None, "") else None
        tolerance = to_decimal(body.cost_tolerance_pct)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "金额或成本容差格式不正确") from exc
    if any(value is not None and value < 0 for value in (sale_price, default_cost)):
        raise HTTPException(400, "售价和成本不能为负数")
    if tolerance < 0 or tolerance > 1:
        raise HTTPException(400, "成本容差必须在 0 到 1 之间")
    sku = db.get(ProductSku, body.sku_id) if body.sku_id else None
    conflict = db.query(ProductSku).filter(ProductSku.jackyun_sku_id == external_id)
    if sku:
        conflict = conflict.filter(ProductSku.id != sku.id)
    elif db.query(ProductSku).filter(ProductSku.sku_code == code).first():
        raise HTTPException(409, "SKU 编码已存在")
    if conflict.first():
        raise HTTPException(409, "吉客云 SKU ID 已存在")
    if sku is None:
        sku = ProductSku(jackyun_sku_id=external_id, sku_code=code)
        db.add(sku)
    sku.jackyun_sku_id = external_id
    sku.sku_code = code
    sku.product_type = product_type
    sku.sku_name = body.sku_name.strip()
    sku.barcode = body.barcode.strip()
    sku.unit = body.unit.strip()
    sku.sale_price = sale_price
    sku.default_cost = default_cost
    sku.cost_mode = body.cost_mode
    sku.cost_tolerance_pct = tolerance
    sku.tax_code = body.tax_code.strip()
    sku.status = body.status.strip() or "active"
    sku.raw = {**(sku.raw or {}), "managedLocally": True}
    # 品类（如 咖啡豆/饼干）存在货品主档 Product.category；仅当本地档案已修改时回写
    if sku.product_id:
        product = db.get(Product, sku.product_id)
        if product is not None and body.goods_category.strip() != (product.category or ""):
            product.category = body.goods_category.strip()
    db.commit()
    audit(db, current_actor(request), "catalog.product_sku.save", "product_skus", sku.id, {"skuCode": sku.sku_code})
    return {"id": sku.id, "skuCode": sku.sku_code, "jackyunSkuId": sku.jackyun_sku_id}


class CostPolicyBody(BaseModel):
    cost_mode: str
    cost_tolerance_pct: str | None = None


@router.post("/products/{sku_id}/cost-policy")
def update_cost_policy(sku_id: int, body: CostPolicyBody, request: Request,
                       db: Session = Depends(get_db)) -> dict[str, str | int]:
    if body.cost_mode not in {"fixed", "dynamic"}:
        raise HTTPException(400, "成本方式必须是 fixed 或 dynamic")
    sku = db.get(ProductSku, sku_id)
    if sku is None:
        raise HTTPException(404, "SKU 不存在")
    if body.cost_tolerance_pct is not None:
        try:
            tolerance = Decimal(body.cost_tolerance_pct)
        except Exception as exc:
            raise HTTPException(400, "成本容差格式不正确") from exc
        if tolerance < 0 or tolerance > 1:
            raise HTTPException(400, "成本容差必须在 0 到 1 之间")
        sku.cost_tolerance_pct = tolerance
    sku.cost_mode = body.cost_mode
    db.commit()
    audit(db, current_actor(request), "catalog.cost_policy.update", "product_skus", sku.id, {"mode": sku.cost_mode})
    return {"id": sku.id, "costMode": sku.cost_mode, "costTolerancePct": str(sku.cost_tolerance_pct)}


class TaxCodeBulkBody(BaseModel):
    items: list[dict[str, Any]]  # [{"kind": "goods"|"consumable", "id": 1}, ...]
    tax_code: str
    overwrite: bool = False      # False=仅填空缺；True=覆盖已有值


@router.post("/catalog/tax-code/bulk")
def bulk_set_tax_code(body: TaxCodeBulkBody, request: Request,
                      db: Session = Depends(get_db)) -> dict[str, Any]:
    """批量设置货品档案（正品 SKU / 耗材）的税收分类编码。"""
    tax_code = body.tax_code.strip()
    if not tax_code:
        raise HTTPException(400, "税务代码不能为空")
    if not (tax_code.isdigit() and len(tax_code) in {10, 19, 21}):
        raise HTTPException(400, "税务代码应为 19 位税收分类编码（兼容旧 10 位简称）")
    if not body.items:
        raise HTTPException(400, "请先勾选要设置的货品")
    updated, skipped, missing = 0, 0, 0
    for item in body.items:
        kind, row_id = item.get("kind"), item.get("id")
        model = ProductSku if kind == "goods" else Consumable if kind == "consumable" else None
        if model is None or not isinstance(row_id, int):
            missing += 1
            continue
        row = db.get(model, row_id)
        if row is None:
            missing += 1
            continue
        if not body.overwrite and (row.tax_code or "").strip():
            skipped += 1
            continue
        row.tax_code = tax_code
        updated += 1
    db.commit()
    audit(db, current_actor(request), "catalog.tax_code.bulk", "catalog", None,
          {"taxCode": tax_code, "updated": updated, "skipped": skipped, "missing": missing})
    return {"updated": updated, "skipped": skipped, "missing": missing}


@router.get("/orders")
def orders(status: str | None = None, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_orders(db, status=status)


@router.get("/aftersales")
def aftersales(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return dashboard.list_aftersales(db)
