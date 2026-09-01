"use client";

import { useCallback, useEffect, useState } from "react";
import MetricCard from "@/components/metric-card";
import { dashboardApi, InventorySummary } from "@/lib/api";

function fmtMoney(v: string | null): string {
  if (v === null || v === undefined) return "—";
  return `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
}

export default function ProductsPage() {
  const [inv, setInv] = useState<InventorySummary | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    dashboardApi
      .inventory()
      .then(setInv)
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  return (
    <div>
      <h1 className="text-xl font-semibold">商品与库存</h1>
      <p className="mt-1 text-sm text-gray-400">
        商品/SKU 主档来自吉客云 erp.storage.goodslist；库存来自 erp.stockquantity.get 本地快照。
      </p>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}

      <div className="mt-5 grid grid-cols-4 gap-4">
        <MetricCard label="SKU 数" value={String(inv?.skuCount ?? "—")} hint={inv?.skuCount ? undefined : "吉客云商品同步落地后显示"} />
        <MetricCard label="最新库存快照" value={inv?.snapshotAt ? new Date(inv.snapshotAt).toLocaleString("zh-CN") : "—"} hint={inv?.note ?? undefined} />
        <MetricCard label="库存总量" value={inv?.totalQuantity ? fmtMoney(inv.totalQuantity) : "—"} />
        <MetricCard label="仓库数" value={String(inv?.byWarehouse?.length ?? 0)} />
      </div>

      <section className="mt-6 rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-medium text-gray-700">各仓库库存（最新快照）</h2>
        {!inv?.byWarehouse?.length ? (
          <div className="mt-3 py-8 text-center text-sm text-gray-400">
            {inv?.note ?? "暂无库存快照"}
          </div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs text-gray-500">
              <tr>
                <th className="py-2 font-medium">仓库</th>
                <th className="py-2 text-right font-medium">SKU 数</th>
                <th className="py-2 text-right font-medium">数量</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {inv.byWarehouse.map((w) => (
                <tr key={String(w.warehouseId)}>
                  <td className="py-2">{w.warehouseId === null ? "默认" : `#${w.warehouseId}`}</td>
                  <td className="py-2 text-right text-gray-500">{w.skus}</td>
                  <td className="py-2 text-right font-medium">{fmtMoney(w.quantity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <div className="mt-4 rounded-xl border border-gray-100 bg-gray-50 p-4 text-xs leading-5 text-gray-500">
        商品主档规则（规格 1.1）：本平台不重新维护正式 SKU，采购录入时从吉客云商品库选择；关联主键使用吉客云内部 ID/SKU 编码，不靠商品名称。
      </div>
    </div>
  );
}
