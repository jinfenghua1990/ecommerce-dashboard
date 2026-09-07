"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type SkuOption = {
  skuId: number;
  skuCode: string;
  skuName: string;
  goodsName: string;
  unit: string;
  suggestedReplenishment: string | null;
};

type ProductionItem = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  unit: string;
  quantity: string;
  completedQty: string;
};

type ProductionMaterial = {
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
  state: "shortage" | "reserved" | "transit" | "factory" | "consumed";
};

type ProductionOrder = {
  id: number;
  orderNo: string;
  factoryName: string;
  status: string;
  plannedStartDate: string | null;
  expectedDeliveryDate: string | null;
  sourceType: string;
  note: string;
  createdBy: string;
  createdAt: string | null;
  itemCount: number;
  materialCount: number;
  materialShortageCount: number;
  items: ProductionItem[];
  materials: ProductionMaterial[];
};

type FormItem = { key: number; skuId: string; quantity: string };

const STATUS: Record<string, { label: string; cls: string }> = {
  planned: { label: "计划中", cls: "bg-slate-100 text-slate-700" },
  confirmed: { label: "已确认", cls: "bg-blue-50 text-blue-700" },
  producing: { label: "生产中", cls: "bg-indigo-50 text-indigo-700" },
  completed: { label: "已完成", cls: "bg-emerald-50 text-emerald-700" },
  cancelled: { label: "已取消", cls: "bg-slate-100 text-slate-400" },
};

const MATERIAL_STATE: Record<ProductionMaterial["state"], string> = {
  shortage: "缺料",
  reserved: "已预占",
  transit: "发往工厂",
  factory: "工厂库存",
  consumed: "已消耗",
};

