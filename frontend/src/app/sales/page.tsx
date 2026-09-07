"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import MetricCard from "@/components/metric-card";
import { authenticatedFetch, dashboardApi, PlatformRow, SkuRow, TrendPoint } from "@/lib/api";
import type { DetailInitial } from "./detail-view";

const SalesDetailView = dynamic(() => import("./detail-view"), {
  ssr: false,
  loading: () => <div className="p-10 text-center text-[12px] text-slate-400">正在加载销售明细…</div>,
});

function fmtMoney(v: string | null): string {
  if (v === null || v === undefined) return "—";
  return `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
}

function fmtPct(part: number, total: number): string {
  if (!total) return "—";
  return `${((part / total) * 100).toFixed(1)}%`;
}

type RangeKey = "month" | "d30" | "pick_month" | "pick_year";

function localIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function addDaysIso(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return localIso(d);
}

/** 上一等长区间（环比基期）：[start, end] → 紧邻的前一长度区间。 */
function prevRange(start: string, end: string): { start: string; end: string; label: string } {
  const lenDays = Math.round((Date.parse(`${end}T00:00:00`) - Date.parse(`${start}T00:00:00`)) / 86400000) + 1;
  return { start: addDaysIso(start, -lenDays), end: addDaysIso(start, -1), label: `前${lenDays}天` };
}

/** 业绩总览：卖了什么、卖了多少钱（钱的口径，以导入销售清单为准）。 */
function SalesOverview({ onDrill }: { onDrill: (init: DetailInitial) => void }) {
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [platforms, setPlatforms] = useState<PlatformRow[]>([]);
  const [skus, setSkus] = useState<SkuRow[]>([]);
  const [prev, setPrev] = useState<{ sales: number; orders: number } | null>(null);
  const [err, setErr] = useState("");
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState("");
  const [importBad, setImportBad] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [rangeKey, setRangeKey] = useState<RangeKey>("d30");
  const [pickMonth, setPickMonth] = useState(() => {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
  const [pickYear, setPickYear] = useState(() => String(new Date().getFullYear()));

  // 顶部快速筛选 → 查询区间（闭区间，本地时区）
  const range = useMemo(() => {
    const now = new Date();
    const y = now.getFullYear();
    if (rangeKey === "month") {
      const start = `${y}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
      return { start, end: localIso(now), label: `当月 ${start.slice(0, 7)}` };
    }
    if (rangeKey === "d30") {
      const startD = new Date(now);
      startD.setDate(startD.getDate() - 29);
      return { start: localIso(startD), end: localIso(now), label: "近30天" };
    }
    if (rangeKey === "pick_month") {
      const [yy, mm] = pickMonth.split("-").map(Number);
      const lastDay = new Date(yy, mm, 0).getDate();
      return { start: `${pickMonth}-01`, end: `${pickMonth}-${String(lastDay).padStart(2, "0")}`, label: `月份 ${pickMonth}` };
    }
    return { start: `${pickYear}-01-01`, end: `${pickYear}-12-31`, label: `年份 ${pickYear}` };
  }, [rangeKey, pickMonth, pickYear]);

  // 月份/年份 ‹ › 逐期翻
  function stepPeriod(dir: 1 | -1) {
    if (rangeKey === "pick_month") {
      const [yy, mm] = pickMonth.split("-").map(Number);
      const d = new Date(yy, mm - 1 + dir, 1);
      setPickMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
    } else if (rangeKey === "pick_year") {
      setPickYear(String(Number(pickYear) + dir));
    }
  }
  const canStep = rangeKey === "pick_month" || rangeKey === "pick_year";
  const atLatest =
    rangeKey === "pick_month"
      ? pickMonth >= `${new Date().getFullYear()}-${String(new Date().getMonth() + 1).padStart(2, "0")}`
      : rangeKey === "pick_year" && Number(pickYear) >= new Date().getFullYear();

  /** 导入端口：吉客云《销售单查询》Excel（销售单+销售单货品双 sheet）→ 本地业绩数据源。 */
  async function importFile(file: File) {
    setImporting(true);
    setImportMsg("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await authenticatedFetch("/api/v1/sales-file/import", { method: "POST", body: fd });
      const d = await res.json();
      if (!res.ok) {
        setImportBad(true);
        setImportMsg(`导入失败：${d?.detail || "服务端拒绝"}`);
        return;
      }
      setImportBad(false);
      setImportMsg(
        `导入完成：有效订单 ${d.ordersImported} 单（新建 ${d.created} / 更新 ${d.updated}）· ` +
        `明细 ${d.itemsImported} 行 · 跳过取消/待审核 ${d.cancelledSkipped} 单` +
        (d.removedCancelled ? ` · 清除库内被标记取消 ${d.removedCancelled} 单` : "") +
        (d.minDate ? ` · 覆盖 ${d.minDate} ~ ${d.maxDate}` : "")
      );
      load();
    } catch {
      setImportBad(true);
      setImportMsg("导入请求异常");
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const load = useCallback(() => {
    const { start, end } = range;
    const p = prevRange(start, end);
    Promise.all([
      dashboardApi.salesTrend(365, start, end),
      dashboardApi.platformRanking(start, end),
      dashboardApi.skuRanking(10, start, end),
      dashboardApi.salesTrend(365, p.start, p.end),
    ])
      .then(([t, pl, s, pt]) => {
        setTrend(t);
        setPlatforms(pl);
        setSkus(s);
        setPrev({
          sales: pt.reduce((acc, x) => acc + (x.salesAmount ? Number(x.salesAmount) : 0), 0),
          orders: pt.reduce((acc, x) => acc + x.orders, 0),
        });
      })
      .catch((e) => setErr(String(e)));
  }, [range]);
  useEffect(load, [load]);

  // 跨度大（年份）时按月聚合趋势，按天太密
  const trendPoints = useMemo(() => {
    if (trend.length <= 90) return trend;
    const byMonth: Record<string, { date: string; orders: number; salesAmount: string }> = {};
    for (const p of trend) {
      const m = p.date.slice(0, 7);
      const cur = (byMonth[m] ||= { date: m, orders: 0, salesAmount: "0" });
      cur.orders += p.orders;
      cur.salesAmount = String(Number(cur.salesAmount) + Number(p.salesAmount || 0));
    }
    return Object.values(byMonth).sort((a, b) => a.date.localeCompare(b.date));
  }, [trend]);

  const totalSales = trend.reduce((acc, t) => acc + (t.salesAmount ? Number(t.salesAmount) : 0), 0);
  const totalOrders = trend.reduce((acc, t) => acc + t.orders, 0);
  // 条形按区间内最大单日归一化，否则单日只占总和的几个百分点、全部贴左看不清
  const maxPoint = Math.max(1, ...trendPoints.map((t) => Number(t.salesAmount) || 0));
  const platformTotal = platforms.reduce((acc, p) => acc + (Number(p.salesAmount) || 0), 0);
  const avgOrder = totalOrders ? totalSales / totalOrders : 0;

  // 环比：基期为 0 时不显示（避免除零假增长）
  const base = prevRange(range.start, range.end);
  const salesDelta = prev && prev.sales > 0 ? ((totalSales - prev.sales) / prev.sales) * 100 : null;
  const ordersDelta = prev && prev.orders > 0 ? ((totalOrders - prev.orders) / prev.orders) * 100 : null;
  const prevAvg = prev && prev.orders ? prev.sales / prev.orders : 0;
  const avgDelta = prevAvg > 0 && avgOrder > 0 ? ((avgOrder - prevAvg) / prevAvg) * 100 : null;

  const pillCls = (active: boolean) =>
    active
      ? "rounded-md bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white"
      : "rounded-md border border-slate-200 px-3 py-1 text-[11px] text-slate-600 hover:bg-slate-50";

  return (
    <>
      {/* 单行工具条：时间快速筛选 + 逐期快选 + 生效区间 + 导入 */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium text-slate-500">时间范围</span>
        <button onClick={() => setRangeKey("month")} className={pillCls(rangeKey === "month")}>当月</button>
        <button onClick={() => setRangeKey("d30")} className={pillCls(rangeKey === "d30")}>近30天</button>
        <button onClick={() => setRangeKey("pick_month")} className={pillCls(rangeKey === "pick_month")}>月份</button>
        <button onClick={() => setRangeKey("pick_year")} className={pillCls(rangeKey === "pick_year")}>年份</button>
        {rangeKey === "pick_month" && (
          <span className="flex items-center gap-1">
            <button onClick={() => stepPeriod(-1)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50">‹</button>
            <input
              type="month"
              value={pickMonth}
              onChange={(e) => setPickMonth(e.target.value)}
              className="rounded-md border border-slate-200 px-2 py-1 text-xs outline-none focus:border-indigo-400"
            />
            <button onClick={() => stepPeriod(1)} disabled={atLatest} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-30">›</button>
          </span>
        )}
        {rangeKey === "pick_year" && (
          <span className="flex items-center gap-1">
            <button onClick={() => stepPeriod(-1)} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50">‹</button>
            <input
              type="number"
              min={2000}
              max={2100}
              value={pickYear}
              onChange={(e) => setPickYear(e.target.value)}
              className="w-24 rounded-md border border-slate-200 px-2 py-1 text-xs outline-none focus:border-indigo-400"
            />
            <button onClick={() => stepPeriod(1)} disabled={atLatest} className="rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-30">›</button>
          </span>
        )}
        {canStep && <span className="text-[10px] text-slate-400">‹ › 逐期翻</span>}
        <span className="text-[11px] text-slate-400">{range.start} ~ {range.end}</span>
        <span className="mx-1 h-4 w-px bg-slate-200" />
        <button
          onClick={() => fileRef.current?.click()}
          disabled={importing}
          title="上传吉客云客户端导出的《销售单查询.xlsx》（含 销售单/销售单货品 两个 sheet），自动合并进本地销售业绩"
          className="rounded-md border border-indigo-300 bg-indigo-50 px-3 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100 disabled:opacity-50"
        >
          {importing ? "导入中…" : "导入销售清单"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void importFile(f);
          }}
        />
      </div>

      {importMsg && (
        <div className={`mt-2 text-[11px] ${importBad ? "text-red-600" : "text-emerald-700"}`}>{importMsg}</div>
      )}
      {err && <div className="mt-2 rounded-lg bg-red-50 p-2.5 text-xs text-red-700">{err}</div>}

      {/* 指标卡：销售额 / 订单数 / 客单价 / 平台数（环比基期 = 紧邻等长区间） */}
      <div className="mt-3 grid grid-cols-4 gap-3">
        <MetricCard label={`${range.label}销售额`} value={fmtMoney(totalSales ? String(totalSales) : null)} delta={salesDelta} deltaSuffix={`vs ${base.label}`} />
        <MetricCard label={`${range.label}订单数`} value={String(totalOrders)} delta={ordersDelta} deltaSuffix={`vs ${base.label}`} />
        <MetricCard label="客单价" value={avgOrder ? fmtMoney(String(avgOrder)) : "—"} delta={avgDelta} deltaSuffix={`vs ${base.label}`} />
        <MetricCard label="动销平台数" value={String(platforms.length)} hint={platforms.length ? undefined : "等订单同步落地"} />
      </div>

      {/* 趋势 + 平台排行 */}
      <div className="mt-3 grid grid-cols-2 gap-3">
        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">{range.label}销售趋势</h2>
          {trendPoints.length === 0 ? (
            <div className="mt-4 py-8 text-center text-sm text-gray-400">该区间暂无销售，导入《销售单查询》后自动出数</div>
          ) : (
            <div className="mt-3 space-y-1.5">
              {trendPoints.map((t) => (
                <div key={t.date} className="flex items-center gap-2 text-xs">
                  <span className="w-20 shrink-0 text-gray-500">{t.date}</span>
                  <div className="h-4 flex-1 overflow-hidden rounded bg-gray-100">
                    <div
                      className="h-full rounded bg-indigo-500"
                      style={{ width: `${Math.max(2, (Number(t.salesAmount) || 0) / maxPoint * 100)}%` }}
                    />
                  </div>
                  <span className="w-24 shrink-0 text-right font-mono text-gray-600">{fmtMoney(t.salesAmount)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">平台排行 <span className="ml-1 text-[11px] font-normal text-slate-400">占比 · 点击看明细</span></h2>
          {platforms.length === 0 ? (
            <div className="mt-4 py-8 text-center text-sm text-gray-400">暂无平台数据</div>
          ) : (
            <table className="mt-2 w-full text-sm">
              <tbody className="divide-y divide-gray-100">
                {platforms.map((p) => (
                  <tr
                    key={p.platform}
                    onClick={() => onDrill({ platform: p.platform, start: range.start, end: range.end })}
                    className="cursor-pointer hover:bg-indigo-50/50"
                    title={`查看 ${p.platform} 在本区间的销售明细`}
                  >
                    <td className="py-2 pr-2">{p.platform}</td>
                    <td className="w-28 py-2 pr-2">
                      <div className="h-1.5 overflow-hidden rounded bg-gray-100">
                        <div className="h-full rounded bg-indigo-400" style={{ width: `${Math.min(100, ((Number(p.salesAmount) || 0) / (platformTotal || 1)) * 100)}%` }} />
                      </div>
                    </td>
                    <td className="py-2 text-right text-gray-500">{p.orders} 单</td>
                    <td className="w-14 py-2 text-right text-[11px] text-gray-400">{fmtPct(Number(p.salesAmount) || 0, platformTotal)}</td>
                    <td className="py-2 pl-2 text-right font-mono font-medium">{fmtMoney(p.salesAmount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      {/* SKU 排行 */}
      <section className="mt-3 rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium text-gray-700">SKU 销售排行 <span className="ml-1 text-[11px] font-normal text-slate-400">Top {skus.length} · 点击看明细</span></h2>
          <span className="text-[11px] text-slate-400">合计 {fmtMoney(String(platformTotal))}（区间全部销售额）</span>
        </div>
        {skus.length === 0 ? (
          <div className="mt-2 py-6 text-center text-sm text-gray-400">暂无 SKU 数据</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs text-gray-500">
              <tr>
                <th className="w-8 py-2 font-medium">#</th>
                <th className="py-2 font-medium">SKU</th>
                <th className="py-2 font-medium">商品</th>
                <th className="py-2 text-right font-medium">订单数</th>
                <th className="py-2 text-right font-medium">销售额</th>
                <th className="w-16 py-2 text-right font-medium">占比</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {skus.map((s, i) => (
                <tr
                  key={s.skuCode}
                  onClick={() => onDrill({ sku: s.skuCode, start: range.start, end: range.end })}
                  className="cursor-pointer hover:bg-indigo-50/50"
                  title={`查看 ${s.goodsName} 在本区间的销售明细`}
                >
                  <td className="py-2 text-[11px] text-slate-400">{i + 1}</td>
                  <td className="py-2 font-mono text-xs">{s.skuCode}</td>
                  <td className="py-2">{s.goodsName}</td>
                  <td className="py-2 text-right text-gray-500">{s.orders}</td>
                  <td className="py-2 text-right font-mono font-medium">{fmtMoney(s.salesAmount)}</td>
                  <td className="py-2 text-right text-[11px] text-gray-400">{fmtPct(Number(s.salesAmount) || 0, platformTotal)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

export default function SalesPage() {
  const [tab, setTab] = useState<"overview" | "detail">("overview");
  // 穿透参数 + 自增 key：每次穿透强制重挂明细组件，保证初始筛选生效
  const [drill, setDrill] = useState<DetailInitial | undefined>(undefined);
  const [drillSeq, setDrillSeq] = useState(0);

  function handleDrill(init: DetailInitial) {
    setDrill(init);
    setDrillSeq((n) => n + 1);
    setTab("detail");
  }

  return (
    <div>
      {/* 页签：业绩总览 / 全部销售明细（标题由工作台外壳提供，不再重复） */}
      <div className="flex w-fit overflow-hidden rounded-lg border border-slate-200 bg-slate-50 text-[12px]">
        {([
          ["overview", "业绩总览"],
          ["detail", "全部销售明细"],
        ] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => {
              // 穿透状态下再点页签 = 回到完整明细（清掉穿透筛选并重挂）
              if (key === "detail" && drill) {
                setDrill(undefined);
                setDrillSeq((n) => n + 1);
              }
              setTab(key);
            }}
            className={key === tab ? "bg-indigo-600 px-4 py-1.5 font-medium text-white" : "px-4 py-1.5 text-slate-600 hover:bg-slate-100"}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "overview" ? (
        <SalesOverview key="overview" onDrill={handleDrill} />
      ) : (
        <SalesDetailView key={`detail-${drillSeq}`} initial={drill} />
      )}
    </div>
  );
}
