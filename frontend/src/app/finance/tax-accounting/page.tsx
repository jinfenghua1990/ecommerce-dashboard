"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type LedgerItem = {
  invoiceId: number;
  invoiceNumber: string;
  direction: string;
  status: string;
  issueDate: string | null;
  sellerName: string;
  buyerName: string;
  amountExclTax: string | null;
  taxAmount: string | null;
  totalAmount: string | null;
  verified: boolean;
  invoiceLine: {
    goodsName: string;
    specification: string;
    unit: string;
    quantity: string | null;
    unitPriceExclTax: string | null;
    taxRate: string | null;
    taxCode: string;
    detailComplete: boolean;
  };
  businessRefs: string[];
  businessAmount: string | null;
  businessQuantity: string | null;
  amountDifference: string | null;
  quantityDifference: string | null;
};

type Ledger = {
  period: string;
  policy: { note: string };
  summary: {
    invoiceCount: number;
    outputInvoiceCount: number;
    inputInvoiceCount: number;
    outputTotalAmount: string | null;
    outputTaxAmount: string | null;
    inputTotalAmount: string | null;
    verifiedInputTaxAmount: string | null;
    estimatedVatBeforeOtherAdjustments: string | null;
    amountDifferenceCount: number;
    quantityDifferenceCount: number;
    blockerCount: number;
    readyForAccountingDraft: boolean;
  };
  blockers: { invoiceId: number; invoiceNumber: string; reasons: string[] }[];
  items: LedgerItem[];
};