function qty(value: string | null, digits = 1) {
  if (value === null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return value;
  return number.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

async function responseError(response: Response, fallback: string) {
  const payload = await response.json().catch(() => ({}));
  if (typeof payload?.detail === "string") return payload.detail;
  return `${fallback}（${response.status}）`;
}

export default function ProductionPanel() {
  const [orders, setOrders] = useState<ProductionOrder[]>([]);
  const [skuOptions, setSkuOptions] = useState<SkuOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [factoryName, setFactoryName] = useState("");
  const [plannedStartDate, setPlannedStartDate] = useState("");
  const [expectedDeliveryDate, setExpectedDeliveryDate] = useState("");
  const [note, setNote] = useState("");
  const [items, setItems] = useState<FormItem[]>([{ key: 1, skuId: "", quantity: "" }]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [orderResponse, skuResponse] = await Promise.all([
        authenticatedFetch("/api/v1/supply-chain/production-orders?limit=300", { cache: "no-store" }),
        authenticatedFetch("/api/v1/supply-chain/replenishment?days=30&lead_days=14&safety_days=7&limit=2000", { cache: "no-store" }),
      ]);
      if (!orderResponse.ok) throw new Error(await responseError(orderResponse, "生产单加载失败"));
      if (!skuResponse.ok) throw new Error(await responseError(skuResponse, "SKU 加载失败"));
      const orderPayload = await orderResponse.json();
      const skuPayload = await skuResponse.json();
      setOrders(orderPayload.rows ?? []);
      setSkuOptions(skuPayload.rows ?? []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const skuId = params.get("skuId") ?? "";
    const quantity = params.get("qty") ?? "";
    if (skuId) {
      setItems([{ key: Date.now(), skuId, quantity }]);
    }
  }, []);

  const summary = useMemo(() => ({
    active: orders.filter((order) => !["completed", "cancelled"].includes(order.status)).length,
    producing: orders.filter((order) => order.status === "producing").length,
    shortage: orders.filter((order) => order.materialShortageCount > 0 && order.status !== "cancelled").length,
  }), [orders]);

  function updateItem(key: number, field: "skuId" | "quantity", value: string) {
    setItems((current) => current.map((item) => item.key === key ? { ...item, [field]: value } : item));
  }

  function addItem() {
    setItems((current) => [...current, { key: Date.now() + current.length, skuId: "", quantity: "" }]);
  }

  function removeItem(key: number) {
    setItems((current) => current.length === 1 ? current : current.filter((item) => item.key !== key));
  }

  async function createOrder() {
    setError("");
    const normalized = items
      .filter((item) => item.skuId && Number(item.quantity) > 0)
      .map((item) => ({ sku_id: Number(item.skuId), quantity: item.quantity }));
    if (!factoryName.trim()) {
      setError("请先填写工厂名称");
      return;
    }
    if (!normalized.length) {
      setError("至少填写一个 SKU 和生产数量");
      return;
    }
    setSaving(true);
    try {
      const response = await authenticatedFetch("/api/v1/supply-chain/production-orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          factory_name: factoryName.trim(),
          planned_start_date: plannedStartDate || null,
          expected_delivery_date: expectedDeliveryDate || null,
          source_type: "manual",
          note,
          items: normalized,
        }),
      });
      if (!response.ok) throw new Error(await responseError(response, "创建生产单失败"));
      setFactoryName("");
      setPlannedStartDate("");
      setExpectedDeliveryDate("");
      setNote("");
      setItems([{ key: Date.now(), skuId: "", quantity: "" }]);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function recalculate(orderId: number) {
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${orderId}/recalculate-materials`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "重算耗材失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  async function cancel(orderId: number) {
    if (!window.confirm("确认取消这张生产单？未发出的耗材预占会自动释放。")) return;
    setError("");
    try {
      const response = await authenticatedFetch(`/api/v1/supply-chain/production-orders/${orderId}/cancel`, { method: "POST" });
      if (!response.ok) throw new Error(await responseError(response, "取消生产单失败"));
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <div className="mx-auto max-w-[1550px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div>
          <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN / PRODUCTION</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">生产订单</h1>
          <p className="mt-1 text-sm text-slate-500">工厂排产 · 耗材自动计算 · 预占不扣库存</p>
        </div>
        <div className="flex gap-2">
          <Link href="/supply-chain" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">返回供应链中心</Link>
          <Link href="/products/inventory-consumables" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">查看耗材库存</Link>
        </div>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">新建生产单</h2>
            <p className="mt-1 text-xs text-slate-500">创建后按货品档案里的耗材关联自动计算需求；预占只是锁定可用量，不会直接扣掉耗材库存。</p>
          </div>
          <div className="flex gap-5 text-right text-xs text-slate-500">
            <div><div className="text-xl font-semibold text-slate-900">{summary.active}</div>进行中</div>
            <div><div className="text-xl font-semibold text-indigo-700">{summary.producing}</div>生产中</div>
            <div><div className="text-xl font-semibold text-red-700">{summary.shortage}</div>缺料单</div>
          </div>
        </div>

        <div className="grid gap-3 xl:grid-cols-[1.1fr_150px_150px_1.8fr_auto]">
          <label className="text-[11px] text-slate-500">工厂名称
            <input value={factoryName} onChange={(e) => setFactoryName(e.target.value)} placeholder="例如：深圳市美啡源实业有限公司" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-400" />
          </label>
          <label className="text-[11px] text-slate-500">计划开始
            <input type="date" value={plannedStartDate} onChange={(e) => setPlannedStartDate(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-2 text-sm text-slate-700" />
          </label>
          <label className="text-[11px] text-slate-500">预计交货
            <input type="date" value={expectedDeliveryDate} onChange={(e) => setExpectedDeliveryDate(e.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 px-2 py-2 text-sm text-slate-700" />
          </label>
          <label className="text-[11px] text-slate-500">备注
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="生产要求、批次、包装说明等" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:border-indigo-400" />
          </label>
          <button onClick={createOrder} disabled={saving} className="self-end rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{saving ? "创建中…" : "创建生产单"}</button>
        </div>

        <div className="mt-3 space-y-2">
          {items.map((item, index) => (
            <div key={item.key} className="grid gap-2 rounded-xl bg-slate-50 p-2 md:grid-cols-[42px_1fr_160px_70px]">
              <div className="self-center text-center text-xs font-semibold text-slate-400">{index + 1}</div>
              <select value={item.skuId} onChange={(e) => updateItem(item.key, "skuId", e.target.value)} className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
                <option value="">选择正品 SKU</option>
                {skuOptions.map((sku) => (
                  <option key={sku.skuId} value={sku.skuId}>{sku.goodsName || sku.skuName || sku.skuCode} · {sku.skuCode}</option>
                ))}
              </select>
              <input type="number" min="0.0001" step="1" value={item.quantity} onChange={(e) => updateItem(item.key, "quantity", e.target.value)} placeholder="生产数量" className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700" />
              <button type="button" onClick={() => removeItem(item.key)} disabled={items.length === 1} className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-30">删除</button>
            </div>
          ))}
          <button type="button" onClick={addItem} className="rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-medium text-slate-600 hover:border-indigo-300 hover:text-indigo-700">+ 添加 SKU</button>
        </div>
      </section>

      {error && <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">生产单列表</h2>
            <p className="mt-1 text-xs text-slate-500">内容全部展开显示；缺料会直接标红，不隐藏在详情页。</p>
          </div>
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50">{loading ? "刷新中…" : "刷新"}</button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1350px] text-sm">
            <thead className="bg-slate-50 text-left text-[11px] font-medium text-slate-500">
              <tr>
                <th className="px-4 py-3">生产单</th><th className="px-4 py-3">工厂</th><th className="px-4 py-3">状态</th><th className="px-4 py-3">生产内容</th><th className="px-4 py-3">耗材需求 / 预占</th><th className="px-4 py-3">计划 / 交货</th><th className="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {orders.map((order) => {
                const status = STATUS[order.status] ?? { label: order.status, cls: "bg-slate-100 text-slate-600" };
                return (
                  <tr key={order.id} className={order.status === "cancelled" ? "bg-slate-50/60" : "bg-white"}>
                    <td className="px-4 py-4 align-top"><div className="font-mono text-xs font-semibold text-slate-800">{order.orderNo}</div><div className="mt-1 text-[10px] text-slate-400">{order.createdAt ? new Date(order.createdAt).toLocaleString("zh-CN") : ""}</div></td>
                    <td className="max-w-[200px] px-4 py-4 align-top"><div className="font-medium text-slate-700">{order.factoryName}</div>{order.note && <div className="mt-1 line-clamp-2 text-xs text-slate-400">{order.note}</div>}</td>
                    <td className="px-4 py-4 align-top"><span className={`rounded-full px-2.5 py-1 text-[10px] font-medium ${status.cls}`}>{status.label}</span>{order.materialShortageCount > 0 && order.status !== "cancelled" && <div className="mt-2 text-[10px] font-medium text-red-600">{order.materialShortageCount} 项缺料</div>}</td>
                    <td className="max-w-[300px] px-4 py-4 align-top"><div className="space-y-1.5">{order.items.map((item) => <div key={item.id} className="flex items-center justify-between gap-3 text-xs"><span className="truncate text-slate-600">{item.skuName || item.skuCode}</span><span className="shrink-0 font-medium tabular-nums text-slate-800">{qty(item.quantity)} {item.unit}</span></div>)}</div></td>
                    <td className="max-w-[400px] px-4 py-4 align-top">
                      {order.materials.length === 0 ? <div className="text-xs text-amber-600">该生产内容尚未关联耗材</div> : <div className="space-y-1.5">{order.materials.map((material) => <div key={material.id} className={`grid grid-cols-[1fr_auto_auto] items-center gap-3 rounded-lg px-2 py-1.5 text-xs ${material.state === "shortage" ? "bg-red-50" : "bg-slate-50"}`}><span className="truncate text-slate-600">{material.name} <span className="text-[10px] text-slate-400">{material.code}</span></span><span className="tabular-nums text-slate-700">需 {qty(material.requiredQty)} / 占 {qty(material.reservedQty)}</span><span className={material.state === "shortage" ? "font-medium text-red-700" : "text-emerald-700"}>{MATERIAL_STATE[material.state]}{Number(material.shortageQty) > 0 ? ` ${qty(material.shortageQty)}` : ""}</span></div>)}</div>}
                    </td>
                    <td className="px-4 py-4 align-top text-xs text-slate-600"><div>开始：{order.plannedStartDate || "—"}</div><div className="mt-1">交货：{order.expectedDeliveryDate || "—"}</div></td>
                    <td className="px-4 py-4 align-top"><div className="flex flex-wrap gap-2">{["planned", "confirmed"].includes(order.status) && <button onClick={() => recalculate(order.id)} className="rounded-md border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50">重算耗材</button>}{!["completed", "cancelled"].includes(order.status) && <button onClick={() => cancel(order.id)} className="rounded-md border border-red-100 px-2 py-1 text-[11px] text-red-600 hover:bg-red-50">取消</button>}</div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!loading && orders.length === 0 && <div className="p-10 text-center text-sm text-slate-400">还没有生产单，可以从上方直接创建。</div>}
        </div>
      </section>
    </div>
  );
}
