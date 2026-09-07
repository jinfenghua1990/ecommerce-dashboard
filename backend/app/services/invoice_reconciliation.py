"""发票维度对账：供应商进项发票 ↔ 采购订单 顺序配平（FIFO）。

业务背景：供应商常把多张采购订单合并开一张发票。用户不看"订单关联了哪些发票"，
而是以发票为主体核对：从该供应商最早的订单开始按时间顺序累加金额，
累加到与发票票面金额一致即"对上了"——这张发票覆盖的订单一目了然。

规则：
- 按**归一化供应商名**分组（发票 seller_name ↔ 订单 supplier_name）；
- 组内订单按下单时间升序、发票按开票日期升序；
- 逐张发票从最早的未配平订单开始消耗金额，直到发票票面金额耗尽（容差 ±0.05 元）；
- 订单可被多张发票**渐进消耗**（部分覆盖，余量留给下一张发票），严格 FIFO；
- 金额口径：订单 = COALESCE(paid_amount, order_amount) + adjustment_amount；
  发票 = total_amount（含税票面）；
- 0 金额订单（金额未同步）跳过，不参与配平，另行提示。

纯推导展示，不落库、不修改任何关联数据；每次调用从头重算。
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models.alibaba1688_import import Alibaba1688Order
from app.models.purchase import ExternalPurchaseOrder
from app.models.tax import TaxInvoice, TaxInvoiceLink

TOLERANCE = Decimal("0.05")

_TRANS = str.maketrans({"（": "(", "）": ")", "　": "", "：": ":", "，": ","})


def normalize_supplier(name: str | None) -> str:
    """供应商名归一化：去空白、全角括号/标点转半角、大写。"""
    return re.sub(r"\s+", "", (name or "").translate(_TRANS)).upper()


def _dec(value: Decimal | float | int | None) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal("0")


def _matches(name_norm: str, target_norm: str) -> bool:
    """供应商名宽松匹配：全等，或一方是另一方的前缀（处理"河北鸿鲲食品" vs
    "河北鸿鲲食品有限公司"这类简称/全称差异）。"""
    if not name_norm or not target_norm:
        return False
    return name_norm == target_norm or name_norm.startswith(target_norm) or target_norm.startswith(name_norm)


def _manual_link_map(db: Session, inv_ids: list[int], po_rows: list) -> dict[int, list[dict[str, Any]]]:
    """人工微调映射：invoice_id -> [{linkId, poId}]。

    只取人工建立的关联（tax_invoice_links.match_method=manual；rejected 已解除，
    自动建议不在此列，避免覆盖用户意图）。1688 源单关联按业务单号换算到对应
    的工作流 PO；换算不到（订单已删除/不属于当前供应商范围）的跳过。
    """
    if not inv_ids:
        return {}
    links = (
        db.query(TaxInvoiceLink)
        .filter(TaxInvoiceLink.invoice_id.in_(inv_ids), TaxInvoiceLink.match_method == "manual")
        .all()
    )
    if not links:
        return {}
    need_1688 = {lnk.target_id for lnk in links if lnk.target_type == "alibaba1688_order"}
    no_by_1688: dict[int, str] = {}
    if need_1688:
        rows = (
            db.query(Alibaba1688Order.id, Alibaba1688Order.external_order_id)
            .filter(Alibaba1688Order.id.in_(need_1688))
            .all()
        )
        no_by_1688 = {r.id: r.external_order_id for r in rows}
    po_id_by_no = {r.external_order_id: r.id for r in po_rows}
    result: dict[int, list[dict[str, Any]]] = {}
    for lnk in links:
        if lnk.target_type == "external_purchase_order":
            po_id: int | None = lnk.target_id
        elif lnk.target_type == "alibaba1688_order":
            no = no_by_1688.get(lnk.target_id)
            po_id = po_id_by_no.get(no) if no else None
        else:
            continue
        if po_id is None:
            continue
        result.setdefault(lnk.invoice_id, []).append({"linkId": lnk.id, "poId": po_id})
    return result


def reconcile(db: Session, supplier: str | None = None) -> dict[str, Any]:
    """supplier 传入时只输出该供应商的分组（供供应商画像的发票匹配清单使用）。"""
    target_norm = normalize_supplier(supplier) if supplier else ""
    po_rows = (
        db.query(
            ExternalPurchaseOrder.id,
            ExternalPurchaseOrder.external_order_id,
            ExternalPurchaseOrder.platform,
            ExternalPurchaseOrder.supplier_name,
            ExternalPurchaseOrder.ordered_at,
            ExternalPurchaseOrder.order_amount,
            ExternalPurchaseOrder.paid_amount,
            ExternalPurchaseOrder.adjustment_amount,
        )
        .order_by(ExternalPurchaseOrder.ordered_at.nulls_last(), ExternalPurchaseOrder.id)
        .all()
    )
    inv_rows = (
        db.query(TaxInvoice)
        .filter(TaxInvoice.direction == "input", TaxInvoice.status == "issued")
        .order_by(TaxInvoice.issue_date.nulls_last(), TaxInvoice.id)
        .all()
    )
    if supplier:
        # 只保留与目标供应商宽松匹配的订单/发票，缩小配平范围
        po_rows = [r for r in po_rows if _matches(normalize_supplier(r.supplier_name), target_norm)]
        inv_rows = [v for v in inv_rows if _matches(normalize_supplier(v.seller_name), target_norm)]

    # 按归一化供应商分组；订单池用 list，队首订单可被多张发票渐进消耗；
    # po_all 保存同一批 dict 引用（含 0 金额单），配平结束后 remaining 即该单未配平余量
    po_pool: dict[str, list[dict[str, Any]]] = {}
    po_all: dict[str, list[dict[str, Any]]] = {}
    po_entry_by_id: dict[int, dict[str, Any]] = {}
    po_meta: dict[str, dict[str, Any]] = {}
    skipped_zero = 0
    for r in po_rows:
        # 供应商模式下统一挂到目标分组：宽松匹配进来的简称/全称归并到一起配平
        norm = target_norm or normalize_supplier(r.supplier_name)
        if not norm:
            continue
        meta = po_meta.setdefault(norm, {"supplier": r.supplier_name, "orderTotal": Decimal("0"), "orderCount": 0})
        amount = (_dec(r.paid_amount) if r.paid_amount is not None else _dec(r.order_amount)) + _dec(r.adjustment_amount)
        meta["orderTotal"] += amount
        meta["orderCount"] += 1
        order_entry = {
            "orderId": r.id, "orderNo": r.external_order_id, "platform": r.platform,
            "date": r.ordered_at.strftime("%Y-%m-%d") if r.ordered_at else None,
            "orderAmount": float(amount), "remaining": amount,
        }
        po_all.setdefault(norm, []).append(order_entry)
        po_entry_by_id[r.id] = order_entry
        if amount <= 0:
            skipped_zero += 1
            continue
        po_pool.setdefault(norm, []).append(order_entry)

    # 人工微调优先：有 manual 关联的发票按关联配平，其余继续 FIFO 自动推导
    manual_map = _manual_link_map(db, [v.id for v in inv_rows], po_rows)

    # 发票按供应商分组（保持时间升序）；供应商模式下归并到目标分组
    inv_by_supplier: dict[str, list[TaxInvoice]] = {}
    for inv in inv_rows:
        norm = target_norm or normalize_supplier(inv.seller_name)
        if norm:
            inv_by_supplier.setdefault(norm, []).append(inv)

    suppliers_out: list[dict[str, Any]] = []
    expense_sellers: list[dict[str, Any]] = []
    for norm, invoices in inv_by_supplier.items():
        pool = po_pool.get(norm, [])
        meta = po_meta.get(norm)
        month_buckets: dict[str, list[dict[str, Any]]] = {}
        inv_total = Decimal("0")
        matched_total = Decimal("0")
        for inv in invoices:
            amount = _dec(inv.total_amount)
            inv_total += amount
            manual_links = manual_map.get(inv.id, [])
            covered: list[dict[str, Any]] = []
            consumed_total = Decimal("0")
            remaining_need = amount
            if manual_links:
                # 手工关联 = 用户意图，按关联逐单消耗（本票不再自动 FIFO）
                for lnk in manual_links:
                    order = po_entry_by_id.get(lnk["poId"])
                    if order is None:
                        continue
                    take = min(max(order["remaining"], Decimal("0")), remaining_need)
                    covered.append({
                        "orderId": order["orderId"], "orderNo": order["orderNo"],
                        "platform": order["platform"], "date": order["date"],
                        "orderAmount": order["orderAmount"],
                        "consumed": float(take),
                        "partial": take < order["remaining"] - TOLERANCE,
                        "source": "manual", "linkId": lnk["linkId"],
                    })
                    order["remaining"] -= take
                    remaining_need -= take
                    consumed_total += take
                    if order["remaining"] <= TOLERANCE:
                        # 吃满的订单从所有 FIFO 池移除，避免自动推导重复消耗
                        for p in po_pool.values():
                            p[:] = [o for o in p if o["orderId"] != order["orderId"]]
            else:
                while pool and remaining_need > TOLERANCE:
                    order = pool[0]
                    take = min(order["remaining"], remaining_need)
                    if take <= 0:  # 已被手工关联吃满但尚未出队的订单，跳过
                        pool.pop(0)
                        continue
                    covered.append({
                        "orderId": order["orderId"], "orderNo": order["orderNo"],
                        "platform": order["platform"], "date": order["date"],
                        "orderAmount": order["orderAmount"],
                        "consumed": float(take),
                        "partial": take < order["remaining"] - TOLERANCE,
                        "source": "auto",
                    })
                    order["remaining"] -= take
                    remaining_need -= take
                    consumed_total += take
                    if order["remaining"] <= TOLERANCE:
                        pool.pop(0)
            diff = consumed_total - amount  # <0 仅当订单耗尽（short）
            if diff >= -TOLERANCE:
                matched_total += amount
            month = inv.issue_date.strftime("%Y-%m") if inv.issue_date else "未知月份"
            month_buckets.setdefault(month, []).append({
                "invoiceId": inv.id,
                "invoiceNo": inv.invoice_number or inv.invoice_code,
                "issueDate": inv.issue_date.strftime("%Y-%m-%d") if inv.issue_date else None,
                "seller": inv.seller_name,
                "amount": float(amount),
                "manualLinked": bool(manual_links),
                "covered": covered,
                "coveredTotal": float(consumed_total),
                "diff": float(diff),
                "status": "matched" if diff >= -TOLERANCE else "short",
            })

        entry = {
            "supplier": (meta or {}).get("supplier") or invoices[0].seller_name,
            "supplierNorm": norm,
            "hasOrders": bool(meta),
            "orderCount": (meta or {}).get("orderCount", 0),
            "orderTotal": float((meta or {}).get("orderTotal", Decimal("0"))),
            "invoiceCount": len(invoices),
            "invoiceTotal": float(inv_total),
            "matchedTotal": float(matched_total),
            "remainingOrders": len(pool),
            "remainingOrderTotal": float(sum(o["remaining"] for o in pool)),
            # 待开票订单（未被任何发票配平的部分）：找供应商开票时直接引用这些订单号
            "pendingOrders": [
                {
                    "orderId": o["orderId"], "orderNo": o["orderNo"], "platform": o["platform"],
                    "date": o["date"], "orderAmount": o["orderAmount"],
                    "remaining": float(o["remaining"]),
                    "partial": o["remaining"] < _dec(o["orderAmount"]) - TOLERANCE,
                }
                for o in pool
            ],
            # 全部订单及未配平余量（FIFO 顺序）：remaining≈orderAmount 未匹配，
            # 0<remaining<orderAmount 部分匹配，remaining≤容差 已配平；金额≤0 未同步
            "orders": [
                {
                    "orderId": o["orderId"], "orderNo": o["orderNo"], "platform": o["platform"],
                    "date": o["date"], "orderAmount": float(_dec(o["orderAmount"])),
                    "remaining": float(o["remaining"]),
                }
                for o in po_all.get(norm, [])
            ],
            "months": [
                {"month": m, "invoices": month_buckets[m]}
                for m in sorted(month_buckets)
            ],
        }
        if meta:
            suppliers_out.append(entry)
        elif len(invoices) <= 12:  # 无订单的费用类卖方（航司/酒店等），折叠展示
            expense_sellers.append({
                "seller": invoices[0].seller_name, "invoiceCount": len(invoices),
                "invoiceTotal": float(inv_total),
            })

    # （待开票订单已并入各供应商块的 pendingOrders，不再单独输出缺票清单）

    suppliers_out.sort(key=lambda e: -e["orderTotal"])
    expense_sellers.sort(key=lambda e: -e["invoiceTotal"])

    return {
        "tolerance": float(TOLERANCE),
        "skippedZeroOrders": skipped_zero,
        "suppliers": suppliers_out,
        "expenseSellers": expense_sellers,
    }
