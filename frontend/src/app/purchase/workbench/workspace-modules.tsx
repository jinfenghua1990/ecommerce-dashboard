"use client";

import dynamic from "next/dynamic";
import type { WorkbenchView } from "@/lib/workbench-navigation";

const loading = () => <div className="p-8 text-sm text-slate-400">正在加载功能…</div>;

// V1.6.1：采购工作台不再嵌入销售、货品、财务、设置等正式业务页面。
// 这里只保留采购域自身的发票对账视图；其它模块必须通过全局侧栏进入独立路由。
const InvoiceReconciliationView = dynamic(() => import("./invoice-reconciliation-view"), { loading });

export function WorkspaceModule({ view }: { view: WorkbenchView }) {
  if (view !== "tax") return null;
  return (
    <section className="mt-5 min-w-0 overflow-x-auto rounded-xl border border-slate-200 bg-white p-5">
      <InvoiceReconciliationView />
    </section>
  );
}
