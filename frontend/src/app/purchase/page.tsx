"use client";

import { useCallback, useEffect, useState } from "react";

type OrderSummary = {
  id: number;
  externalOrderId: string;
  supplierName: string;
  title: string;
  paidAmount: string | null;
  purchaseStatus: string;
  invoiceStatus: string;
  goodsAllocated: string;
  expenseAllocated: string;
  unallocated: string;
  balanced: boolean;
};

type Detail = {
  id: number;
  externalOrderId: string;
  supplierName: string;
  title: string;
  paidAmount: string | null;
  purchaseStatus: string;
  invoiceStatus: string;
  allocations: { id: number; skuCode: string; goodsName: string; quantity: string; unitPrice: string; amount: string }[];
  expenses: { id: number; expenseType: string; amount: string; note: string }[];
  invoices: { id: number; invoiceNo: string; invoiceAmount: string | null; allocatedAmount: string | null; invoiceDate: string | null }[];
  balance: { paid: string; goods_allocated: string; expense_allocated: string; unallocated: string; balanced: boolean; allow_mark_refined: boolean };
};

const PO_STATUS: Record<string, string> = {
  pending_refine: "待完善", confirmed: "已确认采购内容", jackyun_linked: "已关联吉客云采购单",
  producing: "待发货/生产中", shipped: "已发货", arrived: "已到货", inbound: "已入库", done: "完成",
};
const INV_STATUS: Record<string, string> = {
  unverified: "未核验", none: "未开票", applied: "已申请", partial: "部分开票", full: "全部开票",
};
const EXPENSE_LABEL: Record<string, string> = {
  pack: "包装", processing: "加工", plate: "制版", mold: "模具", freight: "运费", testing: "检测", other: "其他",
};
const NEXT_STATUS: Record<string, string> = {
  confirmed: "jackyun_linked", jackyun_linked: "producing", producing: "shipped",
  shipped: "arrived", arrived: "inbound", inbound: "done",
};

