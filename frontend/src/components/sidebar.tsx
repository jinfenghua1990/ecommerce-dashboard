"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthUser, fetchMe, logout } from "@/lib/api";

type NavItem = { href: string; label: string; phase: number | null; nested?: boolean; group?: boolean };

const NAV: NavItem[] = [
  { href: "/", label: "经营总览", phase: null },
  { href: "/sales", label: "销售", phase: 2 },
  { href: "/products", label: "商品档案", phase: 2 },
  { href: "/supply-chain", label: "供应链中心", phase: null, group: true },
  { href: "/supply-chain/production", label: "生产订单", phase: null, nested: true },
  { href: "/purchase/workbench", label: "采购订单", phase: 3, nested: true },
  { href: "/products/inventory-goods", label: "正品库存", phase: null, nested: true },
  { href: "/products/inventory-consumables", label: "耗材管理", phase: null, nested: true },
  { href: "/payments", label: "回款", phase: 5 },
  { href: "/profit", label: "利润", phase: 5 },
  { href: "/finance", label: "财务资料", phase: 4 },
  { href: "/data-center-import", label: "数据中心导入", phase: null },
  { href: "/exceptions", label: "异常中心", phase: null },
  { href: "/automation", label: "自动化", phase: null },
  { href: "/settings", label: "设置", phase: null },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    fetchMe()
      .then(setUser)
      .catch(() => {});
  }, []);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  }

  return (
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-gray-200 bg-white">
      <div className="px-5 py-5">
        <div className="text-lg font-semibold tracking-tight">电商经营数据平台</div>
        <div className="mt-1 text-xs text-gray-400">
          {process.env.NEXT_PUBLIC_ACCESS_MODE === "open" ? "V1 · 局域网直达" : "V1 · 已启用登录"}
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 px-3">
        {NAV.map((item) => {
          const active = item.group
            ? pathname === item.href || pathname.startsWith(`${item.href}/`)
            : pathname === item.href || Boolean(item.nested && pathname.startsWith(`${item.href}/`));
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center justify-between rounded-lg py-2 text-sm transition-colors ${
                item.nested ? "ml-3 border-l border-gray-200 pl-5" : "px-3"
              } ${
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
      <div className="border-t border-gray-100 px-5 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-gray-700">
              {user ? user.displayName || user.username : "…"}
            </div>
            <div className="truncate text-[10px] text-gray-400">
              {user ? user.roles.join(" / ") : ""}
            </div>
          </div>
          {process.env.NEXT_PUBLIC_ACCESS_MODE !== "open" && (
            <button
              onClick={handleLogout}
              className="shrink-0 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 transition-colors hover:bg-gray-50"
            >
              退出
            </button>
          )}
        </div>
        <div className="mt-2 text-[11px] leading-5 text-gray-400">
          吉客云 · 1688 · 浙江农信
        </div>
      </div>
    </aside>
  );
}
