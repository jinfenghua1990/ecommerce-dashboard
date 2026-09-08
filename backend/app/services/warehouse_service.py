from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.warehouse import Warehouse

WAREHOUSE_TYPES = {"factory", "b2c", "other"}
PURPOSES = {"goods", "consumable", "both"}
STATUSES = {"active", "inactive"}


def serialize(row: Warehouse) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "name": row.name,
        "warehouseType": row.warehouse_type,
        "purpose": row.purpose,
        "isSellable": bool(row.is_sellable),
        "status": row.status,
        "note": row.note or "",
        "createdAt": row.created_at.isoformat() if row.created_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_warehouses(db: Session, *, include_inactive: bool = True) -> list[dict]:
    query = db.query(Warehouse)
    if not include_inactive:
        query = query.filter(Warehouse.status == "active")
    rows = query.order_by(Warehouse.status.desc(), Warehouse.id).all()
    return [serialize(row) for row in rows]


def _normalize(
    *, code: str, name: str, warehouse_type: str, purpose: str,
    is_sellable: bool, status: str, note: str,
) -> dict:
    code = code.strip().upper()
    name = name.strip()
    warehouse_type = warehouse_type.strip().lower()
    purpose = purpose.strip().lower()
    status = status.strip().lower()
    note = note.strip()
    if not code:
        raise ValueError("仓库编码不能为空")
    if not name:
        raise ValueError("仓库名称不能为空")
    if warehouse_type not in WAREHOUSE_TYPES:
        raise ValueError("仓库类型必须是 factory / b2c / other")
    if purpose not in PURPOSES:
        raise ValueError("仓库用途必须是 goods / consumable / both")
    if status not in STATUSES:
        raise ValueError("仓库状态必须是 active / inactive")
    return {
        "code": code,
        "name": name,
        "warehouse_type": warehouse_type,
        "purpose": purpose,
        "is_sellable": bool(is_sellable),
        "status": status,
        "note": note,
    }


def create_warehouse(
    db: Session, *, code: str, name: str, warehouse_type: str = "other",
    purpose: str = "both", is_sellable: bool = False,
    status: str = "active", note: str = "",
) -> Warehouse:
    values = _normalize(
        code=code, name=name, warehouse_type=warehouse_type, purpose=purpose,
        is_sellable=is_sellable, status=status, note=note,
    )
    if db.query(Warehouse).filter_by(code=values["code"]).first():
        raise ValueError("仓库编码已存在")
    row = Warehouse(**values)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_warehouse(
    db: Session, warehouse_id: int, *, code: str | None = None,
    name: str | None = None, warehouse_type: str | None = None,
    purpose: str | None = None, is_sellable: bool | None = None,
    status: str | None = None, note: str | None = None,
) -> Warehouse:
    row = db.get(Warehouse, warehouse_id)
    if row is None:
        raise ValueError("仓库不存在")
    values = _normalize(
        code=row.code if code is None else code,
        name=row.name if name is None else name,
        warehouse_type=row.warehouse_type if warehouse_type is None else warehouse_type,
        purpose=row.purpose if purpose is None else purpose,
        is_sellable=row.is_sellable if is_sellable is None else is_sellable,
        status=row.status if status is None else status,
        note=row.note if note is None else note,
    )
    duplicate = db.query(Warehouse).filter(Warehouse.code == values["code"], Warehouse.id != row.id).first()
    if duplicate:
        raise ValueError("仓库编码已存在")
    for key, value in values.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def get_active(db: Session, warehouse_id: int) -> Warehouse:
    row = db.get(Warehouse, warehouse_id)
    if row is None or row.status != "active":
        raise ValueError("所选仓库不存在或已停用")
    return row


def default_for_type(db: Session, warehouse_type: str) -> Warehouse | None:
    return (
        db.query(Warehouse)
        .filter(Warehouse.status == "active", Warehouse.warehouse_type == warehouse_type)
        .order_by(Warehouse.id)
        .first()
    )


def legacy_location(row: Warehouse) -> str:
    """兼容旧耗材双库存字段：工厂仓→factory，其余仓暂映射 own。"""
    return "factory" if row.warehouse_type == "factory" else "own"
