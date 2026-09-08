type PurchaseWorkbenchView = "orders" | "suppliers" | "chain" | "matching" | "tax";

// 仅为兼容正式采购页中尚待清理的旧侧栏死代码类型；这些值不会进入新版菜单、URL 解析或运行时视图。
type LegacyWorkbenchView =
  | "imports"
  | "dashboard"
  | "sales"
  | "products"
  | "inventory_goods"
  | "inventory_consumables"
  | "payments"
  | "profit"
  | "finance"
  | "exceptions"
  | "automation"
  | "settings";

export type WorkbenchView = PurchaseWorkbenchView | LegacyWorkbenchView;

export const WORKBENCH_VIEWS: Partial<Record<WorkbenchView, string>> = {
  orders: "采购订单",
  suppliers: "供应商管理",
  chain: "采购链路",
  matching: "SKU 匹配",
  tax: "发票对账",
};

const ACTIVE_WORKBENCH_VIEWS = new Set<PurchaseWorkbenchView>(["orders", "suppliers", "chain", "matching", "tax"]);

export function parseWorkbenchView(value: string | null): PurchaseWorkbenchView {
  // V1.6.1：运行时只允许采购域内部 5 个视图。
  // 历史 view=sales/products/finance/... 不再嵌套正式业务页面，统一回到采购订单。
  return value && ACTIVE_WORKBENCH_VIEWS.has(value as PurchaseWorkbenchView)
    ? (value as PurchaseWorkbenchView)
    : "orders";
}

/**
 * 仅兼容已经废弃的旧采购入口。
 * 正式业务页面（/、/sales、/products、/finance、/settings 等）必须保持独立路由，
 * 不允许再被统一重定向进 /purchase/workbench?view=…。
 *
 * 注意：侧栏正式入口会由 frontend/scripts/check-navigation-routes.mjs 在 CI 自动校验，
 * 后续新增菜单时如果目标页面不存在或又被加入这里，CI 会直接失败。
 */
const LEGACY_PURCHASE_VIEWS: Record<string, PurchaseWorkbenchView> = {
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
