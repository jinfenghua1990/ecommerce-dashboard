"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type ProductionItem = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  unit: string;
  quantity: string;
  completedQty: string;
  remainingProductionQty: string;
  factoryReadyQty: string;
  shippedQty: string;
  transitQty: string;
  arrivedQty: string;
  pendingInboundQty: string;
  inboundQty: string;
};

type ProductionOrder = {
  id: number;
  orderNo: string;
  factoryName: string;
  status: string;
  expectedDeliveryDate: string | null;
  note: string;
  items: ProductionItem[];
};

type FlowRow = {
  order: ProductionOrder;
  item: ProductionItem;
};

type RowDraft = {
  completeQty: string;
  shipQty: string;
  arriveQty: string;
  carrier: string;
  trackingNo: string;
};

const STATUS: Record<string, string> = {
  planned: "计划中",
  confirmed: "已确认",
  producing: "生产中",
  produced: "已生产",
  shipped: "已发货",
  arrived: "已到货",
  inbound: "部分入库",
  completed: "已完成",
  cancelled: "已取消",
};

function number(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function qty(value: string | number, digits = 1) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function requestKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `sc-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  return typeof payload?.detail === "string" ? payload.detail : `${fallback}（${response.status}）`;
}

export default function InTransitPage() {
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [drafts, setDrafts] = useState<Record<string, RowDraft>>({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [workingKey, setWorkingKey] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await authenticatedFetch("/api/v1/supply-chain/production-orders?limit=1000", { cache: "no-store" });
      if (!response.ok) throw new Error(await responseError(response, "生产执行数据加载失败"));
      const payload = await response.json();
      const nextOrders: ProductionOrder[] = payload.rows ?? [];
      setOrders(nextOrders);
      setDrafts((current) => {
        const next = { ...current };
        for (const order of nextOrders) {
          for (const item of order.items ?? []) {
            const key = `${order.id}:${item.id}`;
            const existing = next[key];
            next[key] = {
              completeQty: existing?.completeQty || (number(item.remainingProductionQty) > 0 ? item.remainingProductionQty : ""),
              shipQty: existing?.shipQty || (number(item.factoryReadyQty) > 0 ? item.factoryReadyQty : ""),
              arriveQty: existing?.arriveQty || (number(item.transitQty) > 0 ? item.transitQty : ""),
              carrier: existing?.carrier || "",
              trackingNo: existing?.trackingNo || "",
            };
          }
        }
        return next;
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const rows = useMemo<FlowRow[]>(() => {
    const term = search.trim().toLowerCase();
    const all = orders.flatMap((order) => (order.items ?? []).map((item) => ({ order, item })));
    return all.filter(({ order, item }) => {
      if (["cancelled", "completed"].includes(order.status)) return false;
      const hasWork = number(item.remainingProductionQty) > 0 || number(item.factoryReadyQty) > 0 || number(item.transitQty) > 0;
      if (!hasWork) return false;
      if (!term) return true;
      return `${order.orderNo} ${order.factoryName} ${item.skuCode} ${item.skuName}`.toLowerCase().includes(term);
    });
  }, [orders, search]);

  const summary = useMemo(() => ({
    remaining: rows.reduce((sum, row) => sum + number(row.item.remainingProductionQty), 0),
    ready: rows.reduce((sum, row) => sum + number(row.item.factoryReadyQty), 0),
    transit: rows.reduce((sum, row) => sum + number(row.item.transitQty), 0),
  }), [rows]);

  function setDraft(key: string, field: keyof RowDraft, value: string) {
    setDrafts((current) => ({
      ...current,
      [key]: { ...(current[key] ?? { completeQty: "", shipQty: "", arriveQty: "", carrier: "", trackingNo: "" }), [field]: value },
    }));
  }

  async function move(row: FlowRow, type: "complete" | "ship" | "arrive") {
    const key = `${row.order.id}:${row.item.id}`;
    const draft = drafts[key];
    const quantity = type === "complete" ? draft?.completeQty : type === "ship" ? draft?.shipQty : draft?.arriveQty;
    if (!quantity || number(quantity) <= 0) {
      setError("本次数量必须大于 0");
      return;
    }
    if (type === "ship" && !draft?.trackingNo.trim()) {
      setError("登记成品发货时请填写物流单号，便于在途追踪");
      return;
    }
    setWorkingKey(`${key}:${type}`);
    setError("");
    setNotice("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${row.order.id}/finished/${type}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: requestKey(),
          carrier: type === "ship" ? draft?.carrier.trim() : "",
          tracking_no: type === "ship" ? draft?.trackingNo.trim() : "",
          note: "",
          items: [{ production_order_item_id: row.item.id, quantity }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "供应执行登记失败"));
      const label = type === "complete" ? "生产完成" : type === "ship" ? "成品发货" : "成品到货";
      setNotice(`${row.item.skuName || row.item.skuCode}：${label} ${qty(quantity)} ${row.item.unit}`);
      setDrafts((current) => ({ ...current, [key]: { ...(current[key] ?? draft), completeQty: "", shipQty: "", arriveQty: "" } }));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorkingKey("");
    }
  }

  return (
    <div className="mx-auto max-w-[1600px] space-y-4">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / EXECUTION</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">生产执行与在途</h1>
            <p className="mt-1 text-sm text-slate-500">一行完成：生产完成 → 工厂发货 → 成品在途 → 到货</p>
          </div>
          <div className="flex gap-2">
            <Link href="/supply-chain/production" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">生产订单</Link>
            <Link href="/supply-chain/receiving" className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700">到货入库</Link>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-500">
          <span>待生产 <b className="ml-1 text-slate-900">{qty(summary.remaining)}</b></span>
          <span>工厂待发 <b className="ml-1 text-amber-700">{qty(summary.ready)}</b></span>
          <span>成品在途 <b className="ml-1 text-indigo-700">{qty(summary.transit)}</b></span>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索生产单 / 工厂 / SKU" className="ml-auto w-64 rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-700 outline-none focus:border-indigo-400" />
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">{loading ? "刷新中…" : "刷新"}</button>
        </div>
      </header>

      {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {notice && <div className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</div>}

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1550px] text-sm">
            <thead className="sticky top-[126px] z-10 bg-slate-50 text-left text-[11px] font-medium text-slate-500">
              <tr>
                <th className="px-3 py-3">生产单 / 工厂</th>
                <th className="px-3 py-3">SKU</th>
                <th className="px-3 py-3">计划</th>
                <th className="px-3 py-3">已生产</th>
                <th className="px-3 py-3">工厂待发</th>
                <th className="px-3 py-3">成品在途</th>
                <th className="px-3 py-3">已到货</th>
                <th className="px-3 py-3">登记生产完成</th>
                <th className="px-3 py-3">登记发货</th>
                <th className="px-3 py-3">确认到货</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => {
                const key = `${row.order.id}:${row.item.id}`;
                const draft = drafts[key] ?? { completeQty: "", shipQty: "", arriveQty: "", carrier: "", trackingNo: "" };
                return (
                  <tr key={key} className="align-top hover:bg-slate-50/50">
                    <td className="px-3 py-3">
                      <div className="font-mono text-xs font-semibold text-slate-800">{row.order.orderNo}</div>
                      <div className="mt-1 max-w-[220px] truncate text-xs text-slate-500">{row.order.factoryName}</div>
                      <div className="mt-1 text-[10px] text-slate-400">{STATUS[row.order.status] ?? row.order.status} · 交货 {row.order.expectedDeliveryDate || "—"}</div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="max-w-[220px] truncate font-medium text-slate-700">{row.item.skuName || row.item.skuCode}</div>
                      <div className="mt-1 font-mono text-[10px] text-slate-400">{row.item.skuCode}</div>
                    </td>
                    <td className="px-3 py-3 font-semibold tabular-nums text-slate-800">{qty(row.item.quantity)} {row.item.unit}</td>
                    <td className="px-3 py-3 tabular-nums text-emerald-700">{qty(row.item.completedQty)}</td>
                    <td className="px-3 py-3 font-semibold tabular-nums text-amber-700">{qty(row.item.factoryReadyQty)}</td>
                    <td className="px-3 py-3 font-semibold tabular-nums text-indigo-700">{qty(row.item.transitQty)}</td>
                    <td className="px-3 py-3 tabular-nums text-slate-700">{qty(row.item.arrivedQty)}</td>
                    <td className="px-3 py-3">
                      {number(row.item.remainingProductionQty) > 0 ? <div className="flex gap-1.5"><input type="number" min="0.0001" step="1" value={draft.completeQty} onChange={(e) => setDraft(key, "completeQty", e.target.value)} className="w-24 rounded-md border border-slate-200 px-2 py-1.5 text-xs" /><button onClick={() => move(row, "complete")} disabled={Boolean(workingKey)} className="rounded-md bg-slate-900 px-2 py-1.5 text-[11px] font-medium text-white disabled:opacity-40">完成</button></div> : <span className="text-xs text-slate-300">—</span>}
                    </td>
                    <td className="px-3 py-3">
                      {number(row.item.factoryReadyQty) > 0 ? <div className="space-y-1.5"><div className="flex gap-1.5"><input type="number" min="0.0001" step="1" value={draft.shipQty} onChange={(e) => setDraft(key, "shipQty", e.target.value)} className="w-20 rounded-md border border-slate-200 px-2 py-1.5 text-xs" /><input value={draft.trackingNo} onChange={(e) => setDraft(key, "trackingNo", e.target.value)} placeholder="物流单号" className="w-28 rounded-md border border-slate-200 px-2 py-1.5 text-xs" /><button onClick={() => move(row, "ship")} disabled={Boolean(workingKey)} className="rounded-md bg-indigo-600 px-2 py-1.5 text-[11px] font-medium text-white disabled:opacity-40">发货</button></div><input value={draft.carrier} onChange={(e) => setDraft(key, "carrier", e.target.value)} placeholder="物流公司（可选）" className="w-full rounded-md border border-slate-200 px-2 py-1 text-[11px]" /></div> : <span className="text-xs text-slate-300">—</span>}
                    </td>
                    <td className="px-3 py-3">
                      {number(row.item.transitQty) > 0 ? <div className="flex gap-1.5"><input type="number" min="0.0001" step="1" value={draft.arriveQty} onChange={(e) => setDraft(key, "arriveQty", e.target.value)} className="w-24 rounded-md border border-slate-200 px-2 py-1.5 text-xs" /><button onClick={() => move(row, "arrive")} disabled={Boolean(workingKey)} className="rounded-md bg-emerald-600 px-2 py-1.5 text-[11px] font-medium text-white disabled:opacity-40">到货</button></div> : <span className="text-xs text-slate-300">—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!loading && rows.length === 0 && <div className="p-10 text-center text-sm text-slate-400">当前没有待生产、工厂待发或成品在途任务。</div>}
        </div>
      </section>

      <div className="text-xs leading-5 text-slate-400">这里记录的是供应链执行过程，不直接增加正品库存；到货后进入“到货入库”，最终库存仍以吉客云真实入库与库存快照为准。</div>
    </div>
  );
}
