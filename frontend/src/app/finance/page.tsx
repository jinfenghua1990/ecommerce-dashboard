"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Pkg = { id: number; version: number; status: string; sha256: string; createdAt: string | null };
type Period = {
  company: string;
  year: number;
  month: number;
  status: string;
  missing: Record<string, number>;
  fileCount: number;
  packages: Pkg[];
};
type FileRow = {
  id: number;
  category: string;
  originalName: string;
  size: number;
  sha256: string;
  version: number;
  uploader: string;
  uploadedAt: string | null;
};
type SalesField = { key: string; label: string; enabled: boolean };
type SalesTemplate = {
  id: number;
  company: string;
  name: string;
  enabled: boolean;
  fields: SalesField[];
  rules: Record<string, string>;
  toAddrs: string[];
  ccAddrs: string[];
  autoSend: boolean;
  sendDay: number;
  sendHour: number;
};
type SalesPreview = {
  year: number;
  month: number;
  rowCount: number;
  fields: SalesField[];
  summary: {
    orderCount: number;
    totalQuantity: string;
    salesAmount: string;
    refundAmount: string;
    netAfterRefund: string;
  };
  byPlatform: Array<{
    platform: string;
    orderCount: number;
    quantity: string;
    salesAmount: string;
    refundAmount: string;
    netAfterRefund: string;
  }>;
  rows: Array<Record<string, string>>;
};

const STATUS_STYLE: Record<string, string> = {
  INCOMPLETE: "bg-amber-50 text-amber-700 ring-amber-200",
  READY: "bg-sky-50 text-sky-700 ring-sky-200",
  PACKAGED: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  SENT: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ERROR: "bg-red-50 text-red-700 ring-red-200",
};
const CAT_LABEL: Record<string, string> = {
  bank: "银行资料",
  jackyun: "吉客云导出",
  invoice: "发票",
  sales_summary: "销售汇总",
  other: "其他",
};