function previousMonth() {
  const now = new Date();
  const value = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`;
}

function money(value: string | null) {
  if (value === null) return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : value;
}

export default function TaxAccountingPage() {
  const [period, setPeriod] = useState(previousMonth);
  const [data, setData] = useState<Ledger | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [year, month] = period.split("-").map(Number);
    if (!year || !month) return;
    setLoading(true);
    setError("");
    try {
      const res = await authenticatedFetch(`/api/v1/tax-accounting/monthly-ledger?year=${year}&month=${month}`, { cache: "no-store" });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `加载失败（${res.status}）`);
      setData(payload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { void load(); }, [load]);

  const blockerByInvoice = useMemo(() => {
    const map = new Map<number, string[]>();
    data?.blockers.forEach((row) => map.set(row.invoiceId, row.reasons));
    return map;
  }, [data]);

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">TAX ACCOUNTING</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">税务做账</h1>
            <p className="mt-1 text-sm text-slate-500">开票为准 · 业务数据只做核对 · 不允许静默覆盖</p>
          </div>
          <div className="flex items-end gap-2">
            <label className="text-xs text-slate-500">账期
              <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} className="mt-1 block rounded-lg border border-slate-200 px-3 py-2 text-sm" />
            </label>
            <button onClick={() => void load()} disabled={loading} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
              {loading ? "核对中…" : "重新核对"}
            </button>
          </div>
        </div>
      </header>

      <section className="rounded-2xl border border-indigo-100 bg-indigo-50/50 px-5 py-4">
        <div className="text-sm font-semibold text-indigo-900">会计口径已固定</div>
        <p className="mt-1 text-sm leading-6 text-indigo-800">
          金额、税额以已确认的官方税务发票为准；数量、单价只有官方开票明细明确提供时才进入做账底稿。吉客云、1688 或手工数据出现冲突时，只显示差异，不覆盖开票数据。
        </p>
      </section>

      {error && <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}

      {data && (
        <>
          <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
            <Metric title="销项价税合计" value={money(data.summary.outputTotalAmount)} />
            <Metric title="销项税额" value={money(data.summary.outputTaxAmount)} />
            <Metric title="进项价税合计" value={money(data.summary.inputTotalAmount)} />
            <Metric title="已认证进项税" value={money(data.summary.verifiedInputTaxAmount)} />
            <Metric title="税额初步差额" value={money(data.summary.estimatedVatBeforeOtherAdjustments)} note="未计其他调整" />
            <Metric title="做账状态" value={data.summary.readyForAccountingDraft ? "可生成底稿" : "待核对"} note={`${data.summary.blockerCount} 项阻塞`} />
          </section>

          {data.blockers.length > 0 && (
            <section className="rounded-2xl border border-amber-200 bg-amber-50/50 p-5">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-base font-semibold text-amber-900">必须核对</h2>
                <span className="text-xs text-amber-700">金额差异 {data.summary.amountDifferenceCount} · 数量差异 {data.summary.quantityDifferenceCount}</span>
              </div>
              <div className="mt-3 grid gap-2 md:grid-cols-2">
                {data.blockers.slice(0, 12).map((row) => (
                  <div key={row.invoiceId} className="rounded-xl border border-amber-100 bg-white px-3 py-3 text-xs leading-5 text-slate-600">
                    <div className="font-medium text-slate-800">发票 {row.invoiceNumber}</div>
                    <div className="mt-1">{row.reasons.join("；")}</div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
            <table className="w-full min-w-[1500px] text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-3 py-2.5">状态</th>
                  <th className="px-3 py-2.5">发票</th>
                  <th className="px-3 py-2.5">开票项目</th>
                  <th className="px-3 py-2.5 text-right">开票数量</th>
                  <th className="px-3 py-2.5 text-right">开票单价(未税)</th>
                  <th className="px-3 py-2.5 text-right">不含税金额</th>
                  <th className="px-3 py-2.5 text-right">税额</th>
                  <th className="px-3 py-2.5 text-right">价税合计</th>
                  <th className="px-3 py-2.5">业务单</th>
                  <th className="px-3 py-2.5 text-right">业务金额</th>
                  <th className="px-3 py-2.5 text-right">金额差异</th>
                  <th className="px-3 py-2.5 text-right">数量差异</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((row) => {
                  const blocked = blockerByInvoice.has(row.invoiceId);
                  return (
                    <tr key={row.invoiceId} className={blocked ? "bg-amber-50/30" : ""}>
                      <td className="px-3 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-medium ${blocked ? "bg-amber-100 text-amber-800" : "bg-emerald-50 text-emerald-700"}`}>{blocked ? "待核对" : "开票真值"}</span></td>
                      <td className="px-3 py-3"><div className="font-medium text-slate-800">{row.invoiceNumber}</div><div className="mt-1 text-[10px] text-slate-400">{row.direction === "output" ? "销项" : row.direction === "input" ? "进项" : "方向待确认"} · {row.issueDate ? new Date(row.issueDate).toLocaleDateString("zh-CN") : "—"}</div></td>
                      <td className="max-w-[240px] px-3 py-3"><div className="truncate text-slate-700">{row.invoiceLine.goodsName || "—"}</div><div className="mt-1 text-[10px] text-slate-400">{row.invoiceLine.specification || ""}{row.invoiceLine.taxRate ? ` · 税率 ${row.invoiceLine.taxRate}%` : ""}</div></td>
                      <td className="px-3 py-3 text-right tabular-nums">{row.invoiceLine.quantity ?? "—"}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.invoiceLine.unitPriceExclTax)}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.amountExclTax)}</td>
                      <td className="px-3 py-3 text-right tabular-nums">{money(row.taxAmount)}</td>
                      <td className="px-3 py-3 text-right font-medium tabular-nums text-slate-900">{money(row.totalAmount)}</td>
                      <td className="max-w-[220px] px-3 py-3 text-xs text-slate-500">{row.businessRefs.join("、") || "未关联"}</td>
                      <td className="px-3 py-3 text-right tabular-nums text-slate-500">{money(row.businessAmount)}</td>
                      <td className={`px-3 py-3 text-right tabular-nums ${Number(row.amountDifference || 0) !== 0 ? "font-medium text-amber-700" : "text-slate-400"}`}>{money(row.amountDifference)}</td>
                      <td className={`px-3 py-3 text-right tabular-nums ${Number(row.quantityDifference || 0) !== 0 ? "font-medium text-amber-700" : "text-slate-400"}`}>{row.quantityDifference ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {data.items.length === 0 && <div className="p-8 text-center text-sm text-slate-400">该账期没有已确认的官方税务发票数据</div>}
          </section>
        </>
      )}
    </div>
  );
}

function Metric({ title, value, note }: { title: string; value: string; note?: string }) {
  return <div className="rounded-xl border border-slate-200 bg-white px-4 py-3"><div className="text-[11px] text-slate-500">{title}</div><div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>{note && <div className="mt-1 text-[10px] text-slate-400">{note}</div>}</div>;
}
