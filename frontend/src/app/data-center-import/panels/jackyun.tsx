"use client";

import { useCallback, useEffect, useState } from "react";
import { JackyunFileImportRow, JackyunRecordPreview, automationApi, jackyunFileApi } from "@/lib/api";
import { LifecyclePanel } from "./LifecyclePanel";

type SyncJobLite = { provider: string; jobType: string; startedAt?: string | null; status: string; errorSummary?: string | null };

/** 在线同步入库单（吉客云开放平台）：每日配额耗尽时自动禁用，引导改用下方文件导入 */
function JackyunOnlineSyncCard() {
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const [blockedReason, setBlockedReason] = useState("");
  const [message, setMessage] = useState("");

  // 吉客云测试环境每天 300 次配额耗尽后 inbound 任务会全部撞 0130020806；
  // 扫今天所有 inbound 任务，若最后一次成功之后全部撞同一道墙就禁用按钮
  const loadGuard = useCallback(async () => {
    try {
      const now = new Date();
      const todayKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
      const jobs: SyncJobLite[] = await automationApi.jobs(100);
      const todayJobs = jobs
        .filter((j) => j.provider === "jackyun" && j.jobType === "inbound" && (j.startedAt || "").startsWith(todayKey))
        .sort((a, b) => (a.startedAt || "").localeCompare(b.startedAt || ""));
      if (todayJobs.length === 0) {
        setBlocked(false);
        setBlockedReason("");
        return;
      }
      const isQuotaFailure = (j: SyncJobLite) => j.status === "failed" && /0130020806|测试期间每天最多调用/.test(j.errorSummary || "");
      let lastSuccess = -1;
      todayJobs.forEach((j, idx) => { if (j.status === "success") lastSuccess = idx; });
      const later = lastSuccess >= 0 ? todayJobs.slice(lastSuccess + 1) : todayJobs;
      if (later.length > 0 && later.every(isQuotaFailure)) {
        setBlocked(true);
        setBlockedReason(later.find(isQuotaFailure)?.errorSummary || "今日吉客云开放平台 300 次/日配额已耗尽");
      } else {
        setBlocked(false);
        setBlockedReason("");
      }
    } catch {
      // 守卫只作防误触提示，失败不阻塞页面
      setBlocked(false);
      setBlockedReason("");
    }
  }, []);

  useEffect(() => { void loadGuard(); }, [loadGuard]);

  async function syncInbound() {
    if (blocked) {
      setMessage("今日配额已耗尽（" + (blockedReason || "请走文件导入") + "），请使用下方文件导入");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const result = await automationApi.runJackyun("inbound");
      setMessage("吉客云入库单已加入同步队列，任务 " + result.taskId.slice(0, 8) + "…完成后数据自动更新");
    } catch (caught) {
      setMessage("同步入库单失败：" + String(caught));
    } finally {
      setBusy(false);
      void loadGuard();
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-medium text-gray-800">在线同步 · 入库单（开放平台）</div>
          <p className="mt-0.5 text-xs text-gray-500">直接从吉客云开放平台拉取入库单；每日配额耗尽时自动禁用，改用下方文件导入。</p>
        </div>
        <button
          disabled={busy || blocked}
          onClick={() => void syncInbound()}
          title={blocked ? blockedReason || "今日配额已耗尽" : undefined}
          className={"shrink-0 rounded-lg px-3.5 py-1.5 text-xs font-medium text-white disabled:opacity-50 " + (blocked ? "bg-amber-500 hover:bg-amber-600" : "bg-indigo-600 hover:bg-indigo-700")}
        >
          {busy ? "同步中…" : blocked ? "配额受限" : "立即同步"}
        </button>
      </div>
      {blocked && <p className="mt-2 rounded bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700">{blockedReason || "今日配额已耗尽"}——请使用下方文件导入。</p>}
      {message && <p className="mt-2 rounded bg-indigo-50 px-2.5 py-1.5 text-xs text-indigo-700">{message}</p>}
    </div>
  );
}

export function JackyunPanel() {
  return (
    <div>
      <JackyunOnlineSyncCard />
      <LifecyclePanel<JackyunFileImportRow, JackyunRecordPreview>
      title="吉客云客户端导出导入"
      description="从吉客云客户端导出的采购/库存/结算等报表，默认上传后自动生效，并继续执行字段映射、订单建档和采购链路关联。需要逐行核对时可关闭自动确认。"
      accept=".xlsx,.csv"
      fileHint="支持吉客云客户端导出的 XLSX / CSV，所有原始列均保留，确认后不影响后续字段映射。"
      api={{
        imports: jackyunFileApi.imports,
        upload: (file, autoConfirm) => jackyunFileApi.upload(file, autoConfirm),
        confirm: jackyunFileApi.confirm,
        softDelete: jackyunFileApi.softDelete,
        restore: jackyunFileApi.restore,
        records: jackyunFileApi.records,
        deleteRow: jackyunFileApi.deleteRow,
        restoreRow: jackyunFileApi.restoreRow,
      }}
      buildConfirmNotice={(row) => {
        const mr = row.mapResult;
        if (!mr) return "";
        if (!mr.ok) return `映射失败：${mr.error ?? "未知错误"}`;
        if (mr.skipped) return `报表已确认并存档：${mr.reason ?? "当前报表类型暂无自动映射"}。`;
        if (mr.mapper === "inbound_items") {
          const parts = [`保留 ${mr.sourceRows ?? 0} 条原始行`];
          parts.push(`回填 ${mr.matched ?? 0} 行入库明细`);
          parts.push(`覆盖 ${mr.documents ?? 0} 张入库单`);
          if (mr.filledFields) parts.push(`写入 ${mr.filledFields} 个字段（单价/数量/规格等）`);
          if (mr.createdExternalOrders) parts.push(`建立 ${mr.createdExternalOrders} 个其他渠道订单号`);
          if (mr.externalOrderNos?.length) parts.push(`订单号：${mr.externalOrderNos.join("、")}`);
          if (mr.createdLinks) parts.push(`新增并确认 ${mr.createdLinks} 条采购-入库关系`);
          if (mr.alreadyLinked) parts.push(`${mr.alreadyLinked} 条已有链路保留不变`);
          if (mr.missingRk?.length) parts.push(`缺少入库单：${mr.missingRk.join("、")}`);
          if (mr.rejectedLinks) parts.push(`${mr.rejectedLinks} 条已拒绝关系未覆盖`);
          if (mr.allocSeeded) parts.push(`反填 ${mr.allocSeeded} 条 SKU 分配`);
          return `入库申请单货品已处理：${parts.join("、")}。原始行不合并，只有同一订单与入库单关系不会重复建链。`;
        }
        if (mr.mapper === "purchase") {
          const parts = [`已生成采购单 ${mr.mapped ?? 0} 张`];
          if (mr.updated) parts.push(`更新 ${mr.updated} 张`);
          return `采购报表已确认并生效：${parts.join("、")}。可到采购工作台订单详情「吉客云采购单关联」匹配。`;
        }
        return "报表已确认并生效。";
      }}
      renderSummary={(row) => (
        <div className="min-w-0 text-sm">
          <div className="truncate font-medium text-gray-700">{row.originalName}</div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-gray-400">
            <span>{formatSize(row.size)}</span>
            <span>{row.rowCount} 行</span>
            <span>类型：{reportTypeLabel(row.reportType)}</span>
            <span>{row.uploader}</span>
            <span>{row.createdAt ? new Date(row.createdAt).toLocaleString("zh-CN") : "—"}</span>
          </div>
        </div>
      )}
      renderPreview={(row, rows, _loading, error, actions) => {
        if (error) return <div className="text-sm text-red-600">{error}</div>;
        if (!rows || rows.length === 0)
          return <div className="py-4 text-center text-sm text-gray-400">本次导入暂无可预览的原始行。</div>;
        const columns = Object.keys(rows[0].payload ?? {});
        const deletedCount = rows.filter((r) => r.rowStatus === "deleted").length;
        return (
          <div>
            <div className="mb-2 text-xs text-gray-500">
              报表类型：{reportTypeLabel(row.reportType)} · 按导入表格原样展示，共 {rows.length} 行
              {deletedCount > 0 && (
                <span className="ml-1 text-gray-400">（{deletedCount} 行已删除，置灰展示，可恢复）</span>
              )}
            </div>
            <div className="max-h-[480px] overflow-auto rounded-md border border-gray-200 bg-white">
              <table className="min-w-full text-xs">
                <thead className="sticky top-0 z-10 bg-gray-50 text-left text-gray-500">
                  <tr>
                    <th className="px-2 py-1.5 font-medium">#</th>
                    {columns.map((c) => (
                      <th key={c} className="whitespace-nowrap px-2 py-1.5 font-medium">{c}</th>
                    ))}
                    <th className="whitespace-nowrap px-2 py-1.5 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((r) => {
                    const deleted = r.rowStatus === "deleted";
                    const busy = actions?.busyRowKey === r.rowIndex;
                    return (
                      <tr key={r.rowIndex} className={deleted ? "bg-gray-50/80" : "hover:bg-indigo-50/30"}>
                        <td className="whitespace-nowrap px-2 py-1.5 text-gray-400">{r.rowIndex}</td>
                        {columns.map((c) => (
                          <td
                            key={c}
                            className={`max-w-48 truncate whitespace-nowrap px-2 py-1.5 ${
                              deleted ? "text-gray-300" : "text-gray-700"
                            }`}
                            title={r.payload[c]}
                          >
                            {r.payload[c] ?? ""}
                          </td>
                        ))}
                        <td className="whitespace-nowrap px-2 py-1.5 text-right">
                          {!actions?.editable ? null : deleted ? (
                            <button
                              onClick={() => actions.onRestoreRow(r.rowIndex)}
                              disabled={busy}
                              className="rounded px-1.5 py-0.5 text-xs text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
                            >
                              恢复
                            </button>
                          ) : (
                            <button
                              onClick={() => actions.onDeleteRow(r.rowIndex)}
                              disabled={busy}
                              className="rounded px-1.5 py-0.5 text-xs text-red-500 hover:bg-red-50 disabled:opacity-40"
                            >
                              {busy ? "处理中…" : "删除"}
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="mt-2 text-xs text-gray-400">
              不需要的行直接删除，已删行不进入业务数据；确认生效前可随时恢复。
            </div>
          </div>
        );
      }}
      />
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function reportTypeLabel(t: string): string {
  if (t === "purchase") return "采购";
  if (t === "inbound") return "入库";
  if (t === "settlement") return "结算";
  if (t === "inventory") return "库存";
  if (t === "sales") return "销售";
  return t || "未知";
}
