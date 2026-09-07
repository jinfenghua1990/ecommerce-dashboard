"use client";

import { useState } from "react";
import Link from "next/link";
import { authenticatedFetch, procurementChainApi, taxInvoiceApi, type TaxInvoiceRow, type WorkbenchDetail } from "@/lib/api";

type Invoice = { invoiceId: number; linkId: number; invoiceKind: "tax" | "manual"; invoiceNo: string; amount: number | null; issueDate: string | null; verified: boolean; verifiedMonth: string };
const money = (value: number | null | undefined) => value == null ? "未提供" : `¥${value.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
const inputClass = "h-8 min-w-0 rounded-md border border-slate-200 bg-white px-2 text-xs";
const buttonClass = "rounded-md bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-600 hover:bg-indigo-100 disabled:opacity-40";

async function mutate(path: string, method: string, body?: object) {
  const res = await authenticatedFetch(`/api/v1/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  const result = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof result.detail === "string" ? result.detail : `保存失败（${res.status}）`);
  return result;
}

// 发票与认证记录面板。
// 说明：结算单（付款）关联与发票无关——本系统付款以 1688 订单实付/微调为准，
// 结算单功能从未启用（链路 0 条），已从 UI 移除；发票主路径是关联税务清单
// （进项发票池自动带出），手工登记仅作发票不在池内时的兜底。
export function RelatedRecordsPanel({ detail, ensurePo, onChanged }: {
  detail: WorkbenchDetail; ensurePo: () => Promise<number>; onChanged: () => Promise<void>;
}) {
  const { order } = detail;
  const invoices = detail.detail.invoice as Invoice[];
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [taxCandidates, setTaxCandidates] = useState<TaxInvoiceRow[]>([]);
  const [invoiceNo, setInvoiceNo] = useState("");
  const [invoiceDate, setInvoiceDate] = useState("");
  const [invoiceAmount, setInvoiceAmount] = useState("");
  const [allocatedAmount, setAllocatedAmount] = useState("");
  const [months, setMonths] = useState<Record<number, string>>({});

  async function action(work: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try { await work(); await onChanged(); setMessage(success); }
    catch (e) { setError(e instanceof Error ? e.message : "操作失败，请重试"); }
    finally { setBusy(false); }
  }

  async function openTaxPicker() {
    setPickerOpen(true); setQuery(""); setError(""); setBusy(true);
    setTaxCandidates([]);
    try {
      setTaxCandidates(await taxInvoiceApi.invoices({ direction: "input", status: "issued" }));
    } catch (e) { setError(e instanceof Error ? e.message : "单据加载失败"); }
    finally { setBusy(false); }
  }

  async function linkTax(invoice: TaxInvoiceRow) {
    await action(async () => {
      await mutate("procurement-chain/invoice-links", "POST", { order_id: order.orderId, invoice_id: invoice.id });
      setPickerOpen(false);
    }, "税务清单发票已关联本单");
  }

  return <section className="space-y-3 rounded-lg border border-slate-200/80 bg-white p-3" aria-label="发票与认证记录">
    <div className="flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold text-slate-700">发票与认证记录</h3>
      <button disabled={busy} onClick={() => void openTaxPicker()} className={buttonClass}>从税务清单带出</button>
    </div>
    <p className="text-[11px] text-slate-400">从已开票的进项发票池选择关联本单；付款状态与发票无关，以 1688 订单实付为准。</p>
    {invoices.length === 0 && <p className="text-xs text-amber-600">尚未关联发票</p>}
    {invoices.map(inv => <div key={`${inv.invoiceKind}-${inv.linkId}`} className="space-y-2 rounded bg-slate-50 p-2 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-mono">{inv.invoiceNo || "未提供号码"}</span><span>{money(inv.amount)} · {inv.invoiceKind === "tax" ? "税务清单" : "手工登记"}</span><button disabled={busy} className="text-red-500" onClick={() => {
        if (window.confirm("解除发票与本单的关联？原始发票保留。")) void action(() => inv.invoiceKind === "tax" ? procurementChainApi.deleteInvoiceLink(inv.linkId) : mutate(`purchase/invoice-links/${inv.linkId}`, "DELETE"), "发票关联已解除");
      }}>解除</button></div>
      {inv.invoiceKind === "tax" ? <div className="flex flex-wrap items-center gap-2">
        <span className={inv.verified ? "text-emerald-600" : "text-slate-500"}>{inv.verified ? `已记录认证 ${inv.verifiedMonth}` : "未记录认证"}</span>
        {!inv.verified && <input aria-label={`认证月份 ${inv.invoiceNo}`} type="month" value={months[inv.invoiceId] ?? ""} onChange={e => setMonths(m => ({ ...m, [inv.invoiceId]: e.target.value }))} className={inputClass} />}
        <button disabled={busy || (!inv.verified && !months[inv.invoiceId])} className={buttonClass} onClick={() => {
          if (window.confirm(inv.verified ? "撤销本地认证记录？不会修改税务系统。" : "请确认已在税务系统完成认证。这里只登记认证结果，是否继续？")) void action(() => procurementChainApi.verifyInvoice(inv.invoiceId, !inv.verified, inv.verified ? "" : months[inv.invoiceId]), "本地认证记录已更新");
        }}>{inv.verified ? "撤销认证记录" : "登记已认证"}</button>
      </div> : <p className="text-slate-400">手工登记不代表已认证；导入对应税务发票后再核对。</p>}
    </div>)}
    {pickerOpen && <div className="space-y-2 rounded-lg border border-indigo-100 bg-indigo-50/30 p-2">
      <div className="flex items-center justify-between text-xs"><strong>选择进项发票</strong><button onClick={() => setPickerOpen(false)}>取消</button></div>
      <input aria-label="搜索关联单据" placeholder="搜索发票号或供应商" value={query} onChange={e => setQuery(e.target.value)} className={`${inputClass} w-full`} />
      <div className="max-h-60 space-y-1 overflow-y-auto">
        {busy && <p className="text-xs text-slate-400">正在读取…</p>}
        {taxCandidates.filter(row => `${row.invoiceNumber} ${row.sellerName}`.toLowerCase().includes(query.toLowerCase())).map(row => <button key={row.id} disabled={busy || invoices.some(inv => inv.invoiceKind === "tax" && inv.invoiceId === row.id)} className="flex w-full justify-between gap-2 rounded bg-white p-2 text-left text-xs hover:bg-indigo-50 disabled:opacity-40" onClick={() => void linkTax(row)}>
          <span>{row.invoiceNumber}<span className="block text-slate-400">{row.sellerName} · {row.issueDate?.slice(0, 10)}</span></span><span>{row.totalAmount ? money(Number(row.totalAmount)) : "未提供"}</span>
        </button>)}
        {!busy && taxCandidates.length === 0 && <p className="py-3 text-xs text-slate-500">暂无可选发票，请先导入税务清单并确认已开票。</p>}
      </div>
      <Link href={`/purchase/workbench?view=imports&tab=tax&order=${order.orderId}`} className="block text-xs text-indigo-600">导入原始清单 →</Link>
    </div>}
    <form className="space-y-2 border-t border-slate-100 pt-3" onSubmit={e => { e.preventDefault(); void action(async () => {
      const poId = await ensurePo();
      await mutate(`purchase/orders/${poId}/invoices`, "POST", { invoice_no: invoiceNo, invoice_amount: invoiceAmount, allocated_amount: allocatedAmount || invoiceAmount, invoice_date: invoiceDate ? `${invoiceDate}T00:00:00+08:00` : null });
      setInvoiceNo(""); setInvoiceAmount(""); setAllocatedAmount(""); setInvoiceDate("");
    }, "发票已登记并分摊到本单"); }}>
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-400">手工登记已有发票</h3>
        <span className="text-[10px] text-slate-300">发票不在税务清单内时使用</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input required aria-label="发票号码" placeholder="真实发票号码" value={invoiceNo} onChange={e => setInvoiceNo(e.target.value)} className={inputClass} />
        <input aria-label="开票日期" type="date" value={invoiceDate} onChange={e => setInvoiceDate(e.target.value)} className={inputClass} />
        <input required type="number" min="0.01" step="0.01" aria-label="票面金额" placeholder="票面金额" value={invoiceAmount} onChange={e => setInvoiceAmount(e.target.value)} className={inputClass} />
        <input type="number" min="0.01" step="0.01" aria-label="本单分摊金额" placeholder="本单分摊（默认全额）" value={allocatedAmount} onChange={e => setAllocatedAmount(e.target.value)} className={inputClass} />
      </div>
      <p className="text-[11px] text-slate-400">可一单多票、一票分多单。此操作仅登记，不会向税务系统开票。</p>
      <button disabled={busy} className={buttonClass}>登记并关联本单</button>
    </form>
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {message && <p role="status" className="text-xs text-emerald-600">{message}</p>}
  </section>;
}
