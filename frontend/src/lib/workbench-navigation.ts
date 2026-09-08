export const WORKBENCH_VIEWS = {
  orders: "采购订单",
  suppliers: "供应商管理",
  chain: "采购链路",
  matching: "SKU 匹配",
  imports: "数据接入",
  tax: "发票对账",
  dashboard: "经营总览",
  sales: "销售",
  products: "商品与库存",
  inventory_goods: "库存-正品",
  inventory_consumables: "库存-耗材",
  payments: "回款与对账",
  profit: "利润报表",
  finance: "财务资料",
  exceptions: "异常中心",
  automation: "自动化",
  settings: "系统设置",
} as const;

export type WorkbenchView = keyof typeof WORKBENCH_VIEWS;

export function parseWorkbenchView(value: string | null): WorkbenchView {
  // 采购工作台内部仍兼容历史 view 参数，但不再接管正式业务页面。
  if (value === "sales_outbound") return "sales";
  return value && value in WORKBENCH_VIEWS ? (value as WorkbenchView) : "orders";
}

/**
 * 仅兼容已经废弃的旧采购入口。
 * 正式业务页面（/、/sales、/products、/finance、/settings 等）必须保持独立路由，
 * 不允许再被统一重定向进 /purchase/workbench?view=…。
 */
const LEGACY_PURCHASE_VIEWS: Record<string, WorkbenchView> = {
  "/purchase": "orders",
  "/procurement-workbench": "orders",
  "/procurement-board": "orders",
  "/procurement-ledger": "chain",
  "/procurement-chain": "chain",
  "/procurement-chain/detail": "orders",
  "/purchase/workbench-v2": "orders",
};

/** 旧采购地址保留查询条件与订单上下文，统一进入正式采购工作台。 */
export function workbenchHref(href: string): string {
  const [pathname, search = ""] = href.split("?");
  const view = LEGACY_PURCHASE_VIEWS[pathname];
  if (!view) return href;

  const params = new URLSearchParams(search);
  if (!params.has("view")) params.set("view", view);
  return `/purchase/workbench?${params.toString()}`;
}
