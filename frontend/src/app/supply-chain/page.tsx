import Link from "next/link";
import ReplenishmentPanel from "./replenishment-panel";

type ModuleCard = {
  title: string;
  description: string;
  href: string;
  badge: string;
};

type FlowStep = {
  label: string;
  href: string;
};

const QUICK_NAV = [
  { label: "补货", href: "#replenishment" },
  { label: "生产订单", href: "/supply-chain/production" },
  { label: "耗材流转", href: "/supply-chain/material-flow" },
  { label: "采购订单", href: "/purchase/workbench" },
  { label: "生产 / 在途", href: "/supply-chain/in-transit" },
  { label: "到货入库", href: "/supply-chain/receiving" },
  { label: "仓库", href: "/supply-chain/warehouses" },
  { label: "货品档案", href: "/products" },
];

const MODULES: ModuleCard[] = [
  {
    title: "补货工作台",
    description: "按真实库存、近销、待供应、交期和安全天数计算建议补货，先判断要不要补、补多少。",
    href: "#replenishment",
    badge: "决策入口",
  },
  {
    title: "生产订单",
    description: "安排工厂生产，多 SKU 自动计算耗材需求；支持缺料提示、部分生产和生产单跟踪。",
    href: "/supply-chain/production",
    badge: "工厂生产",
  },
  {
    title: "采购订单",
    description: "1688 / 淘宝 / 其他采购统一进入采购工作台，默认按下单时间倒序，供应商另有独立视图。",
    href: "/purchase/workbench",
    badge: "直接采购",
  },
  {
    title: "耗材流转",
    description: "彩盒、标签、包装袋、纸箱等从库存预占，到发往工厂、工厂签收和实际消耗形成流水。",
    href: "/supply-chain/material-flow",
    badge: "备料",
  },
  {
    title: "生产 / 在途",
    description: "同一行登记生产完成、工厂发货、物流在途和到货数量，减少来回切页面和重复录入。",
    href: "/supply-chain/in-transit",
    badge: "执行",
  },
  {
    title: "到货入库",
    description: "将生产到货与吉客云真实入库明细做数量关联；系统记录过程，库存事实仍以吉客云为准。",
    href: "/supply-chain/receiving",
    badge: "闭环",
  },
  {
    title: "仓库",
    description: "统一维护工厂仓和 B2C 仓，可新增、改名、启停并绑定吉客云仓库 ID。",
    href: "/supply-chain/warehouses",
    badge: "基础资料",
  },
  {
    title: "正品 / 耗材库存",
    description: "正品和耗材继续分开看库存，但货品档案统一维护，并保留正品与耗材关联关系。",
    href: "/products",
    badge: "货品基础",
  },
];

const FLOW: FlowStep[] = [
  { label: "库存判断", href: "#replenishment" },
  { label: "补货计划", href: "#replenishment" },
  { label: "生产 / 采购", href: "/supply-chain/production" },
  { label: "耗材备料", href: "/supply-chain/material-flow" },
  { label: "生产 / 在途", href: "/supply-chain/in-transit" },
  { label: "到货验收", href: "/supply-chain/receiving" },
  { label: "吉客云入库", href: "/supply-chain/receiving" },
];

export default function SupplyChainPage() {
  return (
    <div className="mx-auto max-w-[1560px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-4 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">SUPPLY CHAIN CENTER</div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">供应链中心</h1>
            <p className="mt-1 text-sm text-slate-500">从库存判断到生产、采购、耗材、在途、到货和入库，一个入口完成。</p>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">V1.6.2 · 业务版</span>
            <Link href="/supply-chain/production" className="rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-700">
              新建生产单
            </Link>
          </div>
        </div>

        <nav className="mt-3 flex gap-1 overflow-x-auto pb-0.5 text-sm whitespace-nowrap">
          {QUICK_NAV.map((item) => (
            <Link
              key={`${item.label}-${item.href}`}
              href={item.href}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-slate-600 transition hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </header>

      <section className="rounded-2xl border border-slate-200 bg-white p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-900">供应链主流程</h2>
            <p className="mt-1 text-xs text-slate-500">先看库存，再决定生产或采购；过程数量与吉客云最终库存事实分开。</p>
          </div>
          <span className="text-xs text-slate-400">按日常操作顺序从左到右</span>
        </div>
        <div className="grid gap-2 lg:grid-cols-7">
          {FLOW.map((item, index) => (
            <Link
              key={`${item.label}-${index}`}
              href={item.href}
              className="group relative rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-center transition hover:border-indigo-200 hover:bg-indigo-50"
            >
              <div className="text-[10px] font-semibold text-slate-400 group-hover:text-indigo-400">{String(index + 1).padStart(2, "0")}</div>
              <div className="mt-1 text-sm font-medium text-slate-700 group-hover:text-indigo-700">{item.label}</div>
              {index < FLOW.length - 1 && <span className="absolute -right-2 top-1/2 z-10 hidden -translate-y-1/2 text-slate-300 lg:block">→</span>}
            </Link>
          ))}
        </div>
      </section>

      <div id="replenishment" className="scroll-mt-36">
        <ReplenishmentPanel />
      </div>

      <section className="space-y-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-900">业务入口</h2>
            <p className="mt-1 text-xs text-slate-500">不做折叠，常用功能全部直接展示；能在一页完成的操作尽量不拆步骤。</p>
          </div>
          <Link href="/purchase/workbench?view=suppliers" className="text-xs font-medium text-indigo-600 hover:text-indigo-700">
            查看供应商视图 →
          </Link>
        </div>

        <div className="grid gap-3 xl:grid-cols-2">
          {MODULES.map((module) => (
            <Link
              key={module.title}
              href={module.href}
              className="group flex items-start gap-4 rounded-xl border border-slate-200 bg-white px-4 py-3.5 transition hover:border-indigo-200 hover:bg-indigo-50/30"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-slate-900 group-hover:text-indigo-700">{module.title}</h3>
                  <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-500 group-hover:bg-indigo-100 group-hover:text-indigo-700">
                    {module.badge}
                  </span>
                </div>
                <p className="mt-1.5 text-xs leading-5 text-slate-500">{module.description}</p>
              </div>
              <span className="mt-1 shrink-0 text-sm text-slate-300 transition group-hover:translate-x-0.5 group-hover:text-indigo-500">→</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-slate-950 px-4 py-3 text-slate-200">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-white">V1.6.2 供应链中心</div>
            <div className="mt-1 text-xs leading-5 text-slate-400">
              采购、生产、耗材、仓库、在途、到货已统一到同一业务入口；历史兼容路由保留，但后续新增功能只围绕这一套主流程继续扩展。
            </div>
          </div>
          <div className="text-xs text-slate-400">库存 → 决策 → 执行 → 到货 → 入库</div>
        </div>
      </section>
    </div>
  );
}
