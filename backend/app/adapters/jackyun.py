import hashlib
import json
import uuid
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

    # 以下同步方法在 Phase 1 拿到真实字段样本后逐个实现 mapping；
    # 在此之前显式未实现，绝不用假数据顶替。
    def sync_products(self) -> None:
        raise NotImplementedError("sync_products 待 Phase 1 真实字段 mapping 后实现")

    def sync_price_lists(self) -> None:
        raise NotImplementedError

    def sync_sales_orders(self) -> None:
        raise NotImplementedError

    def sync_online_orders(self) -> None:
        raise NotImplementedError

    def sync_aftersales(self) -> None:
        raise NotImplementedError

    def sync_inventory(self) -> None:
        raise NotImplementedError

    def sync_warehouses(self) -> None:
        raise NotImplementedError

    def sync_purchase_orders(self) -> None:
        raise NotImplementedError

    def sync_purchase_settlements(self) -> None:
        raise NotImplementedError

    def sync_purchase_returns(self) -> None:
        raise NotImplementedError

    def sync_inbound(self) -> None:
        raise NotImplementedError

    def sync_outbound(self) -> None:
        raise NotImplementedError

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
