"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Alibaba1688Panel } from "./panels/alibaba1688";
import { OtherChannelOrdersPanel } from "./panels/external-orders";
import { JackyunPanel } from "./panels/jackyun";
import { TaxInvoicesPanel } from "./panels/tax-invoices";

const TABS = [
  { key: "alibaba1688", label: "1688 订单", desc: "1688 卖家中心导出订单" },
  { key: "external_orders", label: "其他渠道采购订单", desc: "拼多多、淘宝、线下等订单号主档" },
  { key: "jackyun", label: "吉客云业务单据", desc: "采购单、入库单、结算单等客户端导出文件" },
  { key: "tax", label: "税务发票清单", desc: "税务系统官方发票清单" },
] as const;

type TabKey = (typeof TABS)[number]["key"];
const TAB_KEYS = TABS.map((t) => t.key) as readonly string[];

export default function DataCenterImportPage() {
  const searchParams = useSearchParams();
  const [tab, setTab] = useState<TabKey>("alibaba1688");
  const returnOrder = searchParams.get("order");

  useEffect(() => {
    const param = searchParams.get("tab");
    if (param && TAB_KEYS.includes(param)) {
      setTab(param as TabKey);
    }
  }, [searchParams]);

  return (
    <div>
      <h1 className="text-xl font-semibold">数据接入</h1>
      <p className="mt-1 max-w-4xl text-sm leading-6 text-gray-500">
        按数据源集中管理：每个页签内含该数据源的在线同步与文件导入，两种方式不会混淆或重复认定。
      </p>
      {returnOrder && <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-indigo-100 bg-indigo-50/60 px-4 py-3 text-sm text-indigo-800"><span>正在为采购订单 <b className="font-mono">{returnOrder}</b> 补充原始资料。确认导入后，回到订单完成关联。</span><Link href={`/purchase/workbench?view=orders&order=${encodeURIComponent(returnOrder)}`} className="rounded-md bg-white px-3 py-1.5 text-xs font-medium text-indigo-600 shadow-sm">返回当前订单</Link></div>}

      <div className="mt-5 flex flex-wrap gap-2 border-b border-gray-200">
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => {
                setTab(t.key);
                const url = new URL(window.location.href);
                url.searchParams.set("tab", t.key);
                window.history.replaceState(null, "", url);
              }}
              className={`-mb-px rounded-t-lg border-b-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                active
                  ? "border-indigo-600 text-indigo-700"
                  : "border-transparent text-gray-500 hover:text-gray-800"
              }`}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="mt-6">
        {tab === "alibaba1688" && <Alibaba1688Panel />}
        {tab === "external_orders" && <OtherChannelOrdersPanel />}
        {tab === "jackyun" && <JackyunPanel />}
        {tab === "tax" && <TaxInvoicesPanel />}
      </div>
    </div>
  );
}
