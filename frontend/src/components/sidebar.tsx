"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthUser, fetchMe, logout } from "@/lib/api";

type IconName =
  | "home"
  | "sales"
  | "box"
  | "supply"
  | "factory"
  | "flow"
  | "truck"
  | "receive"
  | "cart"
  | "warehouse"
  | "inventory"
  | "wallet"
  | "profit"
  | "finance"
  | "tax"
  | "import"
  | "alert"
  | "automation"
  | "settings";

type NavItem = {
  href: string;
  label: string;
  icon: IconName;
  nested?: boolean;
  group?: boolean;
};

type NavSection = {
  title?: string;
  items: NavItem[];
};

const NAV_SECTIONS: NavSection[] = [
  {
    items: [
      { href: "/", label: "经营总览", icon: "home" },
      { href: "/sales", label: "销售管理", icon: "sales" },
      { href: "/products", label: "货品档案", icon: "box" },
    ],
  },
  {
    items: [
      { href: "/supply-chain", label: "供应链中心", icon: "supply", group: true },
      { href: "/supply-chain/production", label: "生产订单", icon: "factory", nested: true },
      { href: "/supply-chain/material-flow", label: "耗材流转", icon: "flow", nested: true },
      { href: "/supply-chain/in-transit", label: "生产执行 / 在途", icon: "truck", nested: true },
      { href: "/supply-chain/receiving", label: "到货入库", icon: "receive", nested: true },
      { href: "/purchase/workbench", label: "采购订单", icon: "cart", nested: true },
      { href: "/supply-chain/warehouses", label: "仓库管理", icon: "warehouse", nested: true },
      { href: "/products/inventory-goods", label: "正品库存", icon: "inventory", nested: true },
      { href: "/products/inventory-consumables", label: "耗材管理", icon: "box", nested: true },
    ],
  },
  {
    title: "经营与财务",
    items: [
      { href: "/payments", label: "回款与对账", icon: "wallet" },
      { href: "/profit", label: "利润分析", icon: "profit" },
      { href: "/finance", label: "财务中心", icon: "finance", group: true },
      { href: "/finance/tax-accounting", label: "税务做账", icon: "tax", nested: true },
      { href: "/finance/tax-accounting/categories", label: "分类规则", icon: "settings", nested: true },
    ],
  },
  {
    title: "系统工具",
    items: [
      { href: "/data-center-import", label: "数据接入", icon: "import" },
      { href: "/exceptions", label: "异常中心", icon: "alert" },
      { href: "/automation", label: "自动化", icon: "automation" },
      { href: "/settings", label: "系统设置", icon: "settings" },
    ],
  },
];

