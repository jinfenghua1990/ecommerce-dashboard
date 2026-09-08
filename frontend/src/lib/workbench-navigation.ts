export const WORKBENCH_VIEWS = {
  orders: "采购订单", merge: "采购合并", suppliers: "供应商管理", chain: "采购链路", matching: "SKU 匹配",
  imports: "数据接入", tax: "发票对账", dashboard: "经营总览", sales: "销售",
  products: "商品与库存", inventory_goods: "库存-正品", inventory_consumables: "库存-耗材",
  payments: "回款与对账", profit: "利润报表", finance: "财务资料",
  exceptions: "异常中心", automation: "自动化", settings: "系统设置",
} as const;

export type WorkbenchView = keyof typeof WORKBENCH_VIEWS;

export function parseWorkbenchView(value: string | null): WorkbenchView {
  // 历史深链兼容：销售出库报表已并入「销售」视图的「出库流水」页签。
  if (value === "sales_outbound") return "sales";
  return value && value in WORKBENCH_VIEWS ? value as WorkbenchView : "orders";
}

const LEGACY_VIEWS: Record<string, WorkbenchView> = {
  "/": "dashboard", "/sales": "sales", "/products": "products",
  "/products/inventory-goods": "inventory_goods", "/products/inventory-consumables": "inventory_consumables",
  "/payments": "payments",
  "/profit": "profit", "/finance": "finance", "/exceptions": "exceptions", "/automation": "automation",
  "/settings": "settings", "/purchase": "orders", "/procurement-workbench": "orders",
  "/procurement-board": "orders", "/procurement-ledger": "chain", "/procurement-chain": "chain",
  "/procurement-chain/detail": "orders", "/purchase/workbench-v2": "orders",
  "/purchase/merge": "merge",
  "/data-center-import": "imports", "/alibaba1688-import": "imports", "/jackyun-import": "imports",
  "/tax-invoices": "imports",
};

/** 旧地址保留筛选和订单上下文，统一进入工作台。 */
export function workbenchHref(href: string): string {
  const [pathname, search = ""] = href.split("?");
  const view = LEGACY_VIEWS[pathname];
  if (!view) return href;
  const params = new URLSearchParams(search);
  if (!params.has("view")) params.set("view", view);
  if (pathname === "/jackyun-import") params.set("tab", "jackyun");
  if (pathname === "/alibaba1688-import") params.set("tab", "alibaba1688");
  if (pathname === "/tax-invoices") params.set("tab", "tax");
  return `/purchase/workbench?${params}`;
}
