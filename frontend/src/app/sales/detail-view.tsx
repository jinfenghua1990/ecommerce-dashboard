"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from "react";
import { AftersaleRow, authenticatedFetch, dashboardApi } from "@/lib/api";

type DetailRow = {
  itemId: number;
  orderId: number;
  orderNo: string;
  netOrderNo: string;
  orderedAt: string | null;
  paidAt: string | null;
  platform: string;
  orderType: string;
  orderStatus: string;
  payStatus: string;
  settleStatus: string;
  warehouse: string;
  logisticsNo: string;
  logisticsCompany: string;
  buyerNote: string;
  goodsCount: number | null;
  goodsCost: number | null;
  grossProfit: number | null;
  skuCode: string;
  goodsName: string;
  spec: string;
  unitName: string;
  gift: string;
  quantity: number | null;
  unitPrice: number | null;
  amount: number | null;
  discountAmount: number | null;
  orderAmount: number | null;
  paidAmount: number | null;
};

type DetailResp = {
  rows: DetailRow[];
  total: number;
  page: number;
  pageSize: number;
  platforms: string[];
  statuses: string[];
};

/** 可显示的全部字段（订单 × 货品行级），顺序即列顺序 */
const COLUMNS: { key: keyof DetailRow; label: string; kind?: "money" | "num"; mono?: boolean }[] = [
  { key: "orderNo", label: "订单号", mono: true },
  { key: "netOrderNo", label: "网店订单号", mono: true },
  { key: "orderedAt", label: "下单时间" },
  { key: "paidAt", label: "付款时间" },
  { key: "platform", label: "销售渠道" },
  { key: "orderType", label: "订单类型" },
  { key: "orderStatus", label: "订单状态" },
  { key: "payStatus", label: "付款状态" },
  { key: "settleStatus", label: "结算状态" },
  { key: "warehouse", label: "发货仓库" },
  { key: "logisticsCompany", label: "物流公司" },
  { key: "logisticsNo", label: "物流单号", mono: true },
  { key: "buyerNote", label: "买家留言" },
  { key: "skuCode", label: "货品编号", mono: true },
  { key: "goodsName", label: "货品名称" },
  { key: "spec", label: "规格" },
  { key: "unitName", label: "单位" },
  { key: "gift", label: "赠品" },
  { key: "quantity", label: "数量", kind: "num" },
  { key: "unitPrice", label: "单价", kind: "money" },
  { key: "amount", label: "行金额", kind: "money" },
  { key: "discountAmount", label: "优惠金额", kind: "money" },
  { key: "goodsCount", label: "订单货品数", kind: "num" },
  { key: "orderAmount", label: "订单金额", kind: "money" },
  { key: "paidAmount", label: "订单实付", kind: "money" },
  { key: "goodsCost", label: "订单成本", kind: "money" },
  { key: "grossProfit", label: "订单毛利", kind: "money" },
];

const DEFAULT_VISIBLE = ["orderNo", "orderedAt", "platform", "orderStatus", "skuCode", "goodsName", "spec", "quantity", "unitPrice", "amount", "discountAmount"];
const COLS_KEY = "salesDetailCols";
const WIDTHS_KEY = "salesDetailColWidths";
/** 默认列宽（px）：拖动调整后本地记忆，双击列宽手柄恢复该列默认 */
const DEFAULT_WIDTHS: Record<string, number> = {
  orderNo: 150, netOrderNo: 170, orderedAt: 130, paidAt: 130, platform: 190,
  orderType: 100, orderStatus: 120, payStatus: 90, settleStatus: 90,
  warehouse: 120, logisticsCompany: 110, logisticsNo: 150, buyerNote: 180,
  skuCode: 130, goodsName: 220, spec: 110, unitName: 70, gift: 70,
  quantity: 70, unitPrice: 90, amount: 100, discountAmount: 90, goodsCount: 90,
  orderAmount: 110, paidAmount: 110, goodsCost: 100, grossProfit: 100,
};
const MIN_W = 60;
const MAX_W = 520;

function fmtMoney(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
}

