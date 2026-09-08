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

function n(value: string | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function qty(value: number | string, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  return parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  return typeof payload?.detail === "string" ? payload.detail : `${fallback}（${response.status}）`;
}

export default function MaterialFlowPage() {
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [movements, setMovements] = useState<Movement[]>([]);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<number | null>(null);
  const [error, setError] = useState("");

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
        transit += Math.max(n(material.dispatchedQty) - n(material.factoryReceivedQty), 0);
        factory += Math.max(n(material.factoryReceivedQty) - n(material.consumedQty), 0);
        shortage += n(material.shortageQty);
      }
    }
    return { reserved, transit, factory, shortage };
  }, [orders]);

  async function dispatch(order: ProductionOrder, material: Material) {
    const maxQty = n(material.reservedQty);
    if (maxQty <= 0) return;
    const input = window.prompt(`发往工厂数量（最多 ${qty(maxQty)} ${material.unit}）`, String(maxQty));
    if (input === null) return;
    const amount = Number(input);
    if (!Number.isFinite(amount) || amount <= 0 || amount > maxQty) {
      setError("发料数量不正确");
      return;
    }
    const carrier = window.prompt("物流/快递公司（可留空）", "") ?? "";
    const trackingNo = window.prompt("运单号（可留空）", "") ?? "";
    setWorking(material.id);
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${order.id}/materials/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: crypto.randomUUID(),
          carrier,
          tracking_no: trackingNo,
          note: "供应链中心耗材流转",
          items: [{ reservation_id: material.id, quantity: String(amount) }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "发料失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorking(null);
    }
  }

  async function receive(order: ProductionOrder, material: Material) {
    const transit = Math.max(n(material.dispatchedQty) - n(material.factoryReceivedQty), 0);
    if (transit <= 0) return;
    const input = window.prompt(`工厂本次签收数量（在途 ${qty(transit)} ${material.unit}）`, String(transit));
    if (input === null) return;
    const amount = Number(input);
    if (!Number.isFinite(amount) || amount <= 0 || amount > transit) {
      setError("签收数量不正确");
      return;
    }
    setWorking(material.id);
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${order.id}/materials/receive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_key: crypto.randomUUID(),
          note: "工厂确认签收",
          items: [{ reservation_id: material.id, quantity: String(amount) }],
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "签收失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setWorking(null);
    }
  }

  return (
    <div className="mx-auto max-w-[1580px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div>
          <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / MATERIAL FLOW</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">耗材流转</h1>
          <p className="mt-1 text-sm text-slate-500">预占 → 发工厂 → 在途 → 工厂签收；每一步都写真实库存流水。</p>
        </div>
        <div className="flex gap-2">
          <Link href="/supply-chain/production" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">生产订单</Link>
          <Link href="/products/inventory-consumables" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">耗材库存</Link>
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">{loading ? "刷新中…" : "刷新"}</button>
        </div>
      </header>

      <section className="grid gap-3 md:grid-cols-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-4"><div className="text-xs text-slate-500">生产单已预占</div><div className="mt-1 text-2xl font-semibold text-slate-900">{qty(summary.reserved)}</div></div>
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4"><div className="text-xs text-amber-700">发厂在途</div><div className="mt-1 text-2xl font-semibold text-amber-800">{qty(summary.transit)}</div></div>
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs text-emerald-700">工厂可用</div><div className="mt-1 text-2xl font-semibold text-emerald-800">{qty(summary.factory)}</div></div>
        <div className="rounded-2xl border border-red-200 bg-red-50 p-4"><div className="text-xs text-red-700">生产缺料</div><div className="mt-1 text-2xl font-semibold text-red-800">{qty(summary.shortage)}</div></div>
      </section>

      {error && <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="space-y-3">
        {orders.map((order) => (
          <div key={order.id} className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3">
              <div className="flex flex-wrap items-center gap-4">
                <span className="font-semibold text-slate-900">{order.orderNo}</span>
                <span className="text-sm text-slate-600">{order.factoryName}</span>
                <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">{order.status}</span>
              </div>
              <div className="text-xs text-slate-400">预计交货 {order.expectedDeliveryDate ?? "—"}</div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1250px] text-sm">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr><th className="px-4 py-2">耗材</th><th className="px-3 py-2 text-right">需求</th><th className="px-3 py-2 text-right">已预占</th><th className="px-3 py-2 text-right">累计发出</th><th className="px-3 py-2 text-right">当前在途</th><th className="px-3 py-2 text-right">已到工厂</th><th className="px-3 py-2 text-right">已消耗</th><th className="px-3 py-2 text-right">缺口</th><th className="px-4 py-2">操作</th></tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {(order.materials ?? []).map((material) => {
                    const transit = Math.max(n(material.dispatchedQty) - n(material.factoryReceivedQty), 0);
                    return (
                      <tr key={material.id} className={n(material.shortageQty) > 0 ? "bg-red-50/30" : ""}>
                        <td className="px-4 py-3"><div className="font-medium text-slate-800">{material.name || material.code}</div><div className="text-[11px] text-slate-400">{material.code} · {material.unit}</div></td>
                        <td className="px-3 py-3 text-right">{qty(material.requiredQty)}</td>
                        <td className="px-3 py-3 text-right text-indigo-700">{qty(material.reservedQty)}</td>
                        <td className="px-3 py-3 text-right">{qty(material.dispatchedQty)}</td>
                        <td className="px-3 py-3 text-right font-medium text-amber-700">{qty(transit)}</td>
                        <td className="px-3 py-3 text-right text-emerald-700">{qty(material.factoryReceivedQty)}</td>
                        <td className="px-3 py-3 text-right">{qty(material.consumedQty)}</td>
                        <td className={`px-3 py-3 text-right ${n(material.shortageQty) > 0 ? "font-semibold text-red-700" : "text-slate-400"}`}>{qty(material.shortageQty)}</td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <button onClick={() => dispatch(order, material)} disabled={working === material.id || n(material.reservedQty) <= 0} className="rounded-lg border border-indigo-200 px-2.5 py-1.5 text-xs text-indigo-700 hover:bg-indigo-50 disabled:opacity-30">发工厂</button>
                            <button onClick={() => receive(order, material)} disabled={working === material.id || transit <= 0} className="rounded-lg border border-emerald-200 px-2.5 py-1.5 text-xs text-emerald-700 hover:bg-emerald-50 disabled:opacity-30">确认签收</button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {(order.materials ?? []).length === 0 && <tr><td colSpan={9} className="px-4 py-6 text-center text-sm text-slate-400">该生产单暂未关联耗材</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        {!loading && orders.length === 0 && <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-400">当前没有进行中的生产单</div>}
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-5 py-4"><h2 className="font-semibold text-slate-900">流转记录</h2><p className="mt-1 text-xs text-slate-500">每次发料和签收都独立留痕，不覆盖历史记录。</p></div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1050px] text-sm">
            <thead className="bg-slate-50 text-left text-[11px] text-slate-500"><tr><th className="px-4 py-2">时间</th><th className="px-3 py-2">流转单号</th><th className="px-3 py-2">类型</th><th className="px-3 py-2">生产单</th><th className="px-3 py-2">耗材</th><th className="px-3 py-2 text-right">数量</th><th className="px-3 py-2">物流</th><th className="px-4 py-2">运单号</th></tr></thead>
            <tbody className="divide-y divide-slate-100">
              {movements.map((movement) => (
                <tr key={movement.id}><td className="px-4 py-3 text-xs text-slate-500">{movement.occurredAt ? new Date(movement.occurredAt).toLocaleString("zh-CN") : "—"}</td><td className="px-3 py-3 font-mono text-xs">{movement.movementNo}</td><td className="px-3 py-3"><span className={`rounded px-2 py-1 text-xs ${movement.movementType === "dispatch" ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}>{movement.movementType === "dispatch" ? "发往工厂" : "工厂签收"}</span></td><td className="px-3 py-3">#{movement.productionOrderId}</td><td className="px-3 py-3">{movement.consumableName || movement.consumableCode}<div className="text-[11px] text-slate-400">{movement.consumableCode}</div></td><td className="px-3 py-3 text-right font-medium">{qty(movement.quantity)} {movement.unit}</td><td className="px-3 py-3 text-slate-600">{movement.carrier || "—"}</td><td className="px-4 py-3 text-slate-600">{movement.trackingNo || "—"}</td></tr>
              ))}
              {!movements.length && <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-400">暂无耗材流转记录</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
