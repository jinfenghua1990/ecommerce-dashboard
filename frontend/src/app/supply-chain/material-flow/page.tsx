"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Material = {
  id: number;
  consumableId: number;
  code: string;
  name: string;
  unit: string;
  requiredQty: string;
  reservedQty: string;
  dispatchedQty: string;
  factoryReceivedQty: string;
  consumedQty: string;
  shortageQty: string;
  state: string;
};

type ProductionOrder = {
  id: number;
  orderNo: string;
  factoryName: string;
  status: string;
  expectedDeliveryDate: string | null;
  materials: Material[];
};

type Movement = {
  id: number;
  movementNo: string;
  productionOrderId: number;
  reservationId: number;
  consumableCode: string;
  consumableName: string;
  unit: string;
  movementType: "dispatch" | "factory_receive";
  quantity: string;
  carrier: string;
  trackingNo: string;
  note: string;
  occurredAt: string | null;
};

type MaterialDraft = {
  dispatchQty: string;
  carrier: string;
  trackingNo: string;
  receiveQty: string;
};

function n(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function qty(value: number | string, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function requestKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `material-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  return typeof payload?.detail === "string" ? payload.detail : `${fallback}（${response.status}）`;
}

function materialTransit(material: Material) {
  return Math.max(n(material.dispatchedQty) - n(material.factoryReceivedQty), 0);
}

function defaultDraft(material: Material): MaterialDraft {
  const transit = materialTransit(material);
  return {
    dispatchQty: n(material.reservedQty) > 0 ? String(n(material.reservedQty)) : "",
    carrier: "",
    trackingNo: "",
    receiveQty: transit > 0 ? String(transit) : "",
  };
}

export default function MaterialFlowPage() {
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [drafts, setDrafts] = useState<Record<number, MaterialDraft>>({});
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [ordersResponse, movementsResponse] = await Promise.all([
        authenticatedFetch("/api/v1/supply-chain/production-orders?limit=500", { cache: "no-store" }),
        authenticatedFetch("/api/v1/supply-chain/material-movements?limit=1000", { cache: "no-store" }),
      ]);
      if (!ordersResponse.ok) throw new Error(await responseError(ordersResponse, "生产单加载失败"));
      if (!movementsResponse.ok) throw new Error(await responseError(movementsResponse, "耗材流转加载失败"));
      const orderPayload = await ordersResponse.json();
      const movementPayload = await movementsResponse.json();
      setOrders((orderPayload.rows ?? []).filter((item: ProductionOrder) => !["completed", "cancelled"].includes(item.status)));
      setMovements(movementPayload.rows ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const summary = useMemo(() => {
    let reserved = 0;
    let transit = 0;
    let factory = 0;
    let shortage = 0;
    for (const order of orders) {
      for (const material of order.materials ?? []) {
        reserved += n(material.reservedQty);
        transit += materialTransit(material);
        factory += Math.max(n(material.factoryReceivedQty) - n(material.consumedQty), 0);
        shortage += n(material.shortageQty);
      }
    }
    return { reserved, transit, factory, shortage };
  }, [orders]);

  function draftFor(material: Material) {
    return drafts[material.id] ?? defaultDraft(material);
  }

  function setDraft(material: Material, field: keyof MaterialDraft, value: string) {
    setDrafts((current) => ({
      ...current,
      [material.id]: { ...(current[material.id] ?? defaultDraft(material)), [field]: value },
    }));
  }

  function resetDraft(materialId: number) {
    setDrafts((current) => {
      const next = { ...current };
      delete next[materialId];
      return next;
    });
  }

  async function dispatch(order: ProductionOrder, material: Material) {
    const maxQty = n(material.reservedQty);
    if (maxQty <= 0) return;
    const draft = draftFor(material);
    const amount = Number(draft.dispatchQty);
    if (!Number.isFinite(amount) || amount <= 0 || amount > maxQty) {
      setError(`发料数量应大于 0 且不超过 ${qty(maxQty)} ${material.unit}`);
      return;
    }

    setWorking(`${material.id}:dispatch`);
    setError("");
    setNotice("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${order.id}/materials/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: requestKey(),
          carrier: draft.carrier.trim(),
          tracking_no: draft.trackingNo.trim(),
          note: "供应链中心耗材流转",
          items: [{ reservation_id: material.id, quantity: String(amount) }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "发料失败"));
      setNotice(`${material.name || material.code} 已发往 ${order.factoryName}：${qty(amount)} ${material.unit}`);
      resetDraft(material.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorking(null);
    }
  }

  async function receive(order: ProductionOrder, material: Material) {
    const transit = materialTransit(material);
    if (transit <= 0) return;
    const draft = draftFor(material);
    const amount = Number(draft.receiveQty);
    if (!Number.isFinite(amount) || amount <= 0 || amount > transit) {
      setError(`签收数量应大于 0 且不超过在途 ${qty(transit)} ${material.unit}`);
      return;
    }

    setWorking(`${material.id}:receive`);
    setError("");
    setNotice("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${order.id}/materials/receive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: requestKey(),
          note: "工厂确认签收",
          items: [{ reservation_id: material.id, quantity: String(amount) }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "签收失败"));
      setNotice(`${order.factoryName} 已签收 ${material.name || material.code}：${qty(amount)} ${material.unit}`);
      resetDraft(material.id);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorking(null);
    }
  }

  return (
    <div className="mx-auto max-w-[1650px] space-y-4">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / MATERIAL FLOW</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">耗材流转</h1>
            <p className="mt-1 text-sm text-slate-500">预占 → 发工厂 → 在途 → 工厂签收；发料与签收直接在同一行完成。</p>
          </div>
          <div className="flex gap-2">
            <Link href="/supply-chain/production" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">生产订单</Link>
            <Link href="/products/inventory-consumables" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">耗材库存</Link>
            <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-50">{loading ? "刷新中…" : "刷新"}</button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500">
          <span>生产单 <b className="ml-1 text-slate-900">{orders.length}</b></span>
          <span>已预占 <b className="ml-1 text-indigo-700">{qty(summary.reserved)}</b></span>
          <span>发厂在途 <b className="ml-1 text-amber-700">{qty(summary.transit)}</b></span>
          <span>工厂可用 <b className="ml-1 text-emerald-700">{qty(summary.factory)}</b></span>
          <span>生产缺料 <b className={`ml-1 ${summary.shortage > 0 ? "text-red-700" : "text-slate-900"}`}>{qty(summary.shortage)}</b></span>
          <span className="ml-auto text-[11px] text-slate-400">数量 / 物流 / 运单 / 签收均在表格内处理</span>
        </div>
      </header>

      {error && <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {notice && <div className="rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</div>}

      <section className="space-y-3">
        {orders.map((order) => (
          <div key={order.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-2.5">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-mono text-xs font-semibold text-slate-900">{order.orderNo}</span>
                <span className="text-sm font-medium text-slate-700">{order.factoryName}</span>
                <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">{order.status}</span>
              </div>
              <div className="text-xs text-slate-400">预计交货 {order.expectedDeliveryDate ?? "—"}</div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[1780px] text-sm">
                <thead className="bg-slate-50 text-left text-[11px] font-medium text-slate-500">
                  <tr>
                    <th className="px-4 py-2">耗材</th>
                    <th className="px-3 py-2 text-right">需求</th>
                    <th className="px-3 py-2 text-right">已预占</th>
                    <th className="px-3 py-2 text-right">累计发出</th>
                    <th className="px-3 py-2 text-right">当前在途</th>
                    <th className="px-3 py-2 text-right">已到工厂</th>
                    <th className="px-3 py-2 text-right">已消耗</th>
                    <th className="px-3 py-2 text-right">缺口</th>
                    <th className="px-4 py-2">发料 / 签收（同一行）</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(order.materials ?? []).map((material) => {
                    const transit = materialTransit(material);
                    const draft = draftFor(material);
                    const dispatchBusy = working === `${material.id}:dispatch`;
                    const receiveBusy = working === `${material.id}:receive`;
                    const anyBusy = working?.startsWith(`${material.id}:`) ?? false;

                    return (
                      <tr key={material.id} className={n(material.shortageQty) > 0 ? "bg-red-50/30" : "hover:bg-slate-50/60"}>
                        <td className="px-4 py-2.5">
                          <div className="font-medium text-slate-800">{material.name || material.code}</div>
                          <div className="text-[11px] text-slate-400">{material.code} · {material.unit}</div>
                        </td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{qty(material.requiredQty)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-indigo-700">{qty(material.reservedQty)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{qty(material.dispatchedQty)}</td>
                        <td className="px-3 py-2.5 text-right font-medium tabular-nums text-amber-700">{qty(transit)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums text-emerald-700">{qty(material.factoryReceivedQty)}</td>
                        <td className="px-3 py-2.5 text-right tabular-nums">{qty(material.consumedQty)}</td>
                        <td className={`px-3 py-2.5 text-right tabular-nums ${n(material.shortageQty) > 0 ? "font-semibold text-red-700" : "text-slate-400"}`}>{qty(material.shortageQty)}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1.5 whitespace-nowrap">
                            <span className="text-[10px] font-medium text-indigo-500">发</span>
                            <input
                              type="number"
                              min="0"
                              step="1"
                              value={draft.dispatchQty}
                              onChange={(event) => setDraft(material, "dispatchQty", event.target.value)}
                              placeholder="数量"
                              className="h-8 w-20 rounded-md border border-slate-200 px-2 text-xs outline-none focus:border-indigo-400"
                            />
                            <input
                              value={draft.carrier}
                              onChange={(event) => setDraft(material, "carrier", event.target.value)}
                              placeholder="物流（选填）"
                              className="h-8 w-24 rounded-md border border-slate-200 px-2 text-xs outline-none focus:border-indigo-400"
                            />
                            <input
                              value={draft.trackingNo}
                              onChange={(event) => setDraft(material, "trackingNo", event.target.value)}
                              placeholder="运单号（选填）"
                              className="h-8 w-32 rounded-md border border-slate-200 px-2 text-xs outline-none focus:border-indigo-400"
                            />
                            <button
                              onClick={() => dispatch(order, material)}
                              disabled={anyBusy || n(material.reservedQty) <= 0}
                              className="h-8 rounded-md border border-indigo-200 px-2.5 text-xs font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-30"
                            >
                              {dispatchBusy ? "发料中…" : "发工厂"}
                            </button>

                            <span className="mx-1 h-5 w-px bg-slate-200" />
                            <span className="text-[10px] font-medium text-emerald-600">收</span>
                            <input
                              type="number"
                              min="0"
                              step="1"
                              value={draft.receiveQty}
                              onChange={(event) => setDraft(material, "receiveQty", event.target.value)}
                              placeholder="签收量"
                              className="h-8 w-20 rounded-md border border-slate-200 px-2 text-xs outline-none focus:border-emerald-400"
                            />
                            <button
                              onClick={() => receive(order, material)}
                              disabled={anyBusy || transit <= 0}
                              className="h-8 rounded-md border border-emerald-200 px-2.5 text-xs font-medium text-emerald-700 hover:bg-emerald-50 disabled:opacity-30"
                            >
                              {receiveBusy ? "签收中…" : "确认签收"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {(order.materials ?? []).length === 0 && (
                    <tr><td colSpan={9} className="px-4 py-6 text-center text-sm text-slate-400">该生产单暂未关联耗材</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        ))}

        {!loading && orders.length === 0 && (
          <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400">当前没有进行中的生产单</div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex flex-wrap items-end justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">流转记录</h2>
            <p className="mt-1 text-[11px] text-slate-500">每次发料和签收独立留痕，不覆盖历史记录。</p>
          </div>
          <span className="text-xs text-slate-400">共 {movements.length} 条</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1050px] text-sm">
            <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
              <tr>
                <th className="px-4 py-2">时间</th>
                <th className="px-3 py-2">流转单号</th>
                <th className="px-3 py-2">类型</th>
                <th className="px-3 py-2">生产单</th>
                <th className="px-3 py-2">耗材</th>
                <th className="px-3 py-2 text-right">数量</th>
                <th className="px-3 py-2">物流</th>
                <th className="px-4 py-2">运单号</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {movements.map((movement) => (
                <tr key={movement.id}>
                  <td className="px-4 py-3 text-xs text-slate-500">{movement.occurredAt ? new Date(movement.occurredAt).toLocaleString("zh-CN") : "—"}</td>
                  <td className="px-3 py-3 font-mono text-xs">{movement.movementNo}</td>
                  <td className="px-3 py-3">
                    <span className={`rounded px-2 py-1 text-xs ${movement.movementType === "dispatch" ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}>
                      {movement.movementType === "dispatch" ? "发往工厂" : "工厂签收"}
                    </span>
                  </td>
                  <td className="px-3 py-3">#{movement.productionOrderId}</td>
                  <td className="px-3 py-3">{movement.consumableName || movement.consumableCode}<div className="text-[11px] text-slate-400">{movement.consumableCode}</div></td>
                  <td className="px-3 py-3 text-right font-medium tabular-nums">{qty(movement.quantity)} {movement.unit}</td>
                  <td className="px-3 py-3 text-slate-600">{movement.carrier || "—"}</td>
                  <td className="px-4 py-3 text-slate-600">{movement.trackingNo || "—"}</td>
                </tr>
              ))}
              {!movements.length && <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-400">暂无耗材流转记录</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
