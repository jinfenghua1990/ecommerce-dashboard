"use client";

import { useEffect, useState } from "react";
import MetricCard from "@/components/metric-card";
import StatusBadge from "@/components/status-badge";
import { getOverview, Overview } from "@/lib/api";

const METRICS: { key: string; label: string; money?: boolean; percent?: boolean }[] = [
  { key: "salesAmount", label: "销售额", money: true },
  { key: "netSales", label: "净销售", money: true },
  { key: "orderCount", label: "订单数" },
  { key: "refundRate", label: "退款率", percent: true },
  { key: "grossProfit", label: "商品毛利", money: true },
  { key: "receivable", label: "应回款", money: true },
  { key: "received", label: "已回款", money: true },
  { key: "pendingReceive", label: "待回款", money: true },
];

type MonthlyOverview = Overview & { period?: string };

export default function OverviewPage() {
  const [data, setData] = useState<MonthlyOverview | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getOverview().then(setData).catch((e) => setErr(String(e)));
  }, []);

  if (err)
    return <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">后端不可达：{err}</div>;
  if (!data) return <div className="text-sm text-gray-400">加载中…</div>;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">经营总览</h1>
          <div className="mt-1 text-xs text-gray-400">
            {data.period ? `${data.period} 月度口径` : "月度口径"}
          </div>
        </div>
        <div className="text-xs text-gray-400">
          {data.phaseName} · 下一里程碑：{data.nextMilestone}
        </div>
      </div>

      <div className="mt-5 grid grid-cols-4 gap-4">
        {METRICS.map((m) => {
          const v = data.metrics?.[m.key];
          const display =
            v === null || v === undefined
              ? "—"
              : m.money
                ? `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`
                : m.percent
                  ? `${v}%`
                  : String(v);
          return (
            <MetricCard
              key={m.key}
              label={m.label}
              value={display}
              hint={v === null || v === undefined ? "本月数据未完整落地" : undefined}
            />
          );
        })}
        <MetricCard
          label="待处理异常"
          value={String(data.pendingExceptions)}
          hint={data.pendingExceptions > 0 ? "进入异常中心处理" : "无待处理异常"}
        />
      </div>

      <h2 className="mt-8 text-sm font-medium text-gray-700">数据连接</h2>
      <div className="mt-3 overflow-hidden rounded-xl border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-left text-xs text-gray-500">
            <tr>
              <th className="px-4 py-2.5 font-medium">系统</th>
              <th className="px-4 py-2.5 font-medium">接入方式</th>
              <th className="px-4 py-2.5 font-medium">状态</th>
              <th className="px-4 py-2.5 font-medium">最近测试</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {data.integrations.map((it) => (
              <tr key={it.id}>
                <td className="px-4 py-2.5">{it.name}</td>
                <td className="px-4 py-2.5 text-gray-500">{it.mode}</td>
                <td className="px-4 py-2.5">
                  <StatusBadge status={it.status} />
                  {it.errorSummary && (
                    <span className="ml-2 text-xs text-red-500" title={it.errorSummary}>
                      {it.errorSummary.slice(0, 60)}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-xs text-gray-400">
                  {it.lastTestedAt ? new Date(it.lastTestedAt).toLocaleString("zh-CN") : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