function cellText(row: DetailRow, col: (typeof COLUMNS)[number]): string {
  const v = row[col.key];
  if (col.kind === "money") return fmtMoney(v as number | null);
  if (col.kind === "num") return v === null || v === undefined ? "—" : String(v);
  const s = String(v ?? "");
  return s === "" ? "—" : s;
}

/** 全部销售明细：订单 × 货品行级台账，字段可自选（本地记忆），手工导入与 API 通道同库同源。 */
/** 总览点击穿透带来的初始筛选（平台/SKU/区间）。 */
export type DetailInitial = {
  platform?: string;
  sku?: string;
  start?: string;
  end?: string;
};

export default function SalesDetailView({ initial }: { initial?: DetailInitial } = {}) {
  const [data, setData] = useState<DetailResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [platform, setPlatform] = useState(initial?.platform ?? "");
  const [skuFilter, setSkuFilter] = useState(initial?.sku ?? "");
  const [status, setStatus] = useState("");
  const [startDate, setStartDate] = useState(initial?.start ?? "");
  const [endDate, setEndDate] = useState(initial?.end ?? "");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const firstLoad = useRef(true);
  // 列宽：手动拖拽调整，本地持久化（{} 表示全部用默认值）
  const [widths, setWidths] = useState<Record<string, number>>(() => {
    if (typeof window === "undefined") return {};
    try { return JSON.parse(window.localStorage.getItem(WIDTHS_KEY) || "{}") || {}; } catch { return {}; }
  });
  const dragRef = useRef<{ key: string; startX: number; startW: number } | null>(null);
  const [aftersales, setAftersales] = useState<AftersaleRow[]>([]);
  const [afOpen, setAfOpen] = useState(false);

  useEffect(() => {
    window.localStorage.setItem(WIDTHS_KEY, JSON.stringify(widths));
  }, [widths]);

  // 售后/退款明细：与筛选无关，首次展开时拉取
  useEffect(() => {
    if (!afOpen || aftersales.length > 0) return;
    dashboardApi.aftersales()
      .then(setAftersales)
      .catch(() => setAftersales([]));
  }, [afOpen, aftersales.length]);

  function colW(key: string): number {
    return widths[key] ?? DEFAULT_WIDTHS[key] ?? 120;
  }
  function resetWidth(key: string) {
    setWidths((prev) => { const next = { ...prev }; delete next[key]; return next; });
  }
  function resetAllWidths() { setWidths({}); }

  function startResize(e: ReactMouseEvent, key: string) {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = { key, startX: e.clientX, startW: colW(key) };
    const move = (ev: MouseEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const next = Math.min(MAX_W, Math.max(MIN_W, d.startW + ev.clientX - d.startX));
      setWidths((prev) => (prev[d.key] === next ? prev : { ...prev, [d.key]: next }));
    };
    const up = () => {
      dragRef.current = null;
      document.removeEventListener("mousemove", move);
      document.removeEventListener("mouseup", up);
    };
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
  }

  // 显示字段配置持久化在本地
  const [visible, setVisible] = useState<string[]>(() => {
    if (typeof window === "undefined") return DEFAULT_VISIBLE;
    try {
      const saved = JSON.parse(window.localStorage.getItem(COLS_KEY) || "");
      if (Array.isArray(saved) && saved.length > 0) {
        const valid = saved.filter((k: string) => COLUMNS.some((c) => c.key === k));
        if (valid.length > 0) return valid;
      }
    } catch { /* 忽略坏配置 */ }
    return DEFAULT_VISIBLE;
  });

  useEffect(() => {
    window.localStorage.setItem(COLS_KEY, JSON.stringify(visible));
  }, [visible]);

  useEffect(() => {
    if (!pickerOpen) return;
    const close = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) setPickerOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [pickerOpen]);

  const query = useMemo(() => {
    const p = new URLSearchParams();
    if (q.trim()) p.set("q", q.trim());
    if (platform) p.set("platform", platform);
    if (skuFilter) p.set("sku", skuFilter);
    if (status) p.set("status", status);
    if (startDate) p.set("start_date", startDate);
    if (endDate) p.set("end_date", endDate);
    p.set("page", String(page));
    p.set("page_size", String(pageSize));
    return p.toString();
  }, [q, platform, skuFilter, status, startDate, endDate, page, pageSize]);

  const load = useCallback(async () => {
    setLoading(true);
    setErr("");
    try {
      const res = await authenticatedFetch(`/api/v1/sales-file/detail?${query}`);
      const d = await res.json();
      if (!res.ok) throw new Error(d?.detail || "查询失败");
      setData(d);
    } catch (caught) {
      setErr(String(caught instanceof Error ? caught.message : caught));
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  // 首次加载后筛选条件变化回到第一页
  useEffect(() => {
    if (firstLoad.current) { firstLoad.current = false; return; }
    setPage(1);
  }, [q, platform, skuFilter, status, startDate, endDate, pageSize]);

  const cols = useMemo(
    () => COLUMNS.filter((c) => visible.includes(c.key as string)),
    [visible]
  );
  const totalW = useMemo(() => cols.reduce((acc, c) => acc + colW(c.key as string), 0), [cols, widths]);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;

  function toggleCol(key: string) {
    setVisible((prev) =>
      prev.includes(key)
        ? prev.filter((k) => k !== key)
        : COLUMNS.filter((c) => prev.includes(c.key as string) || c.key === key).map((c) => c.key as string)
    );
  }

  const inputCls = "rounded-md border border-slate-200 px-2 py-1 text-xs outline-none focus:border-indigo-400";

  return (
    <>
    <section className="mt-3 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { firstLoad.current = false; setPage(1); void load(); } }}
          placeholder="订单号 / 网店单号 / 货品编号 / 货品名称"
          className={`${inputCls} w-64`}
        />
        <select value={platform} onChange={(e) => setPlatform(e.target.value)} className={inputCls}>
          <option value="">全部渠道</option>
          {(data?.platforms ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} className={inputCls}>
          <option value="">全部状态</option>
          {(data?.statuses ?? []).map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className={inputCls} />
        <span className="text-xs text-slate-400">~</span>
        <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className={inputCls} />
        <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} className={inputCls}>
          {[20, 50, 100, 200].map((n) => <option key={n} value={n}>{n} 行/页</option>)}
        </select>
        <div className="relative" ref={pickerRef}>
          <button
            onClick={() => setPickerOpen((v) => !v)}
            className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-100"
          >
            显示字段（{cols.length}/{COLUMNS.length}）
          </button>
          {pickerOpen && (
            <div className="absolute right-0 z-30 mt-1 w-64 rounded-lg border border-slate-200 bg-white p-3 shadow-lg">
              <div className="mb-2 flex items-center justify-between text-[11px]">
                <button onClick={() => setVisible(COLUMNS.map((c) => c.key as string))} className="text-indigo-600 hover:underline">全选</button>
                <button onClick={() => setVisible(DEFAULT_VISIBLE)} className="text-slate-500 hover:underline">恢复默认</button>
                <button onClick={() => resetAllWidths()} className="text-slate-500 hover:underline">重置列宽</button>
              </div>
              <div className="grid max-h-72 grid-cols-1 gap-1 overflow-y-auto">
                {COLUMNS.map((c) => (
                  <label key={c.key} className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-xs text-slate-600 hover:bg-slate-50">
                    <input
                      type="checkbox"
                      checked={visible.includes(c.key as string)}
                      onChange={() => toggleCol(c.key as string)}
                      className="accent-indigo-600"
                    />
                    {c.label}
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
        <span className="text-xs text-slate-400">
          {loading ? "加载中…" : data ? `共 ${data.total.toLocaleString("zh-CN")} 行明细` : ""}
        </span>
      </div>

      {(skuFilter || platform || startDate || endDate) && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-slate-400">当前筛选：</span>
          {skuFilter && (
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 font-mono text-indigo-600">SKU {skuFilter}</span>
          )}
          {platform && (
            <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-indigo-600">{platform}</span>
          )}
          {(startDate || endDate) && <span className="text-slate-500">{startDate} ~ {endDate}</span>}
          <button
            onClick={() => { setSkuFilter(""); setPlatform(""); setStartDate(""); setEndDate(""); }}
            className="rounded border border-slate-200 px-1.5 py-0.5 text-slate-500 hover:bg-slate-50"
          >
            清除
          </button>
        </div>
      )}

      {err && <div className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">{err}</div>}

      <div className="mt-3 max-h-[62vh] overflow-auto rounded-lg border border-slate-100">
        <table className="text-left text-xs" style={{ width: totalW, minWidth: "100%", tableLayout: "fixed" }}>
          <colgroup>
            {cols.map((c) => <col key={c.key} style={{ width: colW(c.key as string) }} />)}
          </colgroup>
          <thead className="sticky top-0 z-10 bg-gray-50 text-gray-500">
            <tr>
              {cols.map((c) => (
                <th key={c.key} className={`relative whitespace-nowrap px-3 py-2 font-medium ${c.kind ? "text-right" : ""}`}>
                  <span className="block overflow-hidden text-ellipsis" title={c.label}>{c.label}</span>
                  <span
                    onMouseDown={(e) => startResize(e, c.key as string)}
                    onDoubleClick={() => resetWidth(c.key as string)}
                    title="拖动调整列宽，双击恢复默认"
                    className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize select-none bg-transparent transition-colors hover:bg-indigo-300"
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {(data?.rows ?? []).map((row) => {
              const cancelled = row.orderStatus.startsWith("已取消") || row.orderStatus.startsWith("作废");
              return (
                <tr key={row.itemId} className={`hover:bg-slate-50 ${cancelled ? "text-slate-400" : ""}`}>
                  {cols.map((c) => (
                    <td
                      key={c.key}
                      title={cellText(row, c)}
                      className={`overflow-hidden text-ellipsis whitespace-nowrap px-3 py-2 ${c.kind ? "text-right" : ""} ${c.mono ? "font-mono" : ""} ${
                        c.key === "orderStatus" && !cancelled ? "text-slate-600" : ""
                      }`}
                    >
                      {cellText(row, c)}
                    </td>
                  ))}
                </tr>
              );
            })}
            {!loading && (data?.rows ?? []).length === 0 && (
              <tr>
                <td colSpan={Math.max(1, cols.length)} className="py-10 text-center text-slate-400">
                  暂无明细——先在「业绩总览」导入《销售单查询》，或调整筛选条件
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
        <span>
          第 {data?.page ?? page} / {totalPages} 页
        </span>
        <span className="flex gap-1.5">
          <button
            disabled={page <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            className="rounded-md border border-slate-200 px-2.5 py-1 hover:bg-slate-50 disabled:opacity-40"
          >
            上一页
          </button>
          <button
            disabled={page >= totalPages || loading}
            onClick={() => setPage((p) => p + 1)}
            className="rounded-md border border-slate-200 px-2.5 py-1 hover:bg-slate-50 disabled:opacity-40"
          >
            下一页
          </button>
        </span>
      </div>
    </section>

    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <button onClick={() => setAfOpen((v) => !v)} className="flex w-full items-center justify-between text-left">
        <h2 className="text-sm font-medium text-gray-700">售后 / 退款（本地副本）</h2>
        <span className="text-[11px] text-slate-400">{afOpen ? "收起 ▲" : `展开 ▼ ${aftersales.length ? `共 ${aftersales.length} 条` : ""}`}</span>
      </button>
      {afOpen && (
        aftersales.length === 0 ? (
          <div className="mt-3 py-6 text-center text-xs text-slate-400">暂无售后数据</div>
        ) : (
          <div className="mt-3 max-h-96 overflow-auto rounded-lg border border-slate-100">
            <table className="w-full min-w-max text-left text-xs">
              <thead className="sticky top-0 bg-gray-50 text-gray-500">
                <tr>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">售后单号</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">原订单号</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">类型 / 状态</th>
                  <th className="whitespace-nowrap px-3 py-2 text-right font-medium">退款金额</th>
                  <th className="whitespace-nowrap px-3 py-2 font-medium">原因</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {aftersales.map((row) => (
                  <tr key={row.id} className="hover:bg-slate-50">
                    <td className="whitespace-nowrap px-3 py-2 font-mono">{row.aftersaleNo}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-500">{row.orderNo || "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2">{row.type || "—"} / {row.status || "—"}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-right font-medium">{fmtMoney(Number(row.refundAmount) || 0)}</td>
                    <td className="whitespace-nowrap px-3 py-2 text-slate-500">{row.reason || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </section>
    </>
  );
}
