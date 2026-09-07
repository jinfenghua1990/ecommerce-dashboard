"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alibaba1688BrowserJob,
  Alibaba1688BrowserStatus,
  alibaba1688BrowserApi,
} from "@/lib/api";
import StatusBadge from "@/components/status-badge";

const STATUS_LABELS: Record<string, string> = {
  connected: "已登录",
  needs_login: "待扫码登录",
  error: "异常",
  partial: "部分完成",
  untested: "未验证",
  unconfigured: "未启用",
};

function fmtTime(t: string | null): string {
  if (!t) return "—";
  try {
    return new Date(t).toLocaleString("zh-CN");
  } catch {
    return t;
  }
}

type RunningKind = "sync" | "login" | null;

export function Alibaba1688BrowserCard({ onSynced }: { onSynced?: () => void }) {
  const [status, setStatus] = useState<Alibaba1688BrowserStatus | null>(null);
  const [running, setRunning] = useState<RunningKind>(null);
  const [message, setMessage] = useState<{ text: string; tone: "info" | "ok" | "warn" | "error" } | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const mountedRef = useRef(true);

  const refreshStatus = useCallback(async () => {
    try {
      setStatus(await alibaba1688BrowserApi.status());
    } catch {
      // 状态读取失败不打断页面
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    refreshStatus();
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refreshStatus]);

  // 轮询最近的 SyncJob，直到本次触发的任务结束（成功/失败/待登录）。
  const pollJob = useCallback(
    (kind: Exclude<RunningKind, null>, baselineId: number) => {
      const jobType = kind === "sync" ? "browser_orders" : "browser_login";
      const deadline = Date.now() + (kind === "sync" ? 6 : 6.5) * 60 * 1000;
      timerRef.current && clearInterval(timerRef.current);
      timerRef.current = setInterval(async () => {
        if (Date.now() > deadline) {
          if (timerRef.current) clearInterval(timerRef.current);
          if (mountedRef.current) {
            setRunning(null);
            setMessage({ text: "等待任务结果超时，请稍后刷新查看同步日志", tone: "warn" });
          }
          return;
        }
        try {
          const jobs = await alibaba1688BrowserApi.jobs(8);
          const job = jobs.find((j) => j.jobType === jobType && j.id > baselineId);
          if (!job) return; // 任务还在队列中
          if (job.status === "running") return;
          if (timerRef.current) clearInterval(timerRef.current);
          if (!mountedRef.current) return;
          setRunning(null);
          refreshStatus();
          if (kind === "sync") {
            onSynced?.();
            if (job.status === "success" || job.status === "partial") {
              const s = job.stats || {};
              const match = (s.remarkMatch || {}) as Record<string, unknown>;
              const linked = Number(match.linked || 0);
              const unverified = Number(match.unverified || 0);
              const matchText = linked > 0
                ? `，备注已关联 ${linked} 单`
                : unverified > 0
                  ? `，备注待核验 ${unverified} 个`
                  : "";
              setMessage({
                text: `${job.status === "partial" ? "同步部分完成" : "同步完成"}：新增 ${s.created ?? 0} 单，状态更新 ${s.merged ?? 0} 单，翻 ${s.pagesVisited ?? 0} 页${matchText}`,
                tone: job.status === "partial" ? "warn" : "ok",
              });
            } else if (job.status === "needs_login" || job.status === "login_required") {
              setMessage({ text: "1688 登录态已失效，请点击「扫码登录」重新授权", tone: "warn" });
            } else {
              setMessage({ text: `同步结束（${job.status}）：${job.errorSummary || "详见同步日志"}`, tone: "error" });
            }
          } else if (job.status === "success") {
            setMessage({ text: "扫码登录成功，登录态已保存到服务器", tone: "ok" });
          } else {
            setMessage({ text: `登录未完成（${job.status}）：${job.errorSummary || "请重试"}`, tone: "warn" });
          }
        } catch {
          // 单次轮询失败忽略
        }
      }, 3000);
    },
    [refreshStatus, onSynced]
  );

  const startSync = useCallback(async () => {
    setMessage(null);
    setRunning("sync");
    try {
      let baselineId = 0;
      try {
        const jobs: Alibaba1688BrowserJob[] = await alibaba1688BrowserApi.jobs(1);
        baselineId = jobs[0]?.id ?? 0;
      } catch {
        // 拿不到基线就从 0 开始
      }
      await alibaba1688BrowserApi.sync();
      setMessage({ text: "已发起浏览器同步：正在服务器上打开 1688 订单页捕获数据…", tone: "info" });
      pollJob("sync", baselineId);
    } catch (e) {
      setRunning(null);
      setMessage({ text: "发起同步失败：" + String(e), tone: "error" });
    }
  }, [pollJob]);

  const startLogin = useCallback(async () => {
    setMessage(null);
    setRunning("login");
    try {
      let baselineId = 0;
      try {
        const jobs: Alibaba1688BrowserJob[] = await alibaba1688BrowserApi.jobs(1);
        baselineId = jobs[0]?.id ?? 0;
      } catch {
        // 拿不到基线就从 0 开始
      }
      const res = await alibaba1688BrowserApi.login();
      setMessage({ text: res.message || "请在 Mac mini 屏幕前完成扫码（超时 5 分钟）", tone: "info" });
      pollJob("login", baselineId);
    } catch (e) {
      setRunning(null);
      setMessage({ text: "发起登录失败：" + String(e), tone: "error" });
    }
  }, [pollJob]);

  const toneClass = {
    info: "text-gray-600",
    ok: "text-emerald-700",
    warn: "text-amber-700",
    error: "text-red-600",
  }[message?.tone ?? "info"];

  const badgeStatus = status?.status ?? "unconfigured";

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <span className="h-4 w-1 rounded-full bg-indigo-600" aria-hidden />
          <div>
            <div className="text-sm font-medium text-gray-800">在线同步 · 浏览器直采（主通道）</div>
            <div className="mt-0.5 text-xs text-gray-400">
              服务器 Chrome 持久登录，自动打开「已买到的货品」捕获订单；无需上传 Excel
            </div>
          </div>
          <StatusBadge status={badgeStatus} />
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={startSync}
            disabled={running !== null || !status?.enabled}
            className="rounded-lg bg-indigo-600 px-3.5 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {running === "sync" ? "同步进行中…" : "立即同步"}
          </button>
          <button
            onClick={startLogin}
            disabled={running !== null || !status?.enabled}
            className="rounded-lg border border-gray-300 bg-white px-3.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
          >
            {running === "login" ? "等待扫码…" : "扫码登录"}
          </button>
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-500">
        <span>
          登录账号：<b className="text-gray-700">{status?.account || "—"}</b>
        </span>
        <span>最近同步：{fmtTime(status?.lastSyncAt ?? null)}</span>
        {status?.lastSyncSummary && <span className="text-gray-400">{status.lastSyncSummary}</span>}
      </div>
      {message && <div className={`mt-2 text-xs ${toneClass}`}>{message.text}</div>}
      {!status?.enabled && (
        <div className="mt-2 text-xs text-gray-400">浏览器通道未启用（配置 ALIBABA_1688_BROWSER_ENABLED）</div>
      )}
      <div className="mt-2 text-[11px] leading-5 text-gray-400">
        增量策略：优先回看最近 {status?.lookbackDays ?? 7} 天，并用连续 {status?.stopAfterKnown ?? 15} 条已知订单作兜底；单次最多 {status?.maxPages ?? 10} 页。
        {STATUS_LABELS[badgeStatus] && badgeStatus === "needs_login" ? " 登录态失效时点「扫码登录」到服务器完成一次扫码即可。" : ""}
      </div>
    </div>
  );
}
