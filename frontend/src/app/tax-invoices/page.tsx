"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// 已归档至统一「数据中心导入」页面：/data-center-import?tab=tax
export default function TaxInvoicesRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/data-center-import?tab=tax");
  }, [router]);
  return (
    <div className="py-20 text-center text-sm text-gray-400">正在跳转到「数据中心导入」…</div>
  );
}
