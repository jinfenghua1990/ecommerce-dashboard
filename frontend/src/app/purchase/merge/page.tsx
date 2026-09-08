"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type PurchaseOrder = {
  id: number;
  externalOrderId: string;
  supplierName: string;
  platform: string;
  paidAmount: string | null;
  orderAmount: string | null;
  orderedAt: string | null;
  purchaseStatus: string;
};

type SuggestionOrder = {
  poId: number;
  platform: string;
  orderNo: string;
  supplier: string;
  amount: string;
  orderedAt: string | null;
};

type Suggestion = {
  jackyunPoId: number;
  purchNo: string;
  supplier: string;
  jackyunAmount: string;
  ordersAmount: string;
  difference: string;
  confidence: number;
  orders: SuggestionOrder[];
};

type MergeSummary = {
  jackyunPoId: number;
  purchNo: string;
  supplier: string;
  amount: string;
  allocated: string;
  difference: string;
  balanced: boolean;
  orderCount: number;
  orders: Array<{
    poId: number;
    platform: string;
    orderNo: string;
    allocAmount: string | null;
  }>;
};

function money(value: string | number | null | undefined) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "0.00";
}

function platformName(value: string) {
  if (value === "1688") return "1688";
  if (value === "pdd") return "拼多多";
  if (value === "taobao") return "淘宝";
  return "其他";
}

async function readJson<T>(response: Response): Promise<T> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? String((payload as { detail?: unknown }).detail ?? "")
      : "";
    throw new Error(detail || `请求失败（${response.status}）`);
  }
  return payload as T;
}

