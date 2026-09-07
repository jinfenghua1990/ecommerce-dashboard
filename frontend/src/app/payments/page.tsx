"use client";

import { useCallback, useEffect, useState } from "react";
import MetricCard from "@/components/metric-card";
import { authenticatedFetch, reconApi, ReconOverview, RuleRow, Suggestion, SettlementRow } from "@/lib/api";

const CONFIDENCE_LABEL: Record<string, string> = { high: "高", medium: "中", low: "低" };
const CONFIDENCE_STYLE: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  medium: "bg-amber-50 text-amber-700 ring-amber-200",
  low: "bg-gray-100 text-gray-500 ring-gray-200",
};
const SETTLEMENT_STATUS: Record<string, string> = {
  open: "待回款",
  partial: "部分回款",
  settled: "已结清",
};

export default function PaymentsPage() {
  const [overview, setOverview] = useState<ReconOverview | null>(null);
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [settlements, setSettlements] = useState<SettlementRow[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const [rulePattern, setRulePattern] = useState("");
  const [rulePlatform, setRulePlatform] = useState("");
  const [newPlatform, setNewPlatform] = useState("");
  const [newPeriod, setNewPeriod] = useState("2026-08");
  const [newExpected, setNewExpected] = useState("");
  const [importPeriod, setImportPeriod] = useState("2026-08");
  const [importing, setImporting] = useState(false);

  async function importXlsx() {
    const input = document.getElementById("bank-xlsx") as HTMLInputElement | null;
    const f = input?.files?.[0];
    if (!f) {
      flash("请选择浙江农信交易明细 XLSX");
      return;
    }
    const [y, m] = importPeriod.split("-").map(Number);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("account_no", "ZJRC-001");
    fd.append("period_year", String(y));
    fd.append("period_month", String(m));
    setImporting(true);
    try {
      const res = await authenticatedFetch("/api/v1/reconciliation/import-bank", { method: "POST", body: fd });
      const d = await res.json();
      if (res.ok) {
        flash(`导入完成：解析 ${d.parsed} 行，新增 ${d.created} 条，重复跳过 ${d.duplicates} 条`);
        if (input) input.value = "";
        load();
      } else {
        flash(`导入失败：${d.detail}`);
      }
    } catch (e) {
      flash(`导入异常：${String(e)}`);
    } finally {
      setImporting(false);
    }
  }

  const load = useCallback(() => {
    reconApi.overview().then(setOverview).catch((e) => setErr(String(e)));
    reconApi.rules().then(setRules).catch(() => {});
    reconApi.settlements().then(setSettlements).catch(() => {});
    reconApi.suggestions().then(setSuggestions).catch(() => {});
  }, []);

  useEffect(load, [load]);

  function flash(text: string) {
    setMsg(text);
    setTimeout(() => setMsg(""), 3500);
  }

  async function addRule() {
    if (!rulePattern.trim() || !rulePlatform.trim()) return;
    await reconApi.createRule({
      match_pattern: rulePattern.trim(),
      match_type: "contains",
      platform: rulePlatform.trim(),
    });
    setRulePattern("");
    setRulePlatform("");
    load();
    flash("规则已添加（变更写入审计日志）");
  }

  async function delRule(id: number) {
    await reconApi.deleteRule(id);
    load();
    flash("规则已删除");
  }

  async function addSettlement() {
    const [y, m] = newPeriod.split("-").map(Number);
    if (!newPlatform.trim() || !newExpected) return;
    await reconApi.createSettlement({
      platform: newPlatform.trim(),
      period_year: y,
      period_month: m,
      expected_amount: newExpected,
    });
    setNewPlatform("");
    setNewExpected("");
    load();
    flash("应收结算已登记");
  }

  async function confirm(txnId: number, settlementId: number) {
    await reconApi.confirm(txnId, settlementId);
    load();
    flash("匹配已确认，应收状态已刷新");
  }

  async function reject(txnId: number, settlementId: number) {
    await reconApi.reject(txnId, settlementId);
    load();
    flash("匹配已拒绝");
  }

  const byPlatform = Object.entries(overview?.byPlatform ?? {});

  return (
    <div>
      <h1 className="text-xl font-semibold">回款</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        平台应结算/应收 vs 浙江农信实际到账 → 平台/日期/金额/户名/摘要综合评分匹配 → 确认后计入已回款。银行匹配不只看金额。
      </p>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {msg && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{msg}</div>}

      <div className="mt-6 grid max-w-3xl grid-cols-3 gap-3">
        <MetricCard label="应回款" value={`¥${overview?.receivable ?? "0"}`} />
        <MetricCard label="已回款" value={`¥${overview?.received ?? "0"}`} />
        <MetricCard label="待回款" value={`¥${overview?.pending ?? "0"}`} hint="应回款 − 已回款" />
      </div>

      {byPlatform.length > 0 && (
        <div className="mt-4 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
          <div className="text-xs font-medium text-gray-500">分平台</div>
          <div className="mt-2 space-y-1.5 text-sm">
            {byPlatform.map(([platform, v]) => (
              <div key={platform} className="flex items-center justify-between text-gray-700">
                <span>{platform}</span>
                <span className="tabular-nums">
                  <span className="text-emerald-600">已回 {v.settled}</span>
                  <span className="mx-2 text-gray-300">/</span>
                  <span className="text-gray-500">应收 {v.expected}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 对方户名映射规则 */}
      <div className="mt-8 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">对方户名 → 平台映射规则</div>
        <p className="mt-1 text-xs text-gray-400">
          规则变更有审计日志，不静默重写历史匹配。默认规则来自规格 8.2。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={rulePattern}
            onChange={(e) => setRulePattern(e.target.value)}
            placeholder="对方户名包含关键词，如：上海寻梦信息技术有限公司"
            className="w-80 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <input
            value={rulePlatform}
            onChange={(e) => setRulePlatform(e.target.value)}
            placeholder="平台，如：拼多多"
            className="w-36 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <button onClick={addRule} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
            添加规则
          </button>
        </div>
        {rules.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {rules.map((r) => (
              <span key={r.id} className="inline-flex items-center gap-1.5 rounded-full bg-gray-50 px-3 py-1 text-xs text-gray-600 ring-1 ring-gray-200">
                {r.matchPattern} → {r.platform}
                <button onClick={() => delRule(r.id)} className="text-gray-400 hover:text-red-500">×</button>
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 应收登记 */}
      <div className="mt-4 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">登记平台应收（应结算）</div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={newPlatform}
            onChange={(e) => setNewPlatform(e.target.value)}
            placeholder="平台，如：抖音"
            className="w-32 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <input
            value={newPeriod}
            onChange={(e) => setNewPeriod(e.target.value)}
            type="month"
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <input
            value={newExpected}
            onChange={(e) => setNewExpected(e.target.value)}
            placeholder="应收金额，如：50000"
            className="w-36 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <button onClick={addSettlement} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
            登记应收
          </button>
        </div>
        {settlements.length > 0 && (
          <table className="mt-4 w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-xs text-gray-400">
                <th className="py-2 pr-4 font-medium">平台</th>
                <th className="py-2 pr-4 font-medium">账期</th>
                <th className="py-2 pr-4 font-medium">应收</th>
                <th className="py-2 pr-4 font-medium">已回</th>
                <th className="py-2 font-medium">状态</th>
              </tr>
            </thead>
            <tbody>
              {settlements.map((s) => (
                <tr key={s.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 text-gray-700">{s.platform}</td>
                  <td className="py-2 pr-4 text-gray-500">{s.period}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-700">{s.expectedAmount}</td>
                  <td className="py-2 pr-4 tabular-nums text-emerald-600">{s.settledAmount}</td>
                  <td className="py-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${
                      s.status === "settled" ? "bg-emerald-50 text-emerald-700 ring-emerald-200"
                      : s.status === "partial" ? "bg-amber-50 text-amber-700 ring-amber-200"
                      : "bg-gray-100 text-gray-500 ring-gray-200"
                    }`}>
                      {SETTLEMENT_STATUS[s.status] ?? s.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 银行流水 XLSX 导入（规格 8.1：文件导入模式） */}
      <div className="mt-4 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">导入浙江农信交易明细（XLSX）</div>
        <p className="mt-1 text-xs text-gray-400">
          通用列名检测（交易日期/摘要/对方户名/收入/支出/余额/流水号），指纹幂等：重复导入只跳过不重复。
          文件只做解析，不修改原始资料。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input type="file" accept=".xlsx" className="text-sm" id="bank-xlsx" />
          <input
            value={importPeriod}
            onChange={(e) => setImportPeriod(e.target.value)}
            type="month"
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <button
            onClick={importXlsx}
            disabled={importing}
            className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            {importing ? "导入中…" : "导入流水"}
          </button>
        </div>
      </div>

      {/* 匹配建议 */}
      <div className="mt-8 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">匹配建议</div>
        <p className="mt-1 text-xs text-gray-400">
          评分 = 金额 40 + 平台规则 30 + 账期 15 + 户名/摘要提示 10。建议不落库，确认才生效。
        </p>
        {suggestions.length === 0 ? (
          <p className="mt-4 text-sm text-gray-400">暂无待匹配流水。登记银行流水后会自动生成建议。</p>
        ) : (
          <div className="mt-3 space-y-2">
            {suggestions.map((s) => (
              <div key={`${s.txnId}-${s.settlementId}`} className="rounded-lg border border-gray-100 bg-gray-50/60 p-3">
                <div className="flex items-center justify-between">
                  <div className="text-sm text-gray-700">
                    <span className="tabular-nums font-medium">¥{s.amount}</span>
                    <span className="mx-2 text-gray-300">·</span>
                    {s.counterparty || "（无对方户名）"}
                    <span className="mx-2 text-gray-300">·</span>
                    {s.txnDate}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${CONFIDENCE_STYLE[s.confidence]}`}>
                      {CONFIDENCE_LABEL[s.confidence]} · {s.score}分
                    </span>
                  </div>
                </div>
                <div className="mt-1.5 flex items-center justify-between">
                  <div className="text-xs text-gray-500">
                    建议匹配：{s.platform} {s.period} 应收 ¥{s.expectedAmount}
                    <span className="ml-2 text-gray-400">（{s.reasons.join("；")}）</span>
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => confirm(s.txnId, s.settlementId)}
                      className="rounded-lg bg-emerald-600 px-3 py-1 text-xs text-white hover:bg-emerald-700"
                    >
                      确认匹配
                    </button>
                    <button
                      onClick={() => reject(s.txnId, s.settlementId)}
                      className="rounded-lg bg-white px-3 py-1 text-xs text-gray-500 ring-1 ring-gray-200 hover:text-red-600"
                    >
                      拒绝
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
