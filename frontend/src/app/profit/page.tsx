"use client";

import { useCallback, useEffect, useState } from "react";
import MetricCard from "@/components/metric-card";
import { closingApi, ClosingRow } from "@/lib/api";
import { CostRow, profitApi, ProfitCompute, ProfitOverview } from "@/lib/api";

const SOURCE_STYLE: Record<string, string> = {
  实际采购结算成本: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  采购订单成本: "bg-sky-50 text-sky-700 ring-sky-200",
  SKU默认成本: "bg-amber-50 text-amber-700 ring-amber-200",
  暂估成本: "bg-gray-100 text-gray-500 ring-gray-200",
};

export default function ProfitPage() {
  const [overview, setOverview] = useState<ProfitOverview | null>(null);
  const [costs, setCosts] = useState<CostRow[]>([]);
  const [compute, setCompute] = useState<ProfitCompute | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const [skuId, setSkuId] = useState("");
  const [period, setPeriod] = useState("2026-08");
  const [source, setSource] = useState("default");
  const [amount, setAmount] = useState("");
  const [versions, setVersions] = useState<ClosingRow[]>([]);
  const [closeMsg, setCloseMsg] = useState("");
  const [closeLoading, setCloseLoading] = useState(false);

  const load = useCallback(() => {
    profitApi.overview().then(setOverview).catch(() => {});
    profitApi.costs().then(setCosts).catch(() => {});
    const [y, m] = period.split("-").map(Number);
    if (y && m) profitApi.compute(y, m).then(setCompute).catch(() => {});
    const [vy, vm] = period.split("-").map(Number);
    if (vy && vm) closingApi.versions(vy, vm).then(setVersions).catch(() => {});
  }, [period]);

  useEffect(load, [load]);

  function flash(text: string) {
    setMsg(text);
    setTimeout(() => setMsg(""), 3500);
  }

  async function addCost() {
    const [y, m] = period.split("-").map(Number);
    if (!skuId.trim() || !amount) return;
    const body: Record<string, unknown> = { sku_id: Number(skuId), period_year: y, period_month: m, source };
    body[source === "actual" ? "actual_cost" : source === "purch" ? "purch_order_cost" : source === "estimated" ? "estimated_cost" : "default_cost"] = amount;
    await profitApi.upsertCost(body);
    setSkuId("");
    setAmount("");
    load();
    flash("成本已登记（版本号自动管理，变更写审计日志）");
  }

  async function closePeriod(mode: "snapshot" | "recalc") {
    const [y, m] = period.split("-").map(Number);
    if (!y || !m) return;
    setCloseLoading(true);
    try {
      const res = mode === "snapshot" ? await closingApi.snapshot(y, m) : await closingApi.recalc(y, m);
      setCloseMsg(mode === "recalc" ? `${res.note}：V${res.version}` : `已生成月结快照 V${res.version}`);
      load();
    } catch (e) {
      setCloseMsg(`月结失败：${String(e)}`);
    } finally {
      setCloseLoading(false);
      setTimeout(() => setCloseMsg(""), 4000);
    }
  }

  const coverage = overview?.coverage ?? {};
  const totalSnap = overview?.snapshotCount ?? 0;
  const missingCount = coverage.missing ?? 0;

  return (
    <div>
      <h1 className="text-xl font-semibold">利润</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        商品毛利 = 净销售收入 − 商品成本。成本优先级：实际采购结算 &gt; 采购订单 &gt; SKU 默认 &gt; 暂估。
        成本缺失时明确标识，不显示假装精确的最终利润。
      </p>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {msg && <div className="mt-4 rounded-lg bg-emerald-50 p-3 text-sm text-emerald-700">{msg}</div>}

      <div className="mt-6 grid max-w-3xl grid-cols-4 gap-3">
        <MetricCard label="SKU 数" value={String(overview?.skuCount ?? 0)} />
        <MetricCard label="成本快照" value={String(totalSnap)} />
        <MetricCard label="成本缺失" value={String(missingCount)} hint="无任何来源成本" />
        <MetricCard label="贡献利润" value={overview?.contributionProfitEnabled ? "已启用" : "未开放"} hint="费用数据可靠后开放" />
      </div>

      {/* 登记成本 */}
      <div className="mt-8 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">登记 SKU 成本</div>
        <p className="mt-1 text-xs text-gray-400">
          同一 SKU 同一账期只允许登记一种来源的成本；重复登记值变化时版本号 +1。
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            value={skuId}
            onChange={(e) => setSkuId(e.target.value)}
            placeholder="SKU ID（吉客云同步后）"
            className="w-44 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <input
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            type="month"
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          >
            <option value="actual">实际采购结算成本</option>
            <option value="purch">采购订单成本</option>
            <option value="default">SKU 默认成本</option>
            <option value="estimated">暂估成本</option>
          </select>
          <input
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="金额"
            className="w-28 rounded-lg border border-gray-300 px-3 py-1.5 text-sm"
          />
          <button onClick={addCost} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
            登记
          </button>
        </div>

        {costs.length > 0 && (
          <table className="mt-4 w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-xs text-gray-400">
                <th className="py-2 pr-4 font-medium">SKU</th>
                <th className="py-2 pr-4 font-medium">账期</th>
                <th className="py-2 pr-4 font-medium">实际</th>
                <th className="py-2 pr-4 font-medium">采购订单</th>
                <th className="py-2 pr-4 font-medium">默认</th>
                <th className="py-2 pr-4 font-medium">暂估</th>
                <th className="py-2 pr-4 font-medium">生效成本</th>
                <th className="py-2 font-medium">v</th>
              </tr>
            </thead>
            <tbody>
              {costs.map((c) => (
                <tr key={c.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 text-gray-700">{c.skuCode || `#${c.skuId}`}</td>
                  <td className="py-2 pr-4 text-gray-500">{c.period}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{c.values.actual ?? "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{c.values.purchOrder ?? "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{c.values.default ?? "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{c.values.estimated ?? "—"}</td>
                  <td className="py-2 pr-4">
                    {c.effectiveCost ? (
                      <span className={`inline-flex rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${SOURCE_STYLE[c.effectiveSource ?? ""] ?? "bg-gray-100 text-gray-500 ring-gray-200"}`}>
                        ¥{c.effectiveCost} · {c.effectiveSource}
                      </span>
                    ) : (
                      <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-600 ring-1 ring-inset ring-red-200">成本缺失</span>
                    )}
                  </td>
                  <td className="py-2 text-gray-400">v{c.version}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 毛利计算 */}
      <div className="mt-4 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">商品毛利（{compute?.period ?? period}）</div>
        {compute?.netSales === null ? (
          <p className="mt-3 text-sm text-gray-400">
            暂无销售数据（吉客云销售同步打通后自动计算）。成本登记后可先行维护成本快照。
          </p>
        ) : (
          <div className="mt-3 grid grid-cols-3 gap-3">
            <MetricCard label="净销售收入" value={`¥${compute?.netSales ?? "0"}`} />
            <MetricCard label="商品成本" value={`¥${compute?.goodsCost ?? "0"}`} />
            <MetricCard
              label="商品毛利"
              value={compute?.grossProfit !== null ? `¥${compute?.grossProfit ?? "0"}` : "—"}
              hint={compute?.costMissing ? `成本缺失 SKU：${compute?.costMissingSkus.length ?? 0} 个，毛利不完整` : undefined}
            />
          </div>
        )}
        <p className="mt-3 text-xs text-gray-400">{compute?.note ?? ""}</p>
      </div>

      {/* 月结快照（规格 1.6：V1 保留，重算产生 V2/V3） */}
      <div className="mt-4 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">月结快照 · {period}</div>
            <p className="mt-1 text-xs text-gray-400">
              已发送 V1 不被静默覆盖；修正/重算生成新版本（V2/V3…），版本与计算时间留痕。
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => closePeriod("snapshot")}
              disabled={closeLoading}
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {closeLoading ? "计算中…" : "生成快照"}
            </button>
            <button
              onClick={() => closePeriod("recalc")}
              disabled={closeLoading}
              className="rounded-lg bg-white px-3 py-1.5 text-sm text-indigo-600 ring-1 ring-indigo-200 hover:bg-indigo-50 disabled:opacity-50"
            >
              重新计算
            </button>
          </div>
        </div>
        {closeMsg && <div className="mt-3 text-sm text-emerald-700">{closeMsg}</div>}
        {versions.length > 0 ? (
          <table className="mt-4 w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-xs text-gray-400">
                <th className="py-2 pr-4 font-medium">版本</th>
                <th className="py-2 pr-4 font-medium">毛利</th>
                <th className="py-2 pr-4 font-medium">应回款</th>
                <th className="py-2 pr-4 font-medium">已回款</th>
                <th className="py-2 pr-4 font-medium">计算时间</th>
                <th className="py-2 font-medium">当前</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4 font-medium">V{v.version}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{v.grossProfit !== null ? `¥${v.grossProfit}` : "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{v.receivable !== null ? `¥${v.receivable}` : "—"}</td>
                  <td className="py-2 pr-4 tabular-nums text-gray-600">{v.received !== null ? `¥${v.received}` : "—"}</td>
                  <td className="py-2 pr-4 text-gray-400">{v.calculatedAt ? new Date(v.calculatedAt).toLocaleString("zh-CN") : "—"}</td>
                  <td className="py-2">{v.isCurrent ? <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs text-indigo-700">当前</span> : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="mt-3 text-xs text-gray-400">尚未生成月结快照</p>
        )}
      </div>
    </div>
  );
}
