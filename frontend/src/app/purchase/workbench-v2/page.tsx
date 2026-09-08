"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * V1.6.1 已彻底移除旧版 workbench-v2 UI。
 * 旧收藏地址仅保留兼容跳转，所有采购操作统一进入新版采购工作台。
 */
export default function LegacyWorkbenchV2Redirect() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("view")) params.set("view", "orders");
    router.replace(`/purchase/workbench?${params.toString()}`);
  }, [router]);

  return null;
}
