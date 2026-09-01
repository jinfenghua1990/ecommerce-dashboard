"use client";

import { useEffect, useState } from "react";
import StatusBadge from "@/components/status-badge";
import { getOverview, IntegrationStatus, testJackyun } from "@/lib/api";

type TestState = { loading: boolean; result?: string; tools?: string[] };

export default function SettingsPage() {
  const [items, setItems] = useState<IntegrationStatus[]>([]);
  const [jackyun, setJackyun] = useState<TestState>({ loading: false });

  async function load() {
    const data = await getOverview();
    setItems(data.integrations);
  }
  useEffect(() => {
    load().catch(() => {});
  }, []);

  async function runTest() {
    setJackyun({ loading: true });
    try {
      const res = await testJackyun();
      if (res.ok) {
        setJackyun({ loading: false, result: "连接成功", tools: res.tools });
      } else {
        setJackyun({ loading: false, result: `失败：${res.error}` });
      }
    } catch (e) {
      setJackyun({ loading: false, result: `请求异常：${String(e)}` });
    }
    load();
  }

  return (
    <div>
      <h1 className="text-xl font-semibold">设置 · 数据连接</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        凭证只保存在服务器端（.env / 数据库加密），不回传前端。未配置的系统如实显示，不使用模拟数据伪装连接。
      </p>

      <div className="mt-6 grid max-w-3xl grid-cols-1 gap-3">
        {items.map((it) => (
          <div key={it.id} className="flex items-center justify-between rounded-xl border border-gray-200 bg-white p-4">
            <div>
              <div className="text-sm font-medium">{it.name}</div>
              <div className="mt-0.5 text-xs text-gray-400">
                {it.mode} · Phase {it.phase}
                {it.errorSummary ? ` · ${it.errorSummary.slice(0, 80)}` : ""}
              </div>
            </div>
            <StatusBadge status={it.status} />
          </div>
        ))}
      </div>

      <div className="mt-6 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">吉客云 MCP 连接测试</div>
        <p className="mt-1 text-xs text-gray-400">
          真实调用 MCP：initialize → tools/list，返回已订阅工具清单。失败原因会写入异常中心与同步日志。
        </p>
        <button
          onClick={runTest}
          disabled={jackyun.loading}
          className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {jackyun.loading ? "测试中…" : "立即测试连接"}
        </button>
        {jackyun.result && (
          <div className={`mt-3 text-sm ${jackyun.tools ? "text-emerald-700" : "text-red-600"}`}>
            {jackyun.result}
          </div>
        )}
        {jackyun.tools && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {jackyun.tools.map((t) => (
              <span key={t} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[11px] text-gray-600">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="mt-6 max-w-3xl rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800">
        局域网信任模式：同一内网设备均可访问本平台。请勿在路由器做端口转发，勿将 18080 暴露公网；
        未来如需公网/跨网访问，必须先恢复认证、权限隔离与 TLS。
      </div>
    </div>
  );
}