function NavIcon({ name }: { name: IconName }) {
  const common = "h-[18px] w-[18px] shrink-0";

  if (name === "home") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3.5 10.5 12 3.7l8.5 6.8v9.2a1 1 0 0 1-1 1h-5v-6h-5v6h-5a1 1 0 0 1-1-1v-9.2Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "sales" || name === "profit") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M4 19V9m5 10V5m5 14v-7m5 7V3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "box" || name === "inventory") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="m4.5 7.2 7.5-4 7.5 4v9.6l-7.5 4-7.5-4V7.2Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="m4.8 7.4 7.2 4 7.2-4M12 11.4v9" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "truck") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3 6h11v10H3V6Zm11 4h4l3 3v3h-7v-6Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <circle cx="7" cy="18" r="1.8" stroke="currentColor" strokeWidth="1.7" /><circle cx="18" cy="18" r="1.8" stroke="currentColor" strokeWidth="1.7" />
      </svg>
    );
  }

  if (name === "warehouse" || name === "factory") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3.5 20V8l8.5-4 8.5 4v12M7 20v-7h10v7M9 9h.01M12 9h.01M15 9h.01" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "cart") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M3 5h2l2 10h10.5l2-7H6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <circle cx="9" cy="19" r="1.5" fill="currentColor" /><circle cx="17" cy="19" r="1.5" fill="currentColor" />
      </svg>
    );
  }

  if (name === "wallet") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H18a2 2 0 0 1 2 2v12H6a2 2 0 0 1-2-2V6.5Zm11 4.5h6v4h-6a2 2 0 1 1 0-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "finance" || name === "tax") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M6 3.5h8l4 4V20H6V3.5Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M14 3.5V8h4M9 12h6M9 15.5h6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "alert") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M12 3.8 21 20H3L12 3.8Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
        <path d="M12 9v5m0 3h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "settings") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="1.7" />
        <path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === "automation" || name === "flow") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M19 7a8 8 0 0 0-13-1L4 8m1-1H4V4M5 17a8 8 0 0 0 13 1l2-2m-1 1h1v3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  if (name === "receive" || name === "import") {
    return (
      <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
        <path d="M12 3v11m0 0 4-4m-4 4-4-4M4 17v3h16v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" className={common} aria-hidden="true">
      <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="m4.5 7.2 7.5 4 7.5-4M12 11.2V21" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    fetchMe().then(setUser).catch(() => {});
  }, []);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      router.replace("/login");
    }
  }

  function isActive(item: NavItem) {
    if (item.href === "/") return pathname === "/";
    if (item.group) return pathname === item.href || pathname.startsWith(`${item.href}/`);
    return pathname === item.href || Boolean(item.nested && pathname.startsWith(`${item.href}/`));
  }

  return (
    <aside className="sticky top-0 flex h-screen w-[248px] shrink-0 flex-col overflow-hidden bg-[#112a49] text-white shadow-[8px_0_28px_rgba(15,39,70,0.08)]">
      <div className="border-b border-white/8 px-5 pb-5 pt-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/10 ring-1 ring-white/10">
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="none" aria-hidden="true">
              <path d="M5 8h12v7a5 5 0 0 1-5 5h-2a5 5 0 0 1-5-5V8Z" fill="white" />
              <path d="M17 10h1.4a2.6 2.6 0 1 1 0 5.2H17" stroke="white" strokeWidth="1.8" />
              <path d="M8 5.5c.4-1 1.2-1.5 2.2-1.7M12 5.5c.3-.9 1-1.4 2-1.7" stroke="white" strokeWidth="1.4" strokeLinecap="round" />
            </svg>
          </div>
          <div className="min-w-0">
            <div className="truncate text-[17px] font-semibold tracking-wide">电商经营数据平台</div>
            <div className="mt-0.5 text-[11px] text-slate-300">供应链 · 财务 · 数据</div>
          </div>
        </div>
        <div className="mt-4 inline-flex rounded-md bg-white/8 px-2 py-1 text-[10px] font-medium tracking-wide text-slate-300 ring-1 ring-white/8">
          V1.6 · LOCAL
        </div>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,.18)_transparent]">
        {NAV_SECTIONS.map((section, sectionIndex) => (
          <div key={sectionIndex} className={sectionIndex === 0 ? "" : "mt-4"}>
            {section.title && (
              <div className="mb-2 px-3 text-[10px] font-medium uppercase tracking-[0.16em] text-slate-400">
                {section.title}
              </div>
            )}
            <div className="space-y-1">
              {section.items.map((item) => {
                const active = isActive(item);
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={`group flex items-center gap-3 rounded-lg text-[13px] transition-all ${
                      item.nested ? "ml-4 px-3 py-2" : "px-3 py-2.5"
                    } ${
                      active
                        ? "bg-[#2574e8] font-medium text-white shadow-[0_6px_18px_rgba(37,116,232,.28)]"
                        : item.nested
                          ? "text-slate-300 hover:bg-white/7 hover:text-white"
                          : "text-slate-200 hover:bg-white/8 hover:text-white"
                    }`}
                  >
                    <span className={active ? "text-white" : "text-slate-400 group-hover:text-slate-200"}>
                      <NavIcon name={item.icon} />
                    </span>
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {item.group && (
                      <svg viewBox="0 0 20 20" fill="none" className="h-3.5 w-3.5 opacity-60" aria-hidden="true">
                        <path d="m7 8 3 3 3-3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </Link>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-white/8 p-4">
        <div className="flex items-center gap-3 rounded-xl bg-white/6 p-3 ring-1 ring-white/6">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-slate-400 to-slate-600 text-sm font-semibold text-white">
            {(user?.displayName || user?.username || "A").slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-medium text-white">
              {user ? user.displayName || user.username : "管理员"}
            </div>
            <div className="mt-0.5 truncate text-[10px] text-slate-400">
              {user ? user.roles.join(" / ") : "admin"}
            </div>
          </div>
          {process.env.NEXT_PUBLIC_ACCESS_MODE !== "open" && (
            <button
              onClick={handleLogout}
              className="rounded-md px-1.5 py-1 text-[10px] text-slate-400 transition-colors hover:bg-white/8 hover:text-white"
            >
              退出
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