function previousMonthValue() {
  const now = new Date();
  const d = new Date(now.getFullYear(), now.getMonth() - 1, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function emails(value: string) {
  return value.split(/[\s,;，；]+/).map((x) => x.trim()).filter(Boolean);
}

function money(value: string | undefined) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? `¥${n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "—";
}

export default function FinancePage() {
  const [periods, setPeriods] = useState<Period[]>([]);
  const [sel, setSel] = useState<{ year: number; month: number } | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [month, setMonth] = useState("");
  const [category, setCategory] = useState("bank");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [template, setTemplate] = useState<SalesTemplate | null>(null);
  const [reportMonth, setReportMonth] = useState(previousMonthValue());
  const [preview, setPreview] = useState<SalesPreview | null>(null);
  const [toText, setToText] = useState("");
  const [ccText, setCcText] = useState("");
  const [showFields, setShowFields] = useState(false);

  const loadTemplate = useCallback(async () => {
    const res = await authenticatedFetch("/api/v1/finance/sales-report/template", { cache: "no-store" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "销售汇总模板加载失败");
    setTemplate(data);
    setToText((data.toAddrs || []).join(", "));
    setCcText((data.ccAddrs || []).join(", "));
  }, []);

  const loadPeriods = useCallback(() => {
    authenticatedFetch("/api/v1/finance/periods", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: Period[]) => {
        setPeriods(d);
        if (!sel && d.length > 0) setSel({ year: d[0].year, month: d[0].month });
      })
      .catch(() => {});
  }, [sel]);

  const loadFiles = useCallback(() => {
    if (!sel) return;
    authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/files`, { cache: "no-store" })
      .then((r) => r.json())
      .then(setFiles)
      .catch(() => {});
  }, [sel]);

  useEffect(loadPeriods, [loadPeriods]);
  useEffect(loadFiles, [loadFiles]);
  useEffect(() => { void loadTemplate().catch((e) => setMsg(String(e))); }, [loadTemplate]);

  function updateField(index: number, values: Partial<SalesField>) {
    setTemplate((current) => current ? {
      ...current,
      fields: current.fields.map((field, i) => i === index ? { ...field, ...values } : field),
    } : current);
  }

  function moveField(index: number, direction: -1 | 1) {
    setTemplate((current) => {
      if (!current) return current;
      const target = index + direction;
      if (target < 0 || target >= current.fields.length) return current;
      const next = [...current.fields];
      [next[index], next[target]] = [next[target], next[index]];
      return { ...current, fields: next };
    });
  }

  async function saveTemplate() {
    if (!template) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch("/api/v1/finance/sales-report/template", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company: template.company,
          enabled: template.enabled,
          fields: template.fields,
          rules: template.rules,
          to_addrs: emails(toText),
          cc_addrs: emails(ccText),
          auto_send: template.autoSend,
          send_day: template.sendDay,
          send_hour: template.sendHour,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "保存失败");
      setTemplate(data); setToText((data.toAddrs || []).join(", ")); setCcText((data.ccAddrs || []).join(", "));
      setMsg("销售汇总模板已保存；后续月报和自动发送都会使用这份配置。");
    } catch (e) { setMsg(`保存失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  async function previewSales() {
    const [year, mm] = reportMonth.split("-").map(Number);
    if (!year || !mm) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/sales-report/preview?year=${year}&month=${mm}&limit=30`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "预览失败");
      setPreview(data);
    } catch (e) { setMsg(`预览失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); }
  }

  async function generateSales() {
    const [year, mm] = reportMonth.split("-").map(Number);
    if (!year || !mm) return;
    setBusy(true); setMsg("");
    try {
      const res = await authenticatedFetch(`/api/v1/finance/sales-report/generate?year=${year}&month=${mm}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "生成失败");
      setMsg(`销售汇总已生成并归档：${year}-${String(mm).padStart(2, "0")} · v${data.version}`);
      setSel({ year, month: mm });
      loadPeriods();
    } catch (e) { setMsg(`生成失败：${e instanceof Error ? e.message : String(e)}`); }
    finally { setBusy(false); loadFiles(); }
  }

  async function upload() {
    const f = fileRef.current?.files?.[0];
    if (!f || !month) { setMsg("请选择账期月份和文件"); return; }
    const [year, mm] = month.split("-").map(Number);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("period_year", String(year));
    fd.append("period_month", String(mm));
    fd.append("category", category);
    setBusy(true);
    try {
      const res = await authenticatedFetch("/api/v1/finance/files", { method: "POST", body: fd });
      const d = await res.json();
      if (res.ok) {
        setMsg(`已归档 v${d.version} · SHA256 ${d.sha256.slice(0, 16)}…`);
        if (fileRef.current) fileRef.current.value = "";
      } else setMsg(`失败：${d.detail}`);
    } finally { setBusy(false); loadPeriods(); loadFiles(); }
  }

  async function act(path: string, ok: (d: Record<string, unknown>) => string, body?: unknown) {
    if (!sel) return;
    setBusy(true);
    try {
      const res = await authenticatedFetch(`/api/v1/finance/${sel.year}/${sel.month}/${path}`, {
        method: "POST",
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
      const d = await res.json();
      setMsg(res.ok ? ok(d) : `失败：${d.detail ?? res.status}`);
    } finally { setBusy(false); loadPeriods(); loadFiles(); }
  }

  async function downloadPackage(pkg: Pkg, year: number, mm: number) {
    setBusy(true);
    try {
      const res = await authenticatedFetch(`/api/v1/finance/packages/${pkg.id}/download`);
      if (!res.ok) { const d = await res.json().catch(() => ({})); setMsg(`下载失败：${d.detail ?? res.status}`); return; }
      const blobUrl = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = blobUrl; link.download = `finance_${year}${String(mm).padStart(2, "0")}_V${pkg.version}.zip`;
      document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(blobUrl);
    } finally { setBusy(false); }
  }

  const current = periods.find((p) => sel && p.year === sel.year && p.month === sel.month);
  const enabledCount = template?.fields.filter((f) => f.enabled).length ?? 0;

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-end justify-between gap-4 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div>
          <div className="text-xs font-medium text-indigo-600">FINANCE / MONTHLY DELIVERY</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">财务资料</h1>
          <p className="mt-1 text-sm text-slate-500">销售汇总自动生成 · 原始资料版本归档 · 完整后打包发送</p>
        </div>
        <div className="text-right text-xs text-slate-400">当前模板：{template?.name || "加载中…"}</div>
      </header>

      {msg && <div className="rounded-xl bg-slate-100 px-4 py-3 text-sm text-slate-700">{msg}</div>}

      <section className="rounded-2xl border border-indigo-100 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">每月销售汇总</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">字段选一次后长期复用；数据每月自动从有效销售订单、商品明细和售后退款中计算。生成 Excel 后自动进入本月财务资料。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input type="month" value={reportMonth} onChange={(e) => setReportMonth(e.target.value)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm" />
            <button onClick={() => setShowFields((v) => !v)} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 hover:bg-slate-50">字段配置 · {enabledCount}列</button>
            <button onClick={previewSales} disabled={busy} className="rounded-lg border border-indigo-200 px-3 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-50">预览</button>
            <button onClick={generateSales} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">生成并归档 Excel</button>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[1.4fr_1fr]">
          <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4">
            <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold text-slate-700">发送配置</h3><span className="text-[11px] text-slate-400">保存在数据库，自动任务直接读取</span></div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-500">财务收件人
                <input value={toText} onChange={(e) => setToText(e.target.value)} placeholder="finance@example.com, accountant@example.com" className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" />
              </label>
              <label className="text-xs text-slate-500">抄送（可选）
                <input value={ccText} onChange={(e) => setCcText(e.target.value)} placeholder="多个邮箱用逗号分隔" className="mt-1.5 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm" />
              </label>
            </div>
            <div className="mt-3 flex flex-wrap items-end gap-3">
              <label className="inline-flex items-center gap-2 text-xs text-slate-600"><input type="checkbox" checked={template?.autoSend ?? false} onChange={(e) => setTemplate((t) => t ? { ...t, autoSend: e.target.checked } : t)} />自动发送</label>
              <label className="text-xs text-slate-500">每月第
                <input type="number" min={1} max={28} value={template?.sendDay ?? 3} onChange={(e) => setTemplate((t) => t ? { ...t, sendDay: Number(e.target.value) } : t)} className="ml-1 w-16 rounded border border-slate-200 bg-white px-2 py-1.5 text-sm" /> 日
              </label>
              <label className="text-xs text-slate-500">发送小时
                <select value={template?.sendHour ?? 10} onChange={(e) => setTemplate((t) => t ? { ...t, sendHour: Number(e.target.value) } : t)} className="ml-1 rounded border border-slate-200 bg-white px-2 py-1.5 text-sm">{Array.from({ length: 24 }, (_, i) => <option key={i} value={i}>{String(i).padStart(2, "0")}:00</option>)}</select>
              </label>
              <button onClick={saveTemplate} disabled={busy || !template} className="rounded-lg bg-slate-800 px-3 py-2 text-xs font-medium text-white hover:bg-slate-900 disabled:opacity-40">保存模板</button>
            </div>
            <p className="mt-3 text-[11px] leading-5 text-slate-400">到设定日期后，如果银行资料等尚未齐全，系统不会发空包；之后每天同一小时继续检查，资料齐全后自动发送一次。</p>
          </div>

          <div className="rounded-xl border border-slate-100 bg-slate-50/60 p-4 text-xs text-slate-600">
            <h3 className="text-sm font-semibold text-slate-700">当前计算口径</h3>
            <div className="mt-3 space-y-2">
              <div className="flex justify-between gap-3"><span>有效销售订单</span><b className="font-medium text-slate-800">已支付或已完成</b></div>
              <div className="flex justify-between gap-3"><span>商品净销售额</span><b className="font-medium text-slate-800">商品金额 − 优惠金额</b></div>
              <div className="flex justify-between gap-3"><span>退款</span><select value={template?.rules?.refund_mode ?? "recorded_non_cancelled"} onChange={(e) => setTemplate((t) => t ? { ...t, rules: { ...t.rules, refund_mode: e.target.value } } : t)} className="rounded border border-slate-200 bg-white px-2 py-1"><option value="recorded_non_cancelled">已记录有效退款</option><option value="ignore_refund">不计退款</option></select></div>
              <div className="flex justify-between gap-3"><span>订单级金额</span><b className="font-medium text-slate-800">只在首条商品行输出</b></div>
            </div>
          </div>
        </div>

        {showFields && template && <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-3"><h3 className="text-sm font-semibold text-slate-800">销售明细字段</h3><p className="mt-1 text-[11px] text-slate-400">勾选决定是否导出；名称可直接改；上下按钮决定 Excel 列顺序。</p></div>
          <div className="grid gap-2 lg:grid-cols-2">
            {template.fields.map((field, index) => <div key={field.key} className={`grid grid-cols-[28px_1fr_auto] items-center gap-2 rounded-lg border px-2 py-2 ${field.enabled ? "border-indigo-100 bg-indigo-50/30" : "border-slate-100"}`}>
              <input type="checkbox" checked={field.enabled} onChange={(e) => updateField(index, { enabled: e.target.checked })} />
              <div className="flex min-w-0 items-center gap-2"><span className="w-28 shrink-0 truncate font-mono text-[10px] text-slate-400">{field.key}</span><input value={field.label} onChange={(e) => updateField(index, { label: e.target.value })} className="min-w-0 flex-1 rounded border border-slate-200 bg-white px-2 py-1 text-xs" /></div>
              <div className="flex gap-1"><button onClick={() => moveField(index, -1)} disabled={index === 0} className="rounded border border-slate-200 px-2 py-1 text-[10px] text-slate-500 disabled:opacity-30">↑</button><button onClick={() => moveField(index, 1)} disabled={index === template.fields.length - 1} className="rounded border border-slate-200 px-2 py-1 text-[10px] text-slate-500 disabled:opacity-30">↓</button></div>
            </div>)}
          </div>
          <div className="mt-3 flex justify-end"><button onClick={saveTemplate} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white">保存字段配置</button></div>
        </div>}

        {preview && <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
          <div className="grid grid-cols-5 divide-x divide-slate-100 bg-white">
            <div className="p-3"><div className="text-[10px] text-slate-400">订单数</div><div className="mt-1 text-lg font-semibold">{preview.summary.orderCount}</div></div>
            <div className="p-3"><div className="text-[10px] text-slate-400">销售数量</div><div className="mt-1 text-lg font-semibold">{Number(preview.summary.totalQuantity).toLocaleString("zh-CN")}</div></div>
            <div className="p-3"><div className="text-[10px] text-slate-400">商品净销售额</div><div className="mt-1 text-lg font-semibold">{money(preview.summary.salesAmount)}</div></div>
            <div className="p-3"><div className="text-[10px] text-slate-400">有效退款</div><div className="mt-1 text-lg font-semibold text-amber-700">{money(preview.summary.refundAmount)}</div></div>
            <div className="p-3"><div className="text-[10px] text-slate-400">退款后净销售</div><div className="mt-1 text-lg font-semibold text-emerald-700">{money(preview.summary.netAfterRefund)}</div></div>
          </div>
          <div className="overflow-x-auto border-t border-slate-100 bg-white">
            <table className="w-full min-w-[900px] text-xs"><thead className="bg-slate-50 text-left text-slate-500"><tr>{preview.fields.map((f) => <th key={f.key} className="whitespace-nowrap px-3 py-2 font-medium">{f.label}</th>)}</tr></thead><tbody className="divide-y divide-slate-100">{preview.rows.slice(0, 12).map((row, i) => <tr key={i}>{preview.fields.map((f) => <td key={f.key} className="max-w-[220px] truncate px-3 py-2 text-slate-600">{row[f.key] || "—"}</td>)}</tr>)}</tbody></table>
            <div className="px-3 py-2 text-[10px] text-slate-400">预览前 {Math.min(12, preview.rows.length)} 行 · 本月共 {preview.rowCount} 行明细</div>
          </div>
        </div>}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4"><h2 className="text-base font-semibold text-slate-900">其他财务资料归档</h2><p className="mt-1 text-xs text-slate-500">银行资料、发票、吉客云原始导出等继续版本化保存；销售汇总由上方系统自动生成。</p></div>
        <div className="grid max-w-5xl grid-cols-1 items-end gap-3 md:grid-cols-4">
          <label className="text-xs text-gray-500">账期月份<input type="month" value={month} onChange={(e) => setMonth(e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm" /></label>
          <label className="text-xs text-gray-500">资料类别<select value={category} onChange={(e) => setCategory(e.target.value)} className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm">{Object.entries(CAT_LABEL).filter(([k]) => k !== "sales_summary").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label className="text-xs text-gray-500">文件（XLSX / PDF / ZIP）<input ref={fileRef} type="file" accept=".xlsx,.xls,.pdf,.zip,.csv" className="mt-1 w-full text-sm" /></label>
          <button onClick={upload} disabled={busy} className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50">上传归档</button>
        </div>
      </section>

      <section className="grid gap-5 xl:grid-cols-[420px_1fr]">
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-800">月度账期</h2>
          {periods.map((p) => <div key={`${p.year}-${p.month}`} onClick={() => setSel({ year: p.year, month: p.month })} className={`cursor-pointer rounded-xl border p-4 ${sel && p.year === sel.year && p.month === sel.month ? "border-indigo-300 bg-indigo-50/40" : "border-gray-200 bg-white"}`}>
            <div className="flex items-center justify-between"><div className="text-sm font-medium">{p.year} 年 {p.month} 月</div><span className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${STATUS_STYLE[p.status] ?? ""}`}>{p.status}</span></div>
            <div className="mt-1 text-xs text-gray-400">已归档 {p.fileCount} 个文件{Object.keys(p.missing).length > 0 && ` · 缺少：${Object.entries(p.missing).map(([c, n]) => `${CAT_LABEL[c] ?? c}×${n}`).join("、")}`}</div>
            {p.packages.map((pkg) => <button key={pkg.id} onClick={(e) => { e.stopPropagation(); void downloadPackage(pkg, p.year, p.month); }} disabled={busy} className="mt-2 mr-2 text-xs text-indigo-600 hover:underline">下载 ZIP V{pkg.version}（{pkg.status}）</button>)}
          </div>)}
          {!periods.length && <div className="rounded-xl border border-dashed border-slate-200 p-8 text-center text-sm text-slate-400">暂无账期</div>}
        </div>

        {sel ? <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-sm font-semibold text-slate-800">{sel.year} 年 {sel.month} 月 文件清单</h2><p className="mt-1 text-[11px] text-slate-400">{current?.company}</p></div>
            <div className="flex gap-2">
              <button onClick={() => act("check", (d) => `完整性检查：${d.status}`)} disabled={busy} className="rounded-lg bg-white px-2.5 py-1.5 text-xs text-gray-600 ring-1 ring-gray-200">检查完整性</button>
              <button onClick={() => act("package", (d) => `已生成 ZIP V${d.version}`)} disabled={busy} className="rounded-lg bg-white px-2.5 py-1.5 text-xs text-gray-600 ring-1 ring-gray-200">打包 ZIP</button>
              <button onClick={() => act("send", (d) => `已发送（${d.kind === "resent" ? "重发" : "首次"}）V${d.version}`, { to_addrs: emails(toText), cc_addrs: emails(ccText) })} disabled={busy || !emails(toText).length} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white hover:bg-emerald-700 disabled:opacity-40">发送给财务</button>
            </div>
          </div>
          <div className="mt-3 overflow-hidden rounded-xl border border-gray-200 bg-white">
            {!files.length ? <div className="p-8 text-center text-sm text-gray-400">该账期暂无归档文件</div> : <table className="w-full text-sm"><thead className="bg-gray-50 text-left text-xs text-gray-500"><tr><th className="px-4 py-2.5 font-medium">类别</th><th className="px-4 py-2.5 font-medium">文件名</th><th className="px-4 py-2.5 font-medium">版本</th><th className="px-4 py-2.5 font-medium">SHA256</th><th className="px-4 py-2.5 font-medium">归档时间</th><th className="px-4 py-2.5 font-medium">操作</th></tr></thead><tbody className="divide-y divide-gray-100">{files.map((r) => <tr key={r.id}><td className="px-4 py-2.5 text-xs">{CAT_LABEL[r.category] ?? r.category}</td><td className="px-4 py-2.5">{r.originalName}</td><td className="px-4 py-2.5 text-xs text-gray-500">v{r.version}</td><td className="px-4 py-2.5 font-mono text-xs text-gray-400">{r.sha256}…</td><td className="px-4 py-2.5 text-xs text-gray-400">{r.uploadedAt ? new Date(r.uploadedAt).toLocaleString("zh-CN") : "—"}</td><td className="px-4 py-2.5"><a href={`/api/v1/finance/files/${r.id}/download`} download={r.originalName} className="text-xs font-medium text-indigo-600 hover:underline">下载核对</a></td></tr>)}</tbody></table>}
          </div>
        </div> : <div className="rounded-xl border border-dashed border-slate-200 p-10 text-center text-sm text-slate-400">选择左侧账期查看财务资料</div>}
      </section>
    </div>
  );
}
