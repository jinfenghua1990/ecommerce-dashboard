export const WORKBENCH_VIEWS = {
  orders: "采购订单",
  suppliers: "供应商管理",
  chain: "采购链路",
  matching: "SKU 匹配",
  tax: "发票对账",
} as const;

export type WorkbenchView = keyof typeof WORKBENCH_VIEWS;

const ACTIVE_WORKBENCH_VIEWS = new Set<WorkbenchView>(Object.keys(WORKBENCH_VIEWS) as WorkbenchView[]);

export function parseWorkbenchView(value: string | null): WorkbenchView {
  // V1.6.1：采购工作台源码与运行时都只允许采购域内部 5 个视图。
  // 历史 view=sales/products/finance/... 不再嵌套正式业务页面，统一回到采购订单。
  return value && ACTIVE_WORKBENCH_VIEWS.has(value as WorkbenchView)
    ? (value as WorkbenchView)
    : "orders";
}

/**
 * 仅兼容已经废弃的旧采购 URL。
 * 正式业务页面（/、/sales、/products、/finance、/settings 等）必须保持独立路由，
 * 不允许再被统一重定向进 /purchase/workbench?view=…。
 *
 * 注意：侧栏正式入口会由 frontend/scripts/check-navigation-routes.mjs 在 CI 自动校验，
 * 后续新增菜单时如果目标页面不存在或又被加入这里，CI 会直接失败。
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

/** 旧采购地址保留查询条件与订单上下文，统一进入 V1.6.1 新采购工作台。 */
export function workbenchHref(href: string): string {
  const [pathname, search = ""] = href.split("?");
  const view = LEGACY_PURCHASE_VIEWS[pathname];
  if (!view) return href;

  const params = new URLSearchParams(search);
  if (!params.has("view")) params.set("view", view);
  return `/purchase/workbench?${params.toString()}`;
}
