"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";

/** 采购域整合：原 /purchase 完整编辑器已并入 /purchase/workbench 详情面板，本页仅做重定向。 */
export default function PurchaseLegacyRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/purchase/workbench?view=orders"); }, [router]);
  return null;
}