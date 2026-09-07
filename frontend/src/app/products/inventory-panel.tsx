"use client";

import { useCallback, useEffect, useState } from "react";
import { consumablesApi, dashboardApi, type InventorySkuRow } from "@/lib/api";

const qty = (v: string | null | undefined) =>
  v == null || v === "" ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 4 });

/** 库存-正品：吉客云最新快照（本系统只读，不改动）。 */
export function GoodsInventoryPanel() {
  const [rows, setRows] = useState<InventorySkuRow[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    dashboardApi.inventorySkus(search)
      .then(setRows)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [search]);
  useEffect(() => { const timer = setTimeout(load, 200); return () => clearTimeout(timer); }, [load]);

  const totalQty = rows.reduce((sum, row) => sum + (row.hasSnapshot ? Number(row.quantity || 0) : 0), 0);
  const snapAt = rows.find((row) => row.snapshotAt)?.snapshotAt ?? null;

  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-medium text-gray-700">
            <span className="rounded bg-slate-100 px-1 py-px text-[10px] font-semibold text-slate-500">品</span>
            正品库存（吉客云）
          </h2>
          <p className="mt-1 text-xs text-gray-400">
            按最新吉客云库存快照统计；共 {rows.length} 个货品，合计 {qty(String(totalQty))} 件
            {snapAt ? <> · 快照 {new Date(snapAt).toLocaleString("zh-CN")}</> : " · 暂无快照"}
          </p>
        </div>
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索 SKU、货品或条码" className="w-72 rounded-lg border px-3 py-1.5 text-sm outline-none focus:border-blue-400" />
      </div>
      {err && <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="text-left text-xs text-gray-500">
            <tr className="border-b border-gray-100">
              <th className="py-2">SKU 编码</th>
              <th className="py-2">货品名称</th>
              <th className="py-2">条码</th>
              <th className="py-2">单位</th>
              <th className="py-2 text-right">当前库存</th>
              <th className="py-2 pl-3">仓库分布</th>
              <th className="py-2">档案状态</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row) => (
              <tr key={row.skuId} className={row.status !== "active" ? "text-gray-400" : ""}>
                <td className="py-2.5 font-mono text-xs font-medium text-gray-800">{row.skuCode}</td>
                <td className="max-w-[220px] py-2.5">
                  <div className="truncate">{row.goodsName || row.skuName || "—"}</div>
                  <div className="mt-0.5 text-[10px] text-gray-400">{row.skuName}</div>
                </td>
                <td className="py-2.5 font-mono text-[11px] text-gray-500">{row.barcode || "—"}</td>
                <td className="py-2.5 text-gray-500">{row.unit || "—"}</td>
                <td className="py-2.5 text-right">
                  {row.hasSnapshot ? (
                    <span className={`text-[13px] font-semibold tabular-nums ${Number(row.quantity) === 0 ? "text-amber-600" : "text-gray-800"}`}>{qty(row.quantity)}</span>
                  ) : (
                    <span className="text-xs text-gray-300">无快照</span>
                  )}
                </td>
                <td className="py-2.5 pl-3">
                  {row.warehouses.length ? (
                    <div className="flex flex-wrap gap-1">
                      {row.warehouses.map((w, i) => (
                        <span key={i} className="inline-flex items-center gap-1 rounded bg-gray-50 px-1.5 py-0.5 text-[10px] text-gray-500">
                          <span className="h-1.5 w-1.5 rounded-full bg-blue-300" />
                          {w.warehouseName || `仓#${w.warehouseId}`}:<span className="font-medium tabular-nums text-gray-700">{qty(w.quantity)}</span>
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="text-[10px] text-gray-300">—</span>
                  )}
                </td>
                <td className="py-2.5">
                  <span className={`rounded-full px-2 py-0.5 text-[10px] ${row.status === "active" ? "bg-emerald-50 text-emerald-700" : "bg-gray-100 text-gray-400"}`}>
                    {row.status === "active" ? "启用" : row.status || "停用"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <p className="p-6 text-center text-sm text-gray-400">正在加载库存…</p>}
        {!loading && !rows.length && <p className="p-8 text-center text-sm text-gray-400">没有匹配的货品</p>}
      </div>
    </section>
  );
}

/** 库存-耗材：本系统维护，自有仓 / 工厂 / 在途三口径 + 安全库存预警 + 批量盘点。 */
export function ConsumableInventoryPanel() {
  const [materials, setMaterials] = useState<Awaited<ReturnType<typeof consumablesApi.list>>>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [stockOpen, setStockOpen] = useState(false);
  const [draft, setDraft] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = useCallback(() => {
    setLoading(true);
    consumablesApi.list(search)
      .then(setMaterials)
      .catch((e) => setErr(String(e)))
      .finally(() => setLoading(false));
  }, [search]);
  useEffect(() => { const timer = setTimeout(load, 200); return () => clearTimeout(timer); }, [load]);

  const lowCount = materials.filter((m) => m.lowStock).length;
  const ownTotal = materials.reduce((s, m) => s + Number(m.stockQty || 0), 0);
  const factoryTotal = materials.reduce((s, m) => s + Number(m.factoryQty || 0), 0);
  const transitTotal = materials.reduce((s, m) => s + Number(m.transitQty || 0), 0);

  function openStocktake() {
    setDraft(Object.fromEntries(materials.map((m) => [m.id, String(Number(m.stockQty || 0))])));
    setMsg("");
    setErr("");
    setStockOpen(true);
  }

  /** 实盘数与账面数的差额；实盘留空 = 与账面一致（跳过）。 */
  const stockRows = materials
    .map((m) => {
      const current = Number(m.stockQty || 0);
      const raw = draft[m.id];
      const counted = raw === undefined || raw === "" || Number.isNaN(Number(raw)) ? current : Number(raw);
      return { m, counted, delta: Number((counted - current).toFixed(4)) };
    });
  const changedRows = stockRows.filter((r) => r.delta !== 0);

  async function submitStocktake() {
    if (busy) return;
    if (!changedRows.length) { setMsg("账实相符，无需调整"); return; }
    setBusy(true); setErr("");
    try {
      for (const { m, counted, delta } of changedRows) {
        await consumablesApi.addTransaction(m.id, {
          transaction_type: "stocktake",
          quantity: String(delta),
          note: `盘点：实盘 ${counted}`,
          location: "own",
        });
      }
      setStockOpen(false);
      setMsg(`盘点完成：已按实盘校正 ${changedRows.length} 项，流水已入台账`);
      load();
    } catch (caught) {
      setErr(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-medium text-gray-700">
            <span className="rounded bg-amber-100 px-1 py-px text-[10px] font-semibold text-amber-700">耗</span>
            耗材库存（本系统）
          </h2>
          <p className="mt-1 text-xs text-gray-400">
            共 {materials.length} 种耗材 · 自有仓 {qty(String(ownTotal))} · 工厂 {qty(String(factoryTotal))} · 在途 {qty(String(transitTotal))}
            {lowCount > 0 && <span className="ml-2 font-medium text-amber-600">⚠ {lowCount} 项库存预警</span>}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {msg && !stockOpen && <span className="text-xs text-emerald-600">{msg}</span>}
          <button onClick={openStocktake} disabled={!materials.length} className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-40">盘点库存</button>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索编码、名称或条码" className="w-72 rounded-lg border px-3 py-1.5 text-sm outline-none focus:border-amber-400" />
        </div>
      </div>
      {err && <div className="mt-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[860px] text-sm">
          <thead className="text-left text-xs text-gray-500">
            <tr className="border-b border-gray-100">
              <th className="py-2">耗材</th>
              <th className="py-2">编码</th>
              <th className="py-2">关联正品</th>
              <th className="py-2 text-right">自有仓</th>
              <th className="py-2 text-right">工厂</th>
              <th className="py-2 text-right">在途</th>
              <th className="py-2 text-right">可用</th>
              <th className="py-2 text-right">安全库存</th>
              <th className="py-2">状态</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {materials.map((m) => (
              <tr key={m.id} className={m.lowStock ? "bg-amber-50/60" : ""}>
                <td className="max-w-[200px] py-2.5">
                  <div className="truncate text-gray-800">{m.name}</div>
                  <div className="mt-0.5 text-[10px] text-gray-400">{m.category || "未分类"} · {m.unit}</div>
                </td>
                <td className="py-2.5 font-mono text-xs text-gray-600">{m.code}</td>
                <td className="max-w-[160px] py-2.5">
                  {m.linkedSkus?.length
                    ? <span className="text-[11px] text-gray-500" title={m.linkedSkus.map((s) => s.skuCode).join(", ")}>{m.linkedSkus.slice(0, 2).map((s) => s.skuCode).join(", ")}{m.linkedSkus.length > 2 ? ` +${m.linkedSkus.length - 2}` : ""}</span>
                    : <span className="text-[11px] text-gray-300">未关联</span>}
                </td>
                <td className="py-2.5 text-right"><span className={`tabular-nums ${Number(m.stockQty) < 0 ? "font-semibold text-red-600" : "text-gray-800"}`}>{qty(m.stockQty)}</span></td>
                <td className="py-2.5 text-right tabular-nums text-gray-700">{qty(m.factoryQty)}</td>
                <td className="py-2.5 text-right tabular-nums text-gray-500">{qty(m.transitQty)}</td>
                <td className="py-2.5 text-right tabular-nums font-medium text-gray-800">{qty(m.availableQty)}</td>
                <td className="py-2.5 text-right tabular-nums text-gray-500">{qty(m.minStockQty)}</td>
                <td className="py-2.5">
                  {m.lowStock
                    ? <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">库存预警</span>
                    : Number(m.stockQty) < 0
                      ? <span className="rounded-full bg-red-50 px-2 py-0.5 text-[10px] font-medium text-red-600">负库存</span>
                      : <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700">正常</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {loading && <p className="p-6 text-center text-sm text-gray-400">正在加载耗材库存…</p>}
        {!loading && !materials.length && <p className="p-8 text-center text-sm text-gray-400">暂无耗材档案</p>}
      </div>

      {stockOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-5" role="dialog" aria-modal="true" aria-label="耗材库存盘点" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) setStockOpen(false); }}>
          <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <h3 className="text-base font-semibold text-slate-800">耗材库存盘点</h3>
                <p className="mt-1 text-xs text-slate-400">按实际清点数填写「实盘数量」，提交后系统自动按差额生成盘点流水；留空或与账面一致的项目跳过。</p>
              </div>
              <button type="button" onClick={() => setStockOpen(false)} aria-label="关闭盘点" className="text-xl leading-none text-slate-300 hover:text-slate-500">×</button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-3">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500">
                  <tr className="border-b border-slate-100">
                    <th className="py-2">耗材</th>
                    <th className="py-2 text-right">账面（自有仓）</th>
                    <th className="py-2 text-right">实盘数量</th>
                    <th className="py-2 text-right">差额</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-50">
                  {stockRows.map(({ m, counted, delta }) => (
                    <tr key={m.id}>
                      <td className="py-2">
                        <div className="text-slate-700">{m.name}</div>
                        <div className="mt-0.5 font-mono text-[10px] text-slate-400">{m.code}</div>
                      </td>
                      <td className="py-2 text-right tabular-nums text-slate-500">{qty(m.stockQty)}</td>
                      <td className="py-2 text-right">
                        <input
                          aria-label={`${m.name} 实盘数量`}
                          type="number"
                          step="0.0001"
                          value={draft[m.id] ?? ""}
                          onChange={(e) => setDraft({ ...draft, [m.id]: e.target.value })}
                          className="w-28 rounded border border-slate-200 px-2 py-1 text-right tabular-nums text-slate-700 outline-none focus:border-amber-400"
                        />
                        <span className="ml-1 text-[10px] text-slate-400">{m.unit}</span>
                      </td>
                      <td className={`py-2 text-right tabular-nums ${delta === 0 ? "text-slate-300" : delta > 0 ? "text-emerald-600" : "text-red-500"}`}>
                        {delta === 0 ? "—" : `${delta > 0 ? "+" : ""}${delta}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3">
              <span className="text-xs text-slate-500">
                {changedRows.length > 0 ? <>将校正 <strong className="text-slate-700">{changedRows.length}</strong> 项</> : "账实相符，无需调整"}
              </span>
              <div className="flex items-center gap-2">
                <button disabled={busy} onClick={() => setStockOpen(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button>
                <button disabled={busy} onClick={() => void submitStocktake()} className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50">{busy ? "提交中…" : "提交盘点"}</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