const fmt = (v: string | null | undefined) =>
  v === null || v === undefined ? "—" : `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;

export default function PurchasePage() {
  const [orders, setOrders] = useState<OrderSummary[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [showNew, setShowNew] = useState(false);

  // 新订单表单
  const [extId, setExtId] = useState("");
  const [supplier, setSupplier] = useState("");
  const [title, setTitle] = useState("");
  const [paid, setPaid] = useState("");
  // 分配/费用表单
  const [skuCode, setSkuCode] = useState("");
  const [goodsName, setGoodsName] = useState("");
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [expType, setExpType] = useState("pack");
  const [expAmount, setExpAmount] = useState("");
  // 发票表单
  const [invNo, setInvNo] = useState("");
  const [invAmount, setInvAmount] = useState("");

  const loadOrders = useCallback(() => {
    fetch("/api/v1/purchase/orders", { cache: "no-store" })
      .then((r) => r.json())
      .then(setOrders)
      .catch(() => {});
  }, []);

  const loadDetail = useCallback(() => {
    if (!detail) return;
    fetch(`/api/v1/purchase/orders/${detail.id}`, { cache: "no-store" })
      .then((r) => r.json())
      .then(setDetail)
      .catch(() => {});
  }, [detail]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(loadOrders, [loadOrders]);

  async function call(path: string, body?: unknown, method = "POST") {
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/purchase${path}`, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setMsg(`失败：${d.detail ?? res.status}`);
        return null;
      }
      setMsg("");
      loadOrders();
      return d;
    } finally {
      setBusy(false);
    }
  }

  async function createOrder() {
    if (!extId || !paid) {
      setMsg("订单号和实付金额必填");
      return;
    }
    const d = await call("/orders", {
      external_order_id: extId, supplier_name: supplier, title, paid_amount: paid,
    });
    if (d) {
      setShowNew(false);
      setExtId(""); setSupplier(""); setTitle(""); setPaid("");
      const res = await fetch(`/api/v1/purchase/orders/${d.id}`);
      setDetail(await res.json());
    }
  }

  async function refreshDetail() {
    if (detail) {
      const res = await fetch(`/api/v1/purchase/orders/${detail.id}`);
      setDetail(await res.json());
    }
  }

  const editable = detail?.purchaseStatus === "pending_refine";

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">采购</h1>
        <button
          onClick={() => setShowNew(!showNew)}
          className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
        >
          {showNew ? "收起" : "登记 1688 订单"}
        </button>
      </div>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        1688 每日自动同步待开放平台开通后启用；当前可手工登记**真实已付款**的 1688 订单并完善采购内容。
        未分配金额 ≠ 0 时禁止确认。SKU 将在吉客云开通后自动接入产品库选择器。
      </p>

      {showNew && (
        <div className="mt-4 grid max-w-4xl grid-cols-5 items-end gap-3 rounded-xl border border-gray-200 bg-white p-4">
          <label className="text-xs text-gray-500">1688 订单号*
            <input value={extId} onChange={(e) => setExtId(e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-gray-500">供应商
            <input value={supplier} onChange={(e) => setSupplier(e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-gray-500">原始标题
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="定制专拍/OEM定制…" className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-gray-500">实付金额*
            <input value={paid} onChange={(e) => setPaid(e.target.value)} placeholder="50000" className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm" />
          </label>
          <button onClick={createOrder} disabled={busy}
            className="rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50">
            创建
          </button>
        </div>
      )}

      {msg && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800">{msg}</div>}

      <div className="mt-5 grid max-w-6xl grid-cols-[320px_1fr] gap-5">
        <div className="space-y-2">
          {orders.map((o) => (
            <div
              key={o.id}
              onClick={async () => {
                const res = await fetch(`/api/v1/purchase/orders/${o.id}`);
                setDetail(await res.json());
              }}
              className={`cursor-pointer rounded-xl border p-3 ${
                detail?.id === o.id ? "border-indigo-300 bg-indigo-50/40" : "border-gray-200 bg-white"
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="font-mono text-xs text-gray-500">{o.externalOrderId}</span>
                <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-600">
                  {PO_STATUS[o.purchaseStatus]}
                </span>
              </div>
              <div className="mt-1 truncate text-sm" title={o.title}>{o.supplierName || o.title || "—"}</div>
              <div className="mt-0.5 text-xs text-gray-400">
                实付 {fmt(o.paidAmount)} · 未分配
                <span className={o.balanced ? "text-emerald-600" : "text-red-600"}>
                  {" "}{fmt(o.unallocated)}
                </span>
              </div>
            </div>
          ))}
          {orders.length === 0 && (
            <div className="rounded-xl border border-dashed border-gray-300 p-6 text-center text-sm text-gray-400">
              暂无采购订单
            </div>
          )}
        </div>

        {detail && (
          <div className="space-y-4">
            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="flex items-center justify-between">
                <div>
                  <div className="font-mono text-xs text-gray-400">{detail.externalOrderId}</div>
                  <div className="text-sm font-medium">{detail.supplierName || detail.title || "—"}</div>
                </div>
                <div className="text-right text-xs">
                  <div>实付 <b>{fmt(detail.paidAmount)}</b></div>
                  <div>
                    未分配{" "}
                    <b className={detail.balance.balanced ? "text-emerald-600" : "text-red-600"}>
                      {fmt(detail.balance.unallocated)}
                    </b>
                  </div>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2 text-xs">
                <button
                  onClick={async () => {
                    const d = await call(`/orders/${detail.id}/refine`);
                    if (d) refreshDetail();
                  }}
                  disabled={busy || !editable || !detail.balance.allow_mark_refined}
                  className="rounded-lg bg-emerald-600 px-3 py-1.5 font-medium text-white disabled:opacity-40"
                >
                  标记采购内容完整
                </button>
                {NEXT_STATUS[detail.purchaseStatus] && (
                  <button
                    onClick={async () => {
                      const d = await call(`/orders/${detail.id}/status`, { status: NEXT_STATUS[detail.purchaseStatus] });
                      if (d) refreshDetail();
                    }}
                    disabled={busy}
                    className="rounded-lg bg-white px-3 py-1.5 text-gray-600 ring-1 ring-gray-200 disabled:opacity-40"
                  >
                    流转到「{PO_STATUS[NEXT_STATUS[detail.purchaseStatus]]}」
                  </button>
                )}
                <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] leading-4 text-gray-500">
                  发票状态：{INV_STATUS[detail.invoiceStatus]}
                </span>
              </div>
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium">SKU 分配（吉客云产品库接入后支持搜索选择）</div>
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-xs text-gray-400">
                  <tr><th className="py-1">SKU</th><th>品名</th><th>数量</th><th>单价</th><th>金额</th><th></th></tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {detail.allocations.map((a) => (
                    <tr key={a.id}>
                      <td className="py-1.5 font-mono text-xs">{a.skuCode || "—"}</td>
                      <td className="text-xs">{a.goodsName || "—"}</td>
                      <td className="text-xs">{a.quantity}</td>
                      <td className="text-xs">{fmt(a.unitPrice)}</td>
                      <td className="text-xs font-medium">{fmt(a.amount)}</td>
                      <td className="text-right">
                        {editable && (
                          <button
                            onClick={async () => { await call(`/allocations/${a.id}?po_id=${detail.id}`, undefined, "DELETE"); refreshDetail(); }}
                            className="text-xs text-red-500 hover:underline"
                          >
                            删除
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {detail.allocations.length === 0 && (
                    <tr><td colSpan={6} className="py-2 text-center text-xs text-gray-400">未分配 SKU</td></tr>
                  )}
                </tbody>
              </table>
              {editable && (
                <div className="mt-3 grid grid-cols-6 items-end gap-2">
                  <input value={skuCode} onChange={(e) => setSkuCode(e.target.value)} placeholder="SKU编码"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                  <input value={goodsName} onChange={(e) => setGoodsName(e.target.value)} placeholder="品名"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                  <input value={qty} onChange={(e) => setQty(e.target.value)} placeholder="数量"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                  <input value={price} onChange={(e) => setPrice(e.target.value)} placeholder="单价"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                  <button
                    onClick={async () => {
                      const d = await call(`/orders/${detail.id}/allocations`, {
                        sku_code: skuCode, goods_name: goodsName, quantity: qty, unit_price: price,
                      });
                      if (d) { setSkuCode(""); setGoodsName(""); setQty(""); setPrice(""); refreshDetail(); }
                    }}
                    disabled={busy || !qty || !price}
                    className="rounded-lg bg-indigo-600 px-2 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    添加
                  </button>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium">附加费用（不伪造成 SKU）</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {detail.expenses.map((e) => (
                  <span key={e.id} className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-2.5 py-1 text-xs">
                    {EXPENSE_LABEL[e.expenseType] ?? e.expenseType} {fmt(e.amount)}
                    {editable && (
                      <button
                        onClick={async () => { await call(`/expenses/${e.id}?po_id=${detail.id}`, undefined, "DELETE"); refreshDetail(); }}
                        className="text-red-400 hover:text-red-600"
                      >
                        ×
                      </button>
                    )}
                  </span>
                ))}
                {detail.expenses.length === 0 && <span className="text-xs text-gray-400">无附加费用</span>}
              </div>
              {editable && (
                <div className="mt-3 grid grid-cols-4 items-end gap-2">
                  <select value={expType} onChange={(e) => setExpType(e.target.value)}
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs">
                    {Object.entries(EXPENSE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                  <input value={expAmount} onChange={(e) => setExpAmount(e.target.value)} placeholder="金额"
                    className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                  <button
                    onClick={async () => {
                      const d = await call(`/orders/${detail.id}/expenses`, { expense_type: expType, amount: expAmount });
                      if (d) { setExpAmount(""); refreshDetail(); }
                    }}
                    disabled={busy || !expAmount}
                    className="rounded-lg bg-indigo-600 px-2 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                  >
                    添加费用
                  </button>
                </div>
              )}
            </div>

            <div className="rounded-xl border border-gray-200 bg-white p-4">
              <div className="text-sm font-medium">发票（一单多票 / 一票多单）</div>
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-xs text-gray-400">
                  <tr><th className="py-1">发票号</th><th>票面金额</th><th>分摊到本单</th><th>日期</th></tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {detail.invoices.map((iv) => (
                    <tr key={iv.id}>
                      <td className="py-1.5 font-mono text-xs">{iv.invoiceNo || `#${iv.id}`}</td>
                      <td className="text-xs">{fmt(iv.invoiceAmount)}</td>
                      <td className="text-xs font-medium">{fmt(iv.allocatedAmount)}</td>
                      <td className="text-xs text-gray-400">{iv.invoiceDate ? iv.invoiceDate.slice(0, 10) : "—"}</td>
                    </tr>
                  ))}
                  {detail.invoices.length === 0 && (
                    <tr><td colSpan={4} className="py-2 text-center text-xs text-gray-400">暂无关联发票</td></tr>
                  )}
                </tbody>
              </table>
              <div className="mt-3 grid grid-cols-4 items-end gap-2">
                <input value={invNo} onChange={(e) => setInvNo(e.target.value)} placeholder="发票号"
                  className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                <input value={invAmount} onChange={(e) => setInvAmount(e.target.value)} placeholder="票面金额"
                  className="rounded-lg border border-gray-200 px-2 py-1.5 text-xs" />
                <button
                  onClick={async () => {
                    const inv = await call("/invoices", { invoice_no: invNo, invoice_amount: invAmount });
                    if (inv) {
                      const lr = await call(`/invoices/${inv.id}/links`, {
                        po_id: detail.id, allocated_amount: invAmount,
                      });
                      if (lr) { setInvNo(""); setInvAmount(""); refreshDetail(); }
                    }
                  }}
                  disabled={busy || !invAmount}
                  className="rounded-lg bg-indigo-600 px-2 py-1.5 text-xs font-medium text-white disabled:opacity-40"
                >
                  开票并关联本单
                </button>
                <span className="text-[10px] leading-3 text-gray-400">分摊超票面/超实付将被拦截并记入异常中心</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
