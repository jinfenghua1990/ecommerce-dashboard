"use client";

import { useState, type FormEvent } from "react";
import { authenticatedFetch } from "@/lib/api";

// 与后端 _CONSUMABLE_SELLER_KEYWORDS 同口径：供应商名命中即自动带出「耗材」类型。
const CONSUMABLE_SUPPLIER_KEYWORDS = ["包装", "印刷", "印务", "耗材", "包材"];

export function NewPurchaseModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (orderId: number) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [orderKind, setOrderKind] = useState("goods");
  function onSupplierChange(value: string) {
    // 按货品类型自动带出：供应商名含包装/印刷/耗材等关键词 → 自动选耗材（仍可手改）。
    const hit = CONSUMABLE_SUPPLIER_KEYWORDS.some((k) => value.includes(k));
    setOrderKind(hit ? "consumable" : "goods");
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const response = await authenticatedFetch("/api/v1/purchase/orders", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          external_order_id: String(form.get("orderNo")).trim(),
          platform: String(form.get("platform") || "1688"),
          supplier_name: String(form.get("supplier")).trim(),
          title: String(form.get("title") || "").trim(),
          ordered_at: new Date(String(form.get("orderedAt"))).toISOString(),
          order_amount: String(form.get("amount")),
          paid_amount: String(form.get("amount")),
          order_kind: orderKind,
        }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "采购记录保存失败");
      onCreated(body.workbenchOrderId ?? -body.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "采购记录保存失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4">
      <form onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="new-purchase-title" className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl">
        <div className="flex items-center justify-between"><h2 id="new-purchase-title" className="text-lg font-semibold">新建采购记录</h2><button type="button" onClick={onClose} disabled={busy} aria-label="关闭新建采购">×</button></div>
        <div className="mt-4 grid gap-3 text-sm">
          <label>采购渠道<select name="platform" defaultValue="1688" className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2"><option value="1688">1688</option><option value="pdd">拼多多</option><option value="taobao">淘宝 / 天猫</option><option value="other">其他渠道</option></select></label>
          <label>订单号<input autoFocus required name="orderNo" className="mt-1 w-full rounded-lg border border-slate-200 p-2" /></label>
          <label>供应商<input required name="supplier" onChange={(event) => onSupplierChange(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 p-2" /></label>
          <label>货品类型<select value={orderKind} onChange={(event) => setOrderKind(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-200 bg-white p-2">
            <option value="goods">正品（正常货品采购）</option>
            <option value="consumable">耗材（包材，登记耗材入库单）</option>
          </select></label>
          <label>采购时间<input required type="datetime-local" name="orderedAt" className="mt-1 w-full rounded-lg border border-slate-200 p-2" /></label>
          <label>采购分配总额（元）<input required type="number" name="amount" min="0.01" step="0.01" className="mt-1 w-full rounded-lg border border-slate-200 p-2" /></label>
          <label>采购说明<input name="title" className="mt-1 w-full rounded-lg border border-slate-200 p-2" /></label>
        </div>
        <p className="mt-2 text-xs text-slate-500">类型会按供应商关键词自动带出，可手改；保存后在工作台分配 SKU / 登记耗材入库单。</p>
        {error && <p role="alert" className="mt-3 text-sm text-red-600">{error}</p>}
        <div className="mt-5 flex justify-end gap-2"><button type="button" disabled={busy} onClick={onClose} className="rounded-lg border px-4 py-2 text-sm">取消</button><button disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm text-white disabled:opacity-50">{busy ? "保存中…" : "保存并配置采购"}</button></div>
      </form>
    </div>
  );
}
