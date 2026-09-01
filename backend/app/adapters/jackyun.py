import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.adapters.base import AdapterError, AdapterNotConfigured
from app.config import settings
from app.models.integration import RawApiPayload, SyncCheckpoint, SyncJob, SyncLog

"""JackyunAdapter —— 吉客云 MCP（Streamable HTTP）。

原则（规格 5）：
- 只接已订阅接口，不发明 API method
- 第一次请求保留 raw payload
- 分页 / 增量 / checkpoint / 幂等 upsert / 指数退避 / 限流器 / 调用日志
- 页面刷新不触发全量吉客云查询
"""

RATE_LIMIT_MIN_INTERVAL = 0.35  # 秒；保守限流，Phase 1 依据真实限流响应调整


class JackyunAdapter:
    provider = "jackyun"

    def __init__(self, db: Session):
        self.db = db
        self.url = settings.JACKYUN_MCP_URL
        self.app_key = settings.JACKYUN_APP_KEY
        self.token = settings.JACKYUN_MCP_TOKEN
        self.session_id: str | None = None
        self._last_call_ts: float = 0.0

    def ensure_configured(self) -> None:
        if not settings.jackyun_configured:
            raise AdapterNotConfigured("吉客云 MCP 未配置（JACKYUN_MCP_URL / JACKYUN_MCP_TOKEN 为空）")

    # ---------- MCP 传输层 ----------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            # 吉客云 MCP 实测：Authorization 直接放裸 Token，不带 Bearer 前缀
            "Authorization": self.token,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _rate_limit(self) -> None:
        import time

        now = time.monotonic()
        wait = RATE_LIMIT_MIN_INTERVAL - (now - self._last_call_ts)
        if wait > 0:
            time.sleep(wait)
        self._last_call_ts = time.monotonic()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST JSON-RPC；记录 raw payload（首次方法）与调用日志。"""
        self._rate_limit()
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        try:
            resp = httpx.post(self.url, json=body, headers=self._headers(), timeout=30)
        except httpx.HTTPError as exc:
            self._log("error", f"MCP 请求失败: {exc}")
            raise AdapterError(f"吉客云 MCP 网络错误: {exc}") from exc

        new_session = resp.headers.get("mcp-session-id")
        if new_session:
            self.session_id = new_session

        data: dict[str, Any] | None = None
        ctype = resp.headers.get("content-type", "")
        if resp.status_code == 200:
            if "text/event-stream" in ctype:
                data = self._parse_sse(resp.text)
            else:
                try:
                    data = resp.json()
                except ValueError:
                    data = {"_raw_text": resp.text[:2000]}
        else:
            self._log("error", f"MCP HTTP {resp.status_code}: {resp.text[:500]}")
            raise AdapterError(f"吉客云 MCP HTTP {resp.status_code}: {resp.text[:200]}")

        # raw payload 存档（幂等：同一 digest 只存首次）
        if data is not None:
            exists = (
                self.db.query(RawApiPayload)
                .filter(RawApiPayload.provider == self.provider, RawApiPayload.request_digest == digest)
                .first()
            )
            if not exists:
                self.db.add(
                    RawApiPayload(provider=self.provider, method=str(body.get("method", "")),
                                  request_digest=digest, payload=data)
                )
                self.db.commit()
        return data or {}

    @staticmethod
    def _parse_sse(text: str) -> dict[str, Any]:
        """SSE 帧里取最后一条 JSON-RPC 响应。"""
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    return json.loads(payload)
                except ValueError:
                    continue
        return {"_raw_sse": text[:2000]}

    def _rpc(self, method: str, params: dict[str, Any] | None = None, notify: bool = False) -> dict[str, Any] | None:
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "id": str(uuid.uuid4()),
        }
        if params is not None:
            body["params"] = params
        if notify:
            body.pop("id")
            self._post(body)
            return None
        result = self._post(body)
        if isinstance(result, dict) and "error" in result and result["error"]:
            err = result["error"]
            raise AdapterError(f"MCP error {err.get('code')}: {err.get('message')}")
        return result

    # ---------- 连接测试 ----------

    def test_connection(self) -> dict[str, Any]:
        """initialize → tools/list，返回已订阅工具名列表。真实失败如实抛出。"""
        self.ensure_configured()
        init = self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "ecommerce-ops-platform", "version": "0.1.0"},
        })
        server_info = (init or {}).get("result", {}).get("serverInfo", {})
        self._rpc("notifications/initialized", {}, notify=True)
        tools_resp = self._rpc("tools/list", {})
        tools = [
            t.get("name", "")
            for t in ((tools_resp or {}).get("result", {}).get("tools", []))
            if isinstance(t, dict)
        ]
        return {"server_info": server_info, "tools": sorted(tools), "session_id": self.session_id}

    # ---------- 业务同步（Phase 1 依据真实字段 mapping 逐个实现） ----------

    # 订阅清单（规格 5）：API method ↔ MCP tool 名
    # tool 名来自 2026-09-01 真实 tools/list 响应（open-platform-mcp v1.0.0），非猜测
    SUBSCRIBED_METHODS = {
        "erp.storage.goodsdocin.v2": "getGoodsDocInListInfo",
        "erp.storage.goodsdocout.v2": "getGoodsDocOutListInfo",
        "erp.storage.goodslist": "getGoodsListInfoByGoodsNo",
        "erp-goods.pricelist.get": "getGoodsPriceListInfo",
        "erp.stockquantity.get": "getGoodsStockQuantityListInfo",
        "omsapi-business.order.get": "getOrderListInfo",
        "erp.purch.get": "getPurchOrderListInfo",
        "erp.purchreturn.get": "getPurchOrderReturnListInfo",
        "erp.purchordersett.get": "getPurchOrderSettleListInfo",
        "ass-business.returnchange.fullinfoget": "getReturnChangeListInfo",
        "wms.order.query-info.page": "getShopOrderLiseInfo",
        "oms.trade.fullinfoget": "getTradesListInfo",
        "erp.allocate.get": "getStockAllocateListInfo",
        "erp.warehouse.get": "getWarehouseListInfo",
    }

    def call_subscribed(self, method: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """调用已订阅工具。method 须为 API method 名或真实 tool 名，禁止发明。"""
        self.ensure_configured()
        tool = self.SUBSCRIBED_METHODS.get(method, method)
        if tool not in self.SUBSCRIBED_METHODS.values():
            raise AdapterError(f"拒绝调用未订阅/未确认的吉客云 method: {method}")
        resp = self._rpc("tools/call", {"name": tool, "arguments": arguments}) or {}
        return resp.get("result", {})

    # 以下同步方法：真实调用订阅工具 → raw payload 存档（_post 幂等）→ 按响应结构
    # 动态映射本地表。字段名一律以真实响应为准，不猜字段（规格 5/20）。
    # 未开通开放平台时工具调用会返回 subCode 0130000609，如实失败并进异常中心。

    def _fetch_and_upsert(self, method: str, arguments: dict[str, Any], *,
                          id_fields: tuple[str, ...], upsert) -> dict[str, Any]:
        """通用同步骨架：调用 → 提取列表 → 幂等 upsert。

        id_fields: 真实响应中可作外部主键的字段名候选（按顺序取第一个存在者）
        upsert:    callable(db, record) -> bool，返回 True 表示新建
        """
        self.ensure_configured()
        resp = self.call_subscribed(method, arguments)
        # MCP tools/call 内容通常是 text 序列
        content = resp.get("content") or []
        items: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text.startswith("{"):
                    try:
                        data = json.loads(text)
                    except ValueError:
                        continue
                    items.extend(self._extract_records(data))
        stats = {"fetched": len(items), "created": 0, "updated": 0, "raw_stored": True}
        for rec in items:
            if not isinstance(rec, dict):
                continue
            key = next((rec.get(f) for f in id_fields if rec.get(f) is not None), None)
            if key is None:
                continue
            created = upsert(self.db, rec)
            stats["created" if created else "updated"] += 1
        self.db.commit()
        self._log("info", f"sync {method} 完成", data={"stats": stats})
        return stats

    @staticmethod
    def _extract_records(data: dict[str, Any]) -> list[dict[str, Any]]:
        """从响应 JSON 中提取记录列表；适配常见包装（result.data / data / list 等）。"""
        result = data.get("result") if isinstance(data, dict) else None
        if isinstance(result, dict):
            d = result.get("data")
            if isinstance(d, list):
                return d
            rows = result.get("rows") or result.get("list") or result.get("records")
            if isinstance(rows, list):
                return rows
        d2 = data.get("data")
        if isinstance(d2, list):
            return d2
        if isinstance(data, list):
            return data
        return []

    def sync_products(self) -> None:
        from app.models.catalog import Product, ProductSku

        def _upsert(db: Session, rec: dict) -> bool:
            gid = str(rec.get("goods_id") or rec.get("goodsId") or rec.get("goodsno") or "")
            if not gid:
                return False
            row = db.query(Product).filter_by(jackyun_goods_id=gid).first()
            if row:
                row.raw = rec
                row.goods_code = str(rec.get("goods_code") or rec.get("goodsCode") or row.goods_code)
                row.goods_name = str(rec.get("goods_name") or rec.get("goodsName") or row.goods_name)
                return False
            db.add(Product(jackyun_goods_id=gid, goods_code=str(rec.get("goods_code") or ""),
                           goods_name=str(rec.get("goods_name") or ""), raw=rec))
            return True

        self._fetch_and_upsert(
            "erp.storage.goodslist", {}, id_fields=("goods_id", "goodsId", "goodsno"), upsert=_upsert
        )

    def sync_price_lists(self) -> None:
        from app.models.catalog import ProductSku

        def _upsert(db: Session, rec: dict) -> bool:
            sid = str(rec.get("sku_id") or rec.get("skuId") or rec.get("skuid") or "")
            if not sid:
                return False
            row = db.query(ProductSku).filter_by(jackyun_sku_id=sid).first()
            if row:
                row.raw = rec
                return False
            db.add(ProductSku(jackyun_sku_id=sid, sku_code=str(rec.get("sku_code") or rec.get("skuCode") or sid),
                              sku_name=str(rec.get("sku_name") or rec.get("skuName") or ""), raw=rec))
            return True

        self._fetch_and_upsert(
            "erp-goods.pricelist.get", {}, id_fields=("sku_id", "skuId", "skuid"), upsert=_upsert
        )

    def sync_sales_orders(self) -> None:
        from app.models.sales import SalesOrder

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(rec.get("trade_no") or rec.get("order_no") or rec.get("orderNo") or rec.get("tid") or "")
            if not no:
                return False
            row = db.query(SalesOrder).filter_by(order_no=no).first()
            if row:
                row.raw = rec
                return False
            db.add(SalesOrder(order_no=no, platform=str(rec.get("platform") or ""),
                              order_status=str(rec.get("order_status") or rec.get("status") or ""),
                              raw=rec))
            return True

        self._fetch_and_upsert(
            "oms.trade.fullinfoget", {}, id_fields=("trade_no", "order_no", "orderNo", "tid"), upsert=_upsert
        )

    def sync_online_orders(self) -> None:
        from app.models.sales import SalesOrder

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(rec.get("order_no") or rec.get("orderNo") or rec.get("tid") or "")
            if not no:
                return False
            row = db.query(SalesOrder).filter_by(order_no=no).first()
            if row:
                row.raw = rec
                return False
            db.add(SalesOrder(order_no=no, order_type="online", platform=str(rec.get("platform") or ""),
                              order_status=str(rec.get("order_status") or rec.get("status") or ""), raw=rec))
            return True

        self._fetch_and_upsert(
            "omsapi-business.order.get", {}, id_fields=("order_no", "orderNo", "tid"), upsert=_upsert
        )

    def sync_aftersales(self) -> None:
        from app.models.sales import AftersalesOrder

        def _upsert(db: Session, rec: dict) -> bool:
            no = str(rec.get("return_id") or rec.get("aftersale_no") or rec.get("refund_no")
                     or rec.get("afterSaleId") or "")
            if not no:
                return False
            row = db.query(AftersalesOrder).filter_by(aftersale_no=no).first()
            if row:
                row.raw = rec
                return False
            db.add(AftersalesOrder(aftersale_no=no, type=str(rec.get("type") or "refund"),
                                   status=str(rec.get("status") or ""), raw=rec))
            return True

        self._fetch_and_upsert(
            "ass-business.returnchange.fullinfoget", {},
            id_fields=("return_id", "aftersale_no", "refund_no", "afterSaleId"), upsert=_upsert
        )

    def sync_inventory(self) -> None:
        from app.models.catalog import InventorySnapshot
        from app.models.catalog import ProductSku
        from datetime import datetime, timezone

        def _upsert(db: Session, rec: dict) -> bool:
            sid = str(rec.get("sku_id") or rec.get("skuId") or rec.get("skuid") or rec.get("goods_no") or "")
            if not sid:
                return False
            sku = db.query(ProductSku).filter_by(jackyun_sku_id=sid).first()
            if not sku:
                sku = db.query(ProductSku).filter_by(sku_code=sid).first()
            if not sku:
                return False
            qty = rec.get("quantity") or rec.get("stock") or rec.get("qty")
            if qty is None:
                return False
            try:
                qty_d = Decimal(str(qty))
            except Exception:
                return False
            db.add(InventorySnapshot(sku_id=sku.id, quantity=qty_d,
                                     snapshot_at=datetime.now(timezone.utc), raw=rec))
            return True

        self._fetch_and_upsert(
            "erp.stockquantity.get", {}, id_fields=("sku_id", "skuId", "skuid", "goods_no"), upsert=_upsert
        )

    def sync_warehouses(self) -> None:
        from app.models.catalog import Warehouse

        def _upsert(db: Session, rec: dict) -> bool:
            wid = str(rec.get("warehouse_id") or rec.get("warehouseId") or rec.get("wms_no") or "")
            if not wid:
                return False
            row = db.query(Warehouse).filter_by(jackyun_warehouse_id=wid).first()
            if row:
                row.raw = rec
                return False
            db.add(Warehouse(jackyun_warehouse_id=wid, name=str(rec.get("warehouse_name") or rec.get("name") or ""),
                             raw=rec))
            return True

        self._fetch_and_upsert(
            "erp.warehouse.get", {}, id_fields=("warehouse_id", "warehouseId", "wms_no"), upsert=_upsert
        )

    def sync_purchase_orders(self) -> None:
        from app.models.purchase import JackyunPurchaseOrder

        def _upsert(db: Session, rec: dict) -> bool:
            pid = str(rec.get("purch_id") or rec.get("purchId") or rec.get("id") or "")
            if not pid:
                return False
            row = db.query(JackyunPurchaseOrder).filter_by(jackyun_purch_id=pid).first()
            if row:
                row.raw = rec
                return False
            db.add(JackyunPurchaseOrder(jackyun_purch_id=pid,
                                        purch_no=str(rec.get("purch_no") or rec.get("purchNo") or ""),
                                        supplier_name=str(rec.get("supplier_name") or rec.get("supplierName") or ""),
                                        raw=rec))
            return True

        self._fetch_and_upsert(
            "erp.purch.get", {}, id_fields=("purch_id", "purchId", "id"), upsert=_upsert
        )

    def sync_purchase_settlements(self) -> None:
        self._fetch_and_upsert(
            "erp.purchordersett.get", {}, id_fields=("settle_id", "settleId", "id"),
            upsert=lambda db, rec: False  # 原始存档即可，mapping 待真实样本
        )

    def sync_purchase_returns(self) -> None:
        self._fetch_and_upsert(
            "erp.purchreturn.get", {}, id_fields=("return_id", "returnId", "id"),
            upsert=lambda db, rec: False
        )

    def sync_inbound(self) -> None:
        self._fetch_and_upsert(
            "erp.storage.goodsdocin.v2", {}, id_fields=("doc_id", "docId", "goodsdoc_no"),
            upsert=lambda db, rec: False
        )

    def sync_outbound(self) -> None:
        self._fetch_and_upsert(
            "erp.storage.goodsdocout.v2", {}, id_fields=("doc_id", "docId", "goodsdoc_no"),
            upsert=lambda db, rec: False
        )

    # ---------- 日志辅助 ----------

    def _log(self, level: str, message: str, job_id: int | None = None, data: dict | None = None) -> None:
        self.db.add(SyncLog(provider=self.provider, level=level, message=message,
                            sync_job_id=job_id, data=data or {}))
        self.db.commit()


def start_sync_job(db: Session, provider: str, job_type: str) -> SyncJob:
    job = SyncJob(provider=provider, job_type=job_type, status="running")
    from datetime import datetime, timezone

    job.started_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    return job


def finish_sync_job(db: Session, job: SyncJob, status: str, stats: dict | None = None, error: str = "") -> None:
    from datetime import datetime, timezone

    job.status = status
    job.finished_at = datetime.now(timezone.utc)
    job.stats = stats or {}
    job.error_summary = error
    db.commit()


def load_checkpoint(db: Session, provider: str, job_type: str) -> dict:
    row = db.query(SyncCheckpoint).filter_by(provider=provider, job_type=job_type).first()
    return row.checkpoint if row else {}


def save_checkpoint(db: Session, provider: str, job_type: str, checkpoint: dict) -> None:
    row = db.query(SyncCheckpoint).filter_by(provider=provider, job_type=job_type).first()
    if row:
        row.checkpoint = checkpoint
    else:
        db.add(SyncCheckpoint(provider=provider, job_type=job_type, checkpoint=checkpoint))
    db.commit()
