"use client";

import Link from "next/link";
import { ReactNode, useEffect, useMemo, useState } from "react";
import StatusBadge from "@/components/status-badge";
import { AuthUser, fetchMe, getOverview, Overview } from "@/lib/api";

type MonthlyOverview = Overview & { period?: string };

type DashboardMetricProps = {
  label: string;
  value: string;
  hint: string;
  symbol: string;
  accent: "blue" | "green" | "orange" | "violet";
};

const ACCENTS = {
  blue: {
    card: "from-[#eef5ff] to-[#f8fbff]",
    icon: "bg-[#dbeafe] text-[#2563eb]",
    line: "bg-[#3b82f6]",
  },
  green: {
    card: "from-[#ecfbf5] to-[#f7fdfb]",
    icon: "bg-[#d8f7e9] text-[#10a56e]",
    line: "bg-[#24b887]",
  },
  orange: {
    card: "from-[#fff5e9] to-[#fffaf4]",
    icon: "bg-[#ffead2] text-[#f28a24]",
    line: "bg-[#f59e42]",
  },
  violet: {
    card: "from-[#f2efff] to-[#fbfaff]",
    icon: "bg-[#e7e0ff] text-[#7657e8]",
    line: "bg-[#8b6cf4]",
  },
};

function toNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function money(value: unknown) {
  const n = toNumber(value);
  if (n === null) return "—";
  return `¥${n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function integer(value: unknown) {
  const n = toNumber(value);
  if (n === null) return "—";
  return Math.round(n).toLocaleString("zh-CN");
}

function percentage(value: unknown) {
  const n = toNumber(value);
  if (n === null) return "—";
  return `${n.toFixed(2)}%`;
}

function clampPct(value: number) {
  return Math.max(0, Math.min(100, value));
}

function DashboardMetric({ label, value, hint, symbol, accent }: DashboardMetricProps) {
  const style = ACCENTS[accent];
  return (
    <div className={`relative overflow-hidden rounded-2xl border border-white/70 bg-gradient-to-br ${style.card} p-4 shadow-[0_8px_24px_rgba(31,55,86,0.05)] ring-1 ring-slate-100`}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-medium text-slate-500">{label}</div>
          <div className="mt-2 truncate text-[24px] font-semibold tracking-tight text-[#17233c]">{value}</div>
          <div className="mt-2 text-[11px] text-slate-400">{hint}</div>
        </div>
        <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-sm font-semibold ${style.icon}`}>
          {symbol}
        </div>
      </div>
      <div className="mt-4 h-1 overflow-hidden rounded-full bg-white/70">
        <div className={`h-full w-2/3 rounded-full ${style.line}`} />
      </div>
    </div>
  );
}

function SectionCard({ title, action, children, className = "" }: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-slate-200/80 bg-white shadow-[0_8px_26px_rgba(31,55,86,0.045)] ${className}`}>
      <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-4">
        <h2 className="text-sm font-semibold text-[#17233c]">{title}</h2>
        {action}
      </div>
      {children}
    </section>
  );
}

function RatioRow({ label, value, display, colorClass }: { label: string; value: number; display: string; colorClass: string }) {
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-4 text-xs">
        <span className="text-slate-500">{label}</span>
        <span className="font-medium text-[#17233c]">{display}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${colorClass}`} style={{ width: `${clampPct(value)}%` }} />
      </div>
    </div>
  );
}

function integrationBucket(status: string) {
  const normalized = String(status || "").toLowerCase();
  if (["connected", "success", "ready", "active", "available", "ok"].some((key) => normalized.includes(key))) return "healthy";
  if (["error", "failed", "blocked", "need_login", "need_user_verify", "invalid"].some((key) => normalized.includes(key))) return "error";
  return "warning";
}

const QUICK_ACTIONS = [
  { href: "/purchase/workbench", label: "采购订单", symbol: "+", className: "bg-[#eaf3ff] text-[#2774e8]" },
  { href: "/supply-chain/production", label: "生产订单", symbol: "产", className: "bg-[#f1edff] text-[#785ce8]" },
  { href: "/supply-chain/warehouses", label: "仓库管理", symbol: "仓", className: "bg-[#fff3e5] text-[#f09532]" },
  { href: "/products", label: "货品档案", symbol: "品", className: "bg-[#eaf3ff] text-[#2774e8]" },
  { href: "/finance", label: "财务中心", symbol: "财", className: "bg-[#eafaf3] text-[#18a873]" },
  { href: "/exceptions", label: "异常中心", symbol: "!", className: "bg-[#fff0ef] text-[#ef655f]" },
];

