"use client";

import { useCallback, useEffect, useState } from "react";
import { automationApi, ScheduleItem, SyncJobRow, SyncLogRow } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  running: "bg-indigo-50 text-indigo-700",
  success: "bg-emerald-50 text-emerald-700",
  failed: "bg-red-50 text-red-700",
  skipped: "bg-gray-100 text-gray-500",
  pending: "bg-amber-50 text-amber-700",
  unknown: "bg-gray-100 text-gray-500",
};

const JOB_TYPE_LABEL: Record<string, string> = {
  sales: "订单/售后", online_orders: "线上订单", aftersales: "售后", inventory: "库存",
  products: "商品/SKU", price_lists: "价格", warehouses: "仓库",
  purchase: "采购", purchase_settlements: "采购结算", purchase_returns: "采购退货",
  inbound: "入库", outbound: "出库", connection_test: "连接测试", orders: "1688订单",
};

export default function AutomationPage() {
  const [schedule, setSchedule] = useState<ScheduleItem[]>([]);
  const [jobs, setJobs] = useState<SyncJobRow[]>([]);
  const [logs, setLogs] = useState<SyncLogRow[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    Promise.all([automationApi.schedule(), automationApi.jobs(30), automationApi.logs(80)])
      .then(([s, j, l]) => {
        setSchedule(s.items);
        setJobs(j);
        setLogs(l);
      })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  return (
    <div>
      <h1 className="text-xl font-semibold">自动化</h1>
      <p className="mt-1 text-sm text-gray-400">
        Celery Beat 定时同步。所有外部同步仅在凭证配置后真正执行；未配置如实跳过（见同步日志）。频率后台可配置列入下一迭代。
      </p>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}

      <section className="mt-5 rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-medium text-gray-700">定时任务（beat schedule）</h2>
        <table className="mt-2 w-full text-sm">
          <thead className="text-left text-xs text-gray-500">
            <tr>
              <th className="py-2 font-medium">任务</th>
              <th className="py-2 font-medium">频率</th>
              <th className="py-2 font-medium">执行</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {schedule.map((s) => (
              <tr key={`${s.task}-${s.args}`}>
                <td className="py-2 font-medium">{s.label}</td>
                <td className="py-2 text-gray-500">{s.frequency}</td>
                <td className="py-2 font-mono text-xs text-gray-400">{s.task}({s.args || ""})</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">最近同步任务</h2>
          {jobs.length === 0 ? (
            <div className="mt-3 py-6 text-center text-sm text-gray-400">暂无任务记录（beat 触发后出现）</div>
          ) : (
            <table className="mt-2 w-full text-sm">
              <tbody className="divide-y divide-gray-100">
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td className="py-2">
                      <span className="font-mono text-xs">{JOB_TYPE_LABEL[j.jobType] ?? j.jobType}</span>
                      <span className="ml-1 text-[10px] text-gray-400">({j.provider})</span>
                    </td>
                    <td className="py-2">
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs ${STATUS_STYLE[j.status] ?? STATUS_STYLE.unknown}`}>
                        {j.status}
                      </span>
                    </td>
                    <td className="py-2 text-right text-xs text-gray-400">
                      {j.startedAt ? new Date(j.startedAt).toLocaleString("zh-CN") : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">同步日志</h2>
          {logs.length === 0 ? (
            <div className="mt-3 py-6 text-center text-sm text-gray-400">暂无日志</div>
          ) : (
            <div className="mt-2 max-h-96 space-y-1.5 overflow-auto text-xs">
              {logs.map((l) => (
                <div key={l.id} className="flex gap-2">
                  <span className={`shrink-0 ${l.level === "error" ? "text-red-500" : l.level === "warn" ? "text-amber-500" : "text-gray-400"}`}>
                    [{l.level}]
                  </span>
                  <span className="flex-1 break-all text-gray-600">{l.message}</span>
                  <span className="shrink-0 text-gray-300">#{l.id}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
