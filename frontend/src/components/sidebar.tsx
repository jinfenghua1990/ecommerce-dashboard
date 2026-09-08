"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthUser, fetchMe, logout } from "@/lib/api";

type NavItem = {
  href: string;
  label: string;
  nested?: boolean;
  group?: boolean;
};

type NavSection = {
  title: string;
  items: NavItem[];
};

const NAV_SECTIONS: NavSection[] = [
  {
    title: "经营",
    items: [
      { href: "/", label: "经营总览" },
      { href: "/sales", label: "销售" },
      { href: "/products", label: "货品档案" },
    ],
  },
  {
    title: "供应链",
    items: [
      { href: "/supply-chain", label: "供应链中心", group: true },
      { href: "/supply-chain/production", label: "生产订单", nested: true },
      { href: "/supply-chain/material-flow", label: "耗材流转", nested: true },
      { href: "/purchase/workbench", label: "采购订单", nested: true },
      { href: "/purchase/merge", label: "采购合并", nested: true },
      { href: "/supply-chain/in-transit", label: "生产 / 在途", nested: true },
      { href: "/supply-chain/receiving", label: "到货入库", nested: true },
      { href: "/supply-chain/warehouses", label: "仓库", nested: true },
      { href: "/products/inventory-goods", label: "正品库存", nested: true },
      { href: "/products/inventory-consumables", label: "耗材库存", nested: true },
    ],
  },
  {
    title: "财务",
    items: [
      { href: "/payments", label: "回款" },
      { href: "/profit", label: "利润" },
      { href: "/finance", label: "财务中心", group: true },
      { href: "/finance/tax-accounting", label: "税务做账", nested: true },
      { href: "/finance/tax-accounting/categories", label: "分类规则", nested: true },
    ],
  },
  {
    title: "系统",
    items: [
      { href: "/data-center-import", label: "数据中心导入" },
      { href: "/exceptions", label: "异常中心" },
      { href: "/automation", label: "自动化" },
      { href: "/settings", label: "设置" },
    ],
  },
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
    <aside className="flex h-screen w-56 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-5 py-4">
        <div className="text-base font-semibold tracking-tight text-slate-900">电商经营数据平台</div>
        <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
          <span>V1.6</span>
          <span>·</span>
          <span>{process.env.NEXT_PUBLIC_ACCESS_MODE === "open" ? "局域网直达" : "已启用登录"}</span>
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-3">
        <div className="space-y-4">
          {NAV_SECTIONS.map((section) => (
            <section key={section.title}>
              <div className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                {section.title}
              </div>
              <div className="space-y-0.5">
                {section.items.map((item) => {
                  const active = item.group
                    ? pathname === item.href || pathname.startsWith(`${item.href}/`)
                    : pathname === item.href || Boolean(item.nested && pathname.startsWith(`${item.href}/`));

                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      className={`flex min-h-9 items-center rounded-lg py-1.5 text-sm transition-colors ${
                        item.nested ? "ml-3 border-l border-slate-200 pl-4 pr-2" : "px-3"
                      } ${
                        active
                          ? "bg-indigo-50 font-medium text-indigo-700"
                          : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                      }`}
                    >
                      <span className="truncate">{item.label}</span>
                    </Link>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      </nav>

      <div className="border-t border-slate-100 px-5 py-3">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-slate-700">
              {user ? user.displayName || user.username : "…"}
            </div>
            <div className="truncate text-[10px] text-slate-400">
              {user ? user.roles.join(" / ") : ""}
            </div>
          </div>
          {process.env.NEXT_PUBLIC_ACCESS_MODE !== "open" && (
            <button
              onClick={handleLogout}
              className="shrink-0 rounded-md border border-slate-200 px-2 py-1 text-[11px] text-slate-600 transition-colors hover:bg-slate-50"
            >
              退出
            </button>
          )}
        </div>
        <div className="mt-2 text-[10px] leading-5 text-slate-400">吉客云 · 1688 · 浙江农信</div>
      </div>
    </aside>
  );
}