export default function PurchaseMergePage() {
  const [orders, setOrders] = useState<PurchaseOrder[]>([]);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selected, setSelected] = useState<Record<number, string>>({});
  const [purchNo, setPurchNo] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [lastSummary, setLastSummary] = useState<MergeSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ordersRes, suggestionsRes] = await Promise.all([
        authenticatedFetch("/api/v1/purchase/orders?limit=500"),
        authenticatedFetch("/api/v1/purchase/merge-suggestions"),
      ]);
      const orderRows = await readJson<PurchaseOrder[]>(ordersRes);
      const suggestionRows = await readJson<{ items: Suggestion[] }>(suggestionsRes);
      setOrders(orderRows.filter((row) => row.purchaseStatus === "confirmed"));
      setSuggestions(suggestionRows.items ?? []);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "采购合并数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visibleOrders = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    const rows = keyword
      ? orders.filter((row) =>
          row.externalOrderId.toLowerCase().includes(keyword)
          || row.supplierName.toLowerCase().includes(keyword)
          || platformName(row.platform).toLowerCase().includes(keyword))
      : orders;
    return [...rows].sort((a, b) => {
      const at = a.orderedAt ? new Date(a.orderedAt).getTime() : 0;
      const bt = b.orderedAt ? new Date(b.orderedAt).getTime() : 0;
      return bt - at || b.id - a.id;
    });
  }, [orders, query]);

  const selectedRows = useMemo(
    () => orders.filter((row) => Object.prototype.hasOwnProperty.call(selected, row.id)),
    [orders, selected],
  );
  const selectedTotal = useMemo(
    () => selectedRows.reduce((sum, row) => sum + Number(selected[row.id] || 0), 0),
    [selectedRows, selected],
  );

  function toggle(row: PurchaseOrder) {
    setSelected((current) => {
      if (Object.prototype.hasOwnProperty.call(current, row.id)) {
        const next = { ...current };
        delete next[row.id];
        return next;
      }
      return {
        ...current,
        [row.id]: String(row.paidAmount ?? row.orderAmount ?? ""),
      };
    });
  }

  async function submitMerge(nextPurchNo: string, rows: Array<{ poId: number; amount: string }>) {
    if (!nextPurchNo.trim()) {
      setMessage("请填写吉客云采购单号");
      return;
    }
    if (rows.length < 2) {
      setMessage("至少选择 2 张线上采购订单");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const response = await authenticatedFetch("/api/v1/purchase/merge-groups", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purch_no: nextPurchNo.trim(),
          allocations: rows.map((row) => ({ po_id: row.poId, alloc_amount: row.amount })),
          note: "采购工作台合并",
        }),
      });
      const result = await readJson<MergeSummary>(response);
      setLastSummary(result);
      setMessage(`已合并 ${result.orderCount} 张来源订单 → ${result.purchNo}，金额差额 ¥${money(result.difference)}`);
      setSelected({});
      setPurchNo("");
      await load();
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "采购合并失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-w-[980px] space-y-3">
      <div className="sticky top-0 z-20 -mx-1 flex items-center justify-between gap-3 border-b border-slate-200 bg-white/95 px-1 py-2 backdrop-blur">
        <div>
          <div className="text-base font-semibold text-slate-900">采购单合并</div>
          <div className="text-[12px] text-slate-500">多张 1688 / 淘宝 / 拼多多 / 其他采购订单，共同对应 1 张吉客云采购单；原始订单始终保留。</div>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-[12px]">
          <span className="rounded bg-slate-100 px-2 py-1 text-slate-600">待确认 {orders.length}</span>
          <span className="rounded bg-amber-50 px-2 py-1 text-amber-700">推荐合并 {suggestions.length}</span>
          <button onClick={() => void load()} disabled={loading || busy} className="rounded border border-slate-300 px-2.5 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-40">刷新</button>
        </div>
      </div>

      {message && (
        <div className={`rounded-md border px-3 py-2 text-[12px] ${message.startsWith("已合并") ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
          {message}
        </div>
      )}

      {lastSummary && (
        <div className="flex items-center gap-4 rounded-md border border-emerald-200 bg-emerald-50/60 px-3 py-2 text-[12px] text-slate-700">
          <span className="font-medium text-emerald-700">最近合并</span>
          <span className="font-mono">{lastSummary.purchNo}</span>
          <span>{lastSummary.orderCount} 单</span>
          <span>吉客云 ¥{money(lastSummary.amount)}</span>
          <span>已分摊 ¥{money(lastSummary.allocated)}</span>
          <span className={lastSummary.balanced ? "text-emerald-700" : "text-red-600"}>{lastSummary.balanced ? "金额已闭环" : `差额 ¥${money(lastSummary.difference)}`}</span>
        </div>
      )}

      <section className="rounded-lg border border-amber-200 bg-amber-50/20 p-3">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-[13px] font-semibold text-slate-800">系统推荐</div>
          <div className="text-[11px] text-slate-500">同供应商 + 45 天内 + 2~4 张来源单金额合计命中吉客云采购单；只推荐，不会自动合并。</div>
        </div>
        {suggestions.length === 0 ? (
          <div className="rounded border border-dashed border-slate-200 bg-white px-3 py-3 text-[12px] text-slate-400">暂无高置信合并建议。</div>
        ) : (
          <div className="space-y-1.5">
            {suggestions.map((item) => (
              <div key={item.jackyunPoId} className="flex items-center gap-3 rounded border border-slate-200 bg-white px-2.5 py-2 text-[12px]">
                <div className="w-36 shrink-0">
                  <div className="font-mono font-medium text-indigo-700">{item.purchNo}</div>
                  <div className="text-[11px] text-slate-400">吉客云 ¥{money(item.jackyunAmount)}</div>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium text-slate-700">{item.supplier}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {item.orders.map((order) => (
                      <span key={order.poId} className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
                        {platformName(order.platform)} · {order.orderNo} · ¥{money(order.amount)}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="w-24 shrink-0 text-right">
                  <div className="font-medium text-emerald-700">{Math.round(item.confidence * 100)}%</div>
                  <div className="text-[11px] text-slate-400">差额 ¥{money(item.difference)}</div>
                </div>
                <button
                  disabled={busy}
                  onClick={() => void submitMerge(item.purchNo, item.orders.map((order) => ({ poId: order.poId, amount: order.amount })))}
                  className="shrink-0 rounded bg-indigo-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                >
                  采用建议
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-3">
        <div className="mb-2 flex items-center gap-2">
          <div className="text-[13px] font-semibold text-slate-800">手工合并</div>
          <input
            value={purchNo}
            onChange={(event) => setPurchNo(event.target.value)}
            placeholder="吉客云采购单号"
            className="h-8 w-64 rounded border border-slate-300 px-2 text-[12px] outline-none focus:border-indigo-400"
          />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索订单号 / 供应商 / 渠道"
            className="h-8 w-64 rounded border border-slate-300 px-2 text-[12px] outline-none focus:border-indigo-400"
          />
          <div className="ml-auto flex items-center gap-3 text-[12px] text-slate-500">
            <span>已选 {selectedRows.length} 单</span>
            <span>分摊合计 <b className="text-slate-800">¥{money(selectedTotal)}</b></span>
            <button
              disabled={busy || selectedRows.length < 2 || !purchNo.trim()}
              onClick={() => void submitMerge(purchNo, selectedRows.map((row) => ({ poId: row.id, amount: selected[row.id] })))}
              className="rounded bg-slate-900 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-slate-800 disabled:opacity-30"
            >
              {busy ? "合并中…" : "确认合并"}
            </button>
          </div>
        </div>

        <div className="overflow-hidden rounded border border-slate-200">
          <div className="grid grid-cols-[38px_90px_180px_minmax(180px,1fr)_120px_130px] items-center bg-slate-50 px-2 py-1.5 text-[11px] font-medium text-slate-500">
            <span></span><span>渠道</span><span>线上订单</span><span>供应商</span><span className="text-right">实付</span><span className="text-right">合并分摊</span>
          </div>
          {visibleOrders.map((row) => {
            const checked = Object.prototype.hasOwnProperty.call(selected, row.id);
            return (
              <div key={row.id} className="grid grid-cols-[38px_90px_180px_minmax(180px,1fr)_120px_130px] items-center border-t border-slate-100 px-2 py-1.5 text-[12px] hover:bg-slate-50/70">
                <input type="checkbox" checked={checked} onChange={() => toggle(row)} className="h-4 w-4" />
                <span className="text-slate-600">{platformName(row.platform)}</span>
                <span className="truncate font-mono text-slate-700" title={row.externalOrderId}>{row.externalOrderId}</span>
                <span className="truncate text-slate-600" title={row.supplierName}>{row.supplierName || "—"}</span>
                <span className="text-right tabular-nums text-slate-700">¥{money(row.paidAmount ?? row.orderAmount)}</span>
                {checked ? (
                  <input
                    value={selected[row.id] ?? ""}
                    onChange={(event) => setSelected((current) => ({ ...current, [row.id]: event.target.value }))}
                    className="ml-auto h-7 w-28 rounded border border-indigo-200 px-2 text-right text-[12px] tabular-nums outline-none focus:border-indigo-400"
                  />
                ) : (
                  <span className="text-right text-slate-300">—</span>
                )}
              </div>
            );
          })}
          {!loading && visibleOrders.length === 0 && (
            <div className="px-3 py-8 text-center text-[12px] text-slate-400">没有符合条件的待合并采购订单。</div>
          )}
          {loading && <div className="px-3 py-8 text-center text-[12px] text-slate-400">正在加载采购订单…</div>}
        </div>
      </section>

      <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] leading-5 text-slate-500">
        合并只建立采购关系，不会删除或改写 1688 / 淘宝 / 拼多多原始订单。合并后系统继续分别保留来源订单金额、SKU、物流和发票追溯；进入“完成”前会强制核验入库、发票、付款与认证覆盖。
      </div>
    </div>
  );
}