export default function OverviewPage() {
  const [data, setData] = useState<MonthlyOverview | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    setNow(new Date());
    getOverview().then(setData).catch((e) => setErr(String(e)));
    fetchMe().then(setUser).catch(() => {});
  }, []);

  const metrics = data?.metrics || {};
  const salesAmount = toNumber(metrics.salesAmount);
  const netSales = toNumber(metrics.netSales);
  const grossProfit = toNumber(metrics.grossProfit);
  const refundRate = toNumber(metrics.refundRate);
  const received = toNumber(metrics.received);
  const pendingReceive = toNumber(metrics.pendingReceive);

  const grossMargin = netSales && grossProfit !== null ? (grossProfit / netSales) * 100 : 0;
  const collectionBase = (received || 0) + (pendingReceive || 0);
  const collectionProgress = collectionBase > 0 ? ((received || 0) / collectionBase) * 100 : 0;

  const integrationStats = useMemo(() => {
    const integrations = data?.integrations || [];
    const stats = { healthy: 0, warning: 0, error: 0, total: integrations.length };
    integrations.forEach((item) => {
      stats[integrationBucket(item.status)] += 1;
    });
    return stats;
  }, [data]);

  const healthyPct = integrationStats.total ? (integrationStats.healthy / integrationStats.total) * 100 : 0;
  const warningPct = integrationStats.total ? (integrationStats.warning / integrationStats.total) * 100 : 0;
  const donutBackground = integrationStats.total
    ? `conic-gradient(#4f8df7 0 ${healthyPct}%, #f2b24f ${healthyPct}% ${healthyPct + warningPct}%, #ef6f6c ${healthyPct + warningPct}% 100%)`
    : "#e8edf4";

  const todos = useMemo(() => {
    if (!data) return [];
    const rows: { label: string; count?: string; href: string; tone: string }[] = [];
    if (data.pendingExceptions > 0) {
      rows.push({ label: "待处理异常", count: String(data.pendingExceptions), href: "/exceptions", tone: "bg-[#fff0ef] text-[#ee625d]" });
    }
    if ((pendingReceive || 0) > 0) {
      rows.push({ label: "待回款金额", count: money(pendingReceive), href: "/payments", tone: "bg-[#fff5e7] text-[#e98c27]" });
    }
    if (integrationStats.error > 0) {
      rows.push({ label: "数据连接异常", count: String(integrationStats.error), href: "/settings", tone: "bg-[#fff0ef] text-[#ee625d]" });
    }
    if (grossProfit === null) {
      rows.push({ label: "本月毛利数据待完善", href: "/profit", tone: "bg-[#eef5ff] text-[#3377df]" });
    }
    if (rows.length === 0) {
      rows.push({ label: "当前没有需要处理的重点事项", href: "/", tone: "bg-[#eaf9f2] text-[#179d6a]" });
    }
    return rows;
  }, [data, pendingReceive, integrationStats.error, grossProfit]);

  if (err) {
    return <div className="rounded-2xl border border-red-100 bg-red-50 p-5 text-sm text-red-700">后端不可达：{err}</div>;
  }
  if (!data) return <div className="text-sm text-slate-400">经营数据加载中…</div>;

  const hour = now?.getHours();
  const greeting = hour === undefined ? "你好" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const displayName = user?.displayName || user?.username || "Admin";
  const dateText = now
    ? now.toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "long" })
    : "";

  return (
    <div className="mx-auto max-w-[1580px] pb-8">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-[27px] font-semibold tracking-tight text-[#14213a]">
              {greeting}，{displayName}
            </h1>
            <span className="text-xl" aria-hidden="true">👋</span>
          </div>
          <p className="mt-1 text-sm text-slate-500">从采购到入库，再到经营与财务，全流程数据统一查看</p>
        </div>
        <div className="flex flex-col items-end gap-2 text-right">
          <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-500 shadow-sm">{dateText}</div>
          <div className="text-[11px] text-slate-400">
            {data.period ? `${data.period} 月度口径` : "月度口径"} · {data.phaseName} · 下一里程碑：{data.nextMilestone}
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
        <DashboardMetric label="本月销售额" value={money(metrics.salesAmount)} hint="真实销售数据自动汇总" symbol="¥" accent="blue" />
        <DashboardMetric label="本月订单数" value={integer(metrics.orderCount)} hint="当前月度有效订单" symbol="单" accent="green" />
        <DashboardMetric label="待回款" value={money(metrics.pendingReceive)} hint="进入回款与对账处理" symbol="回" accent="orange" />
        <DashboardMetric label="待处理异常" value={String(data.pendingExceptions)} hint={data.pendingExceptions > 0 ? "需要进入异常中心处理" : "当前无待处理异常"} symbol="!" accent="violet" />
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-12">
        <SectionCard title="经营健康度" className="xl:col-span-8" action={<Link href="/profit" className="text-xs font-medium text-[#3478df] hover:text-[#1f63c7]">查看利润分析 →</Link>}>
          <div className="grid gap-6 p-5 md:grid-cols-2">
            <RatioRow label="商品毛利率" value={grossMargin} display={grossProfit === null ? "待完善" : `${grossMargin.toFixed(2)}%`} colorClass="bg-[#4f8df7]" />
            <RatioRow label="回款进度" value={collectionProgress} display={collectionBase > 0 ? `${collectionProgress.toFixed(2)}%` : "—"} colorClass="bg-[#28b487]" />
            <RatioRow label="退款率" value={refundRate || 0} display={percentage(metrics.refundRate)} colorClass="bg-[#f2b24f]" />
            <RatioRow label="数据连接健康度" value={healthyPct} display={integrationStats.total ? `${integrationStats.healthy}/${integrationStats.total}` : "—"} colorClass="bg-[#8367ed]" />
          </div>
          <div className="grid grid-cols-2 border-t border-slate-100 md:grid-cols-4">
            {[
              ["净销售", money(metrics.netSales)],
              ["商品毛利", money(metrics.grossProfit)],
              ["已回款", money(metrics.received)],
              ["应回款", money(metrics.receivable)],
            ].map(([label, value]) => (
              <div key={label} className="border-r border-slate-100 px-5 py-4 last:border-r-0">
                <div className="text-[11px] text-slate-400">{label}</div>
                <div className="mt-1 text-sm font-semibold text-[#17233c]">{value}</div>
              </div>
            ))}
          </div>
        </SectionCard>

        <SectionCard title="快速操作" className="xl:col-span-4">
          <div className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">
            {QUICK_ACTIONS.map((item) => (
              <Link key={item.href} href={item.href} className="group rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-center transition-all hover:-translate-y-0.5 hover:border-slate-200 hover:bg-white hover:shadow-md">
                <div className={`mx-auto flex h-10 w-10 items-center justify-center rounded-xl text-sm font-semibold ${item.className}`}>{item.symbol}</div>
                <div className="mt-2 text-xs font-medium text-slate-600 group-hover:text-[#1f5fbf]">{item.label}</div>
              </Link>
            ))}
          </div>
        </SectionCard>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-12">
        <SectionCard title="数据连接状态" className="xl:col-span-4" action={<Link href="/settings" className="text-xs font-medium text-[#3478df] hover:text-[#1f63c7]">连接设置 →</Link>}>
          <div className="flex items-center gap-6 p-5">
            <div className="relative h-32 w-32 shrink-0 rounded-full" style={{ background: donutBackground }}>
              <div className="absolute inset-[15px] flex flex-col items-center justify-center rounded-full bg-white shadow-inner">
                <div className="text-2xl font-semibold text-[#17233c]">{integrationStats.total}</div>
                <div className="text-[10px] text-slate-400">数据源</div>
              </div>
            </div>
            <div className="min-w-0 flex-1 space-y-3 text-xs">
              <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-slate-500"><i className="h-2.5 w-2.5 rounded-full bg-[#4f8df7]" />正常</span><b className="font-medium text-slate-700">{integrationStats.healthy}</b></div>
              <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-slate-500"><i className="h-2.5 w-2.5 rounded-full bg-[#f2b24f]" />待配置 / 注意</span><b className="font-medium text-slate-700">{integrationStats.warning}</b></div>
              <div className="flex items-center justify-between"><span className="flex items-center gap-2 text-slate-500"><i className="h-2.5 w-2.5 rounded-full bg-[#ef6f6c]" />异常</span><b className="font-medium text-slate-700">{integrationStats.error}</b></div>
            </div>
          </div>
        </SectionCard>

        <SectionCard title="数据连接明细" className="xl:col-span-5" action={<Link href="/data-center-import" className="text-xs font-medium text-[#3478df] hover:text-[#1f63c7]">数据接入 →</Link>}>
          <div className="divide-y divide-slate-100 px-5">
            {data.integrations.slice(0, 6).map((item) => (
              <div key={item.id} className="flex items-center gap-3 py-3">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-[11px] font-semibold text-slate-500 ring-1 ring-slate-100">源</div>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium text-slate-700">{item.name}</div>
                  <div className="mt-0.5 truncate text-[10px] text-slate-400">{item.mode}</div>
                </div>
                <StatusBadge status={item.status} />
              </div>
            ))}
            {data.integrations.length === 0 && <div className="py-8 text-center text-xs text-slate-400">暂无数据连接</div>}
          </div>
        </SectionCard>

        <SectionCard title="待办事项" className="xl:col-span-3" action={<Link href="/exceptions" className="text-xs font-medium text-[#3478df] hover:text-[#1f63c7]">查看全部 →</Link>}>
          <div className="divide-y divide-slate-100 px-4">
            {todos.map((item, index) => (
              <Link key={`${item.label}-${index}`} href={item.href} className="flex items-center gap-3 py-3.5 transition-colors hover:bg-slate-50/70">
                <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-xs font-semibold ${item.tone}`}>{index + 1}</div>
                <div className="min-w-0 flex-1 truncate text-xs text-slate-600">{item.label}</div>
                {item.count && <span className="shrink-0 rounded-full bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">{item.count}</span>}
              </Link>
            ))}
          </div>
        </SectionCard>
      </div>

      <div className="mt-4 rounded-2xl border border-slate-200/80 bg-white px-5 py-4 text-[11px] text-slate-400 shadow-[0_8px_26px_rgba(31,55,86,0.04)]">
        当前总览只展示已有真实业务数据；没有可靠数据来源的趋势、预测和订单列表不会使用模拟数据填充。
      </div>
    </div>
  );
}
