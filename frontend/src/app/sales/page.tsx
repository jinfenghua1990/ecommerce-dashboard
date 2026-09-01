"use client";

import { useCallback, useEffect, useState } from "react";
import MetricCard from "@/components/metric-card";
import { dashboardApi, PlatformRow, SalesOrderRow, SkuRow, TrendPoint } from "@/lib/api";

function fmtMoney(v: string | null): string {
  if (v === null || v === undefined) return "—";
  return `¥${Number(v).toLocaleString("zh-CN", { minimumFractionDigits: 2 })}`;
}

export default function SalesPage() {
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [platforms, setPlatforms] = useState<PlatformRow[]>([]);
  const [skus, setSkus] = useState<SkuRow[]>([]);
  const [orders, setOrders] = useState<SalesOrderRow[]>([]);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    Promise.all([
      dashboardApi.salesTrend(30),
      dashboardApi.platformRanking(),
      dashboardApi.skuRanking(10),
      dashboardApi.orders(),
    ])
      .then(([t, p, s, o]) => {
        setTrend(t);
        setPlatforms(p);
        setSkus(s);
        setOrders(o);
      })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(load, [load]);

  const totalSales = trend.reduce((acc, t) => acc + (t.salesAmount ? Number(t.salesAmount) : 0), 0);
  const totalOrders = trend.reduce((acc, t) => acc + t.orders, 0);

  return (
    <div>
      <h1 className="text-xl font-semibold">销售</h1>
      <p className="mt-1 text-sm text-gray-400">
        数据来自吉客云已订阅接口的本地同步副本（oms.trade.fullinfoget 等）。页面只查本地库，不触发吉客云查询。
      </p>

      {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}</div>}

      <div className="mt-5 grid grid-cols-4 gap-4">
        <MetricCard label="近30天销售额" value={fmtMoney(totalSales ? String(totalSales) : null)} />
        <MetricCard label="近30天订单数" value={String(totalOrders)} />
        <MetricCard label="平台数" value={String(platforms.length)} hint={platforms.length ? undefined : "等订单同步落地"} />
        <MetricCard label="本地订单记录" value={String(orders.length)} hint={orders.length ? undefined : "吉客云开通后自动同步"} />
      </div>

      <div className="mt-6 grid grid-cols-2 gap-4">
        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">近30天销售趋势</h2>
          {trend.length === 0 ? (
            <div className="mt-4 py-8 text-center text-sm text-gray-400">暂无同步数据，吉客云开放平台开通后自动出数</div>
          ) : (
            <div className="mt-3 space-y-1.5">
              {trend.slice(-14).map((t) => (
                <div key={t.date} className="flex items-center gap-2 text-xs">
                  <span className="w-20 shrink-0 text-gray-500">{t.date}</span>
                  <div className="h-4 flex-1 overflow-hidden rounded bg-gray-100">
                    <div
                      className="h-full rounded bg-indigo-500"
                      style={{ width: `${Math.min(100, (Number(t.salesAmount) || 0) / (totalSales || 1) * 100)}%` }}
                    />
                  </div>
                  <span className="w-24 shrink-0 text-right text-gray-600">{fmtMoney(t.salesAmount)}</span>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="rounded-xl border border-gray-200 bg-white p-4">
          <h2 className="text-sm font-medium text-gray-700">平台排行</h2>
          {platforms.length === 0 ? (
            <div className="mt-4 py-8 text-center text-sm text-gray-400">暂无平台数据</div>
          ) : (
            <table className="mt-2 w-full text-sm">
              <tbody className="divide-y divide-gray-100">
                {platforms.map((p) => (
                  <tr key={p.platform}>
                    <td className="py-2">{p.platform}</td>
                    <td className="py-2 text-right text-gray-500">{p.orders} 单</td>
                    <td className="py-2 text-right font-medium">{fmtMoney(p.salesAmount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-medium text-gray-700">SKU 销售排行</h2>
        {skus.length === 0 ? (
          <div className="mt-2 py-6 text-center text-sm text-gray-400">暂无 SKU 数据</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs text-gray-500">
              <tr>
                <th className="py-2 font-medium">SKU</th>
                <th className="py-2 font-medium">商品</th>
                <th className="py-2 text-right font-medium">订单数</th>
                <th className="py-2 text-right font-medium">销售额</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {skus.map((s) => (
                <tr key={s.skuCode}>
                  <td className="py-2 font-mono text-xs">{s.skuCode}</td>
                  <td className="py-2">{s.goodsName}</td>
                  <td className="py-2 text-right text-gray-500">{s.orders}</td>
                  <td className="py-2 text-right font-medium">{fmtMoney(s.salesAmount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
        <h2 className="text-sm font-medium text-gray-700">订单（本地副本）</h2>
        {orders.length === 0 ? (
          <div className="mt-2 py-6 text-center text-sm text-gray-400">暂无订单数据</div>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-xs text-gray-500">
              <tr>
                <th className="py-2 font-medium">订单号</th>
                <th className="py-2 font-medium">平台/店铺</th>
                <th className="py-2 font-medium">状态</th>
                <th className="py-2 text-right font-medium">实付</th>
                <th className="py-2 font-medium">下单时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {orders.map((o) => (
                <tr key={o.id}>
                  <td className="py-2 font-mono text-xs">{o.orderNo}</td>
                  <td className="py-2">{o.platform || "—"}{o.storeName ? ` / ${o.storeName}` : ""}</td>
                  <td className="py-2 text-xs text-gray-500">{o.orderStatus || "—"}</td>
                  <td className="py-2 text-right font-medium">{fmtMoney(o.paidAmount)}</td>
                  <td className="py-2 text-xs text-gray-400">{o.orderedAt ? new Date(o.orderedAt).toLocaleString("zh-CN") : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
