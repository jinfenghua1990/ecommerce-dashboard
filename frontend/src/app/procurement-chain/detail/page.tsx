"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * V1.6.1 已移除旧版采购链详情页。
 * 历史 ?id= 链接会映射到新版采购工作台的订单详情。
 */
export default function LegacyProcurementChainDetailRedirect() {
  const router = useRouter();

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const legacyId = params.get("id");
    params.delete("id");
    params.set("view", "orders");
    if (legacyId && !params.has("order")) params.set("order", legacyId);
    router.replace(`/purchase/workbench?${params.toString()}`);
  }, [router]);

  return null;
}
