"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Sidebar from "@/components/sidebar";
import { fetchMe, getToken, redirectToLogin } from "@/lib/api";
import { workbenchHref } from "@/lib/workbench-navigation";

/**
 * 路由守卫：除 /login 外，先向服务端校验令牌，再渲染业务页面。
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
    // 旧路由一律收敛进 /purchase/workbench?view=…（open 模式同样生效，保证单一外壳）
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

  if (pathname === "/login") {
    return <>{children}</>;
  }
  if (!ready) {
    return null;
  }

  if (pathname === "/purchase/workbench") {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto px-8 py-6">{children}</main>
    </div>
  );
}
