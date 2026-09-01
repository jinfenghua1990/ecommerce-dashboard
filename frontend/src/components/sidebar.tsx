"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/", label: "经营总览", phase: null },
  { href: "/sales", label: "销售", phase: 2 },
  { href: "/products", label: "商品与库存", phase: 2 },
  { href: "/purchase", label: "采购", phase: 3 },
  { href: "/payments", label: "回款", phase: 5 },
  { href: "/profit", label: "利润", phase: 5 },
  { href: "/finance", label: "财务资料", phase: 4 },
  { href: "/exceptions", label: "异常中心", phase: null },
  { href: "/automation", label: "自动化", phase: null },
  { href: "/settings", label: "设置", phase: null },
];

export default function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-gray-200 bg-white">
      <div className="px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">电商经营数据平台</div>
        <div className="mt-1 text-xs text-gray-400">V1 · 局域网信任模式</div>
      </div>
      <nav className="flex-1 space-y-0.5 px-3">
        {NAV.map((item) => {
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-indigo-50 font-medium text-indigo-700"
                  : "text-gray-600 hover:bg-gray-100"
              }`}
            >
              <span>{item.label}</span>
              {item.phase && (
                <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-400">
                  P{item.phase}
                </span>
              )}
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-gray-100 px-5 py-3 text-[11px] leading-5 text-gray-400">
        吉客云 · 1688 · 浙江农信
        <br />
        仅限内网访问，勿开放公网
      </div>
    </aside>
  );
}
