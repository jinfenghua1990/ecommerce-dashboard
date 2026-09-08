import Link from "next/link";
import ReplenishmentPanel from "./replenishment-panel";

type ModuleCard = {
  title: string;
  description: string;
  href?: string;
  status: "available" | "next";
  badge: string;
};

const MODULES: ModuleCard[] = [
  {
    title: "补货工作台",
    description: "已经接入真实库存、近销数量和待供应数量，可按交期与安全天数动态计算建议补货。",
    href: "#replenishment",
    status: "available",
    badge: "已接入",
  },
  {
    title: "生产订单",
    description: "按工厂安排生产，自动根据正品与耗材关联计算需求；预占不扣库存，缺料直接提示。",
    href: "/supply-chain/production",
    status: "available",
    badge: "V1.3",
  },
  {
    title: "采购订单",
    description: "沿用现有采购工作台，继续管理 1688/其他渠道订单、SKU 匹配、供应商和采购链路。",
    href: "/purchase/workbench",
    status: "available",
    badge: "已接入",
  },
  {
    title: "耗材管理",
    description: "管理彩盒、标签、包装袋、纸箱等耗材库存及与正品的关联。",
    href: "/products/inventory-consumables",
    status: "available",
    badge: "已接入",
  },
  {
    title: "正品库存",
    description: "查看当前成品库存，为补货计划、生产安排和到货入库提供库存基准。",
    href: "/products/inventory-goods",
    status: "available",
    badge: "已接入",
  },
  {
    title: "生产执行 / 在途",
    description: "按生产单逐行登记生产完成、工厂发货、物流在途和部分到货，数量链实时累计。",
    href: "/supply-chain/in-transit",
    status: "available",
    badge: "V1.5",
  },
  {
    title: "到货入库",
    description: "将已到货生产成品与吉客云真实入库明细按 SKU 和数量关联，不重复增加库存。",
    href: "/supply-chain/receiving",
    status: "available",
    badge: "V1.5",
  },
  {
    title: "供应商 / 工厂",
    description: "复用现有供应商视图，后续增加工厂类型、交期和生产能力字段。",
    href: "/purchase/workbench?view=suppliers",
    status: "available",
    badge: "已接入",
  },
];

const FLOW = ["库存判断", "补货计划", "生产 / 采购", "耗材备料", "生产 / 在途", "到货验收", "吉客云入库"];

export default function SupplyChainPage() {
  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN CENTER</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">供应链中心</h1>
            <p className="mt-1 text-sm text-slate-500">补货 · 生产 · 采购 · 耗材 · 在途 · 到货</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link href="/supply-chain/production" className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-600 transition hover:bg-slate-50">生产订单</Link>
            <Link href="/supply-chain/in-transit" className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-100">生产执行 / 在途</Link>
            <Link href="/supply-chain/receiving" className="rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-medium text-indigo-700 transition hover:bg-indigo-100">到货入库</Link>
            <Link href="/purchase/workbench" className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-700">采购订单</Link>
          </div>
        </div>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900">供应补货主流程</h2>
            <p className="mt-1 text-xs text-slate-500">补货计划之后选择“工厂生产”或“直接采购”，生产过程与最终库存事实分开管理。</p>
          </div>
          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">统一运行于 8000 端口</span>
        </div>
        <div className="grid gap-2 lg:grid-cols-7">
          {FLOW.map((item, index) => (
            <div key={item} className="relative rounded-xl border border-slate-200 bg-slate-50 px-3 py-4 text-center">
              <div className="text-[10px] font-semibold text-slate-400">{String(index + 1).padStart(2, "0")}</div>
              <div className="mt-1 text-sm font-medium text-slate-700">{item}</div>
              {index < FLOW.length - 1 && <span className="absolute -right-2 top-1/2 z-10 hidden -translate-y-1/2 text-slate-300 lg:block">→</span>}
            </div>
          ))}
        </div>
      </section>

      <div id="replenishment" className="scroll-mt-24">
        <ReplenishmentPanel />
      </div>

      <section>
        <div className="mb-3">
          <h2 className="text-base font-semibold text-slate-900">功能模块</h2>
          <p className="mt-1 text-xs text-slate-500">补货、生产、采购、耗材、在途、到货和库存均已进入同一供应链入口。</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {MODULES.map((module) => {
            const card = (
              <div className={`h-full rounded-2xl border p-5 transition ${module.href ? "border-slate-200 bg-white hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-sm" : "border-dashed border-slate-200 bg-slate-50/70"}`}>
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-base font-semibold text-slate-900">{module.title}</h3>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-[10px] font-medium ${module.status === "available" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                    {module.badge}
                  </span>
                </div>
                <p className="mt-3 text-sm leading-6 text-slate-500">{module.description}</p>
                <div className="mt-5 text-xs font-medium text-indigo-600">{module.href ? "打开模块 →" : "按真实数据接口逐步接入"}</div>
              </div>
            );
            return module.href ? <Link key={module.title} href={module.href}>{card}</Link> : <div key={module.title}>{card}</div>;
          })}
        </div>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-slate-950 px-5 py-4 text-slate-200">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-white">V1.5 生产成品闭环</div>
            <div className="mt-1 text-xs leading-5 text-slate-400">生产完成、工厂待发、成品在途、部分到货和吉客云真实入库关联已形成数量链；本系统记录过程，最终正品库存仍以吉客云库存快照为准。</div>
          </div>
          <div className="text-xs text-slate-400">计划 → 已生产 → 已发货 → 已到货 → 已入库</div>
        </div>
      </section>
    </div>
  );
}