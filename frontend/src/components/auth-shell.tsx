"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "@/components/sidebar";
import { fetchMe, getToken, redirectToLogin } from "@/lib/api";
import { workbenchHref } from "@/lib/workbench-navigation";

/**
 * 路由守卫：除 /login 外，先向服务端校验令牌，再渲染业务页面。
 *
 * V1.6.1 统一使用同一套全局侧栏 + 动态主内容区。
 * 采购工作台仍保留自己的业务视图组件，但旧 WorkbenchSidebar 由全局样式隐藏，
 * 避免出现“双侧栏 / 套工作台”的嵌套体验。
 */
export default function AuthShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const openAccess = process.env.NEXT_PUBLIC_ACCESS_MODE === "open";

  useEffect(() => {
    if (pathname === "/login") {
      setReady(true);
      return;
    }

    // 仅废弃旧采购地址做兼容跳转；正式业务页保持自己的平铺路由。
    const destination = workbenchHref(pathname + window.location.search);
    if (destination !== pathname + window.location.search) {
      setReady(false);
      router.replace(destination);
      return;
    }

    if (openAccess) {
      setReady(true);
      return;
    }
    if (!getToken()) {
      router.replace("/login");
      return;
    }

    let cancelled = false;
    setReady(false);
    fetchMe()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch(() => {
        if (!cancelled) redirectToLogin();
      });
    return () => {
      cancelled = true;
    };
  }, [openAccess, pathname, router]);

  if (pathname === "/login") return <>{children}</>;
  if (!ready) return null;

  const purchaseWorkbench = pathname === "/purchase/workbench";

  return (
    <div className="flex h-screen w-full min-w-0 overflow-hidden bg-[#f4f7fb]">
      <Sidebar />
      <main
        data-app-main
        data-purchase-workbench={purchaseWorkbench ? "true" : undefined}
        className={`h-screen w-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto ${
          purchaseWorkbench ? "p-0" : "px-6 py-5 xl:px-8 xl:py-6"
        }`}
      >
        <div className="app-route-content w-full min-w-0">{children}</div>
      </main>
    </div>
  );
}
