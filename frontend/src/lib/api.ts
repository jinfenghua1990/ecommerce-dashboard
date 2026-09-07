// ---------- 登录令牌（localStorage 保存，请求统一携带 Bearer） ----------

const TOKEN_KEY = "ecdp_access_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

export function redirectToLogin() {
  if (typeof window !== "undefined") {
    clearToken();
    window.location.href = "/login";
  }
}

/** 所有业务请求统一携带令牌，并在服务端判定失效时回登录页。 */
export async function authenticatedFetch(
  input: RequestInfo | URL,
  init: RequestInit = {}
): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401 && process.env.NEXT_PUBLIC_ACCESS_MODE !== "open") {
    redirectToLogin();
    throw new Error("登录已过期");
  }
  return res;
}

/** FastAPI 的 detail 可能是字符串（HTTPException）或 422 校验错误数组；统一转成可读文案。 */
function detailToMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        const d = item as { msg?: string; loc?: unknown[] };
        const loc = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body") : [];
        return `${loc.length ? `${loc.join(".")}: ` : ""}${d.msg ?? ""}`.trim();
      })
      .filter(Boolean);
    if (parts.length) return parts.join("；");
  }
  if (detail && typeof detail === "object") {
    try { return JSON.stringify(detail); } catch { /* 退回 fallback */ }
  }
  return fallback;
}

// ---------- 认证 ----------

export type AuthUser = {
  id: number;
  username: string;
  displayName: string;
  roles: string[];
  isActive: boolean;
};

export async function login(
  username: string,
  password: string,
  rememberMe = false
): Promise<AuthUser> {
  const res = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember_me: rememberMe }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `登录失败（${res.status}）`));
  }
  const data = (await res.json()) as { accessToken: string; user: AuthUser };
  setToken(data.accessToken);
  return data.user;
}

export async function fetchMe(): Promise<AuthUser> {
  const res = await authenticatedFetch("/api/v1/auth/me");
  if (!res.ok) throw new Error(`me ${res.status}`);
  return res.json();
}

export async function logout(): Promise<void> {
  try {
    await authenticatedFetch("/api/v1/auth/logout", { method: "POST" });
  } finally {
    clearToken();
  }
}

export async function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  const res = await authenticatedFetch("/api/v1/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `修改失败（${res.status}）`));
  }
  clearToken();
  window.location.href = "/login?passwordChanged=1";
}

export type IntegrationStatus = {
  id: string;
  name: string;
  mode: string;
  phase: number;
  status: string;
  lastTestedAt: string | null;
  lastSuccessAt: string | null;
  errorSummary: string | null;
};

export type Overview = {
  phase: number;
  phaseName: string;
  accessMode: string;
  dataState: string;
  integrations: IntegrationStatus[];
  pendingExceptions: number;
  nextMilestone: string;
  metrics: Record<string, number | null>;
};

export async function getOverview(): Promise<Overview> {
  const res = await authenticatedFetch("/api/v1/system/overview", {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`overview ${res.status}`);
  return res.json();
}

export async function testJackyun(): Promise<{
  ok: boolean; status?: string; businessReady?: boolean; tools?: string[]; error?: string;
}> {
  const res = await authenticatedFetch("/api/v1/integrations/jackyun/test", {
    method: "POST",
  });
  return res.json();
}

export type ExceptionRow = {
  id: number;
  code: string;
  type: string;
  severity: string;
  title: string;
  detail: Record<string, unknown>;
  status: string;
  createdAt: string | null;
  handledBy: string;
  note: string;
};

export async function getExceptions(): Promise<ExceptionRow[]> {
  const res = await authenticatedFetch("/api/v1/exceptions", {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`exceptions ${res.status}`);
  return res.json();
}

export async function updateExceptionStatus(
  id: number,
  status: string,
  note: string
): Promise<void> {
  await authenticatedFetch(`/api/v1/exceptions/${id}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status, note }),
  });
}

// ---------- 回款中心（Phase 5） ----------

export type RuleRow = {
  id: number;
  matchPattern: string;
  matchType: string;
  platform: string;
  enabled: boolean;
  note: string;
};

export type ReconTxn = {
  id: number;
  txnDate: string;
  direction: string;
  amount: string;
  counterpartyName: string;
  summary: string;
  voucherNo: string;
  matched: boolean;
};

export type SettlementRow = {
  id: number;
  platform: string;
  storeName: string;
  period: string;
  expectedAmount: string;
  settledAmount: string;
  status: string;
};

export type Suggestion = {
  txnId: number;
  txnDate: string;
  counterparty: string;
  amount: string;
  settlementId: number;
  platform: string;
  period: string;
  expectedAmount: string;
  score: number;
  confidence: string;
  reasons: string[];
};

export type ReconOverview = {
  receivable: string;
  received: string;
  pending: string;
  byPlatform: Record<string, { expected: string; settled: string }>;
};

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `HTTP ${res.status}`));
  }
  return res.json();
}

/** 仅校验状态码的 DELETE（软删除接口返回 204 无响应体）。 */
async function noContentFetch(url: string): Promise<void> {
  const res = await authenticatedFetch(url, { method: "DELETE" });
  if (!res.ok) {
    const data = (await res.json().catch(() => ({}))) as { detail?: unknown };
    throw new Error(detailToMessage(data.detail, `HTTP ${res.status}`));
  }
}

export const reconApi = {
  overview: () => jsonFetch<ReconOverview>("/api/v1/reconciliation/overview"),
  rules: () => jsonFetch<RuleRow[]>("/api/v1/reconciliation/rules"),
  createRule: (body: { match_pattern: string; match_type: string; platform: string; note?: string }) =>
    jsonFetch<{ id: number }>("/api/v1/reconciliation/rules", { method: "POST", body: JSON.stringify(body) }),
  deleteRule: (id: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/reconciliation/rules/${id}`, { method: "DELETE" }),
  transactions: () => jsonFetch<ReconTxn[]>("/api/v1/reconciliation/transactions"),
  createTxn: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; created: boolean; fingerprint: string }>("/api/v1/reconciliation/transactions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  settlements: () => jsonFetch<SettlementRow[]>("/api/v1/reconciliation/settlements"),
  createSettlement: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number }>("/api/v1/reconciliation/settlements", { method: "POST", body: JSON.stringify(body) }),
  suggestions: () => jsonFetch<Suggestion[]>("/api/v1/reconciliation/suggestions"),
  confirm: (txnId: number, settlementId: number) =>
    jsonFetch<{ id: number; score: number; confidence: string }>("/api/v1/reconciliation/confirm", {
      method: "POST",
      body: JSON.stringify({ txn_id: txnId, settlement_id: settlementId }),
    }),
  reject: (txnId: number, settlementId: number) =>
    jsonFetch<{ ok: boolean }>("/api/v1/reconciliation/reject", {
      method: "POST",
      body: JSON.stringify({ txn_id: txnId, settlement_id: settlementId }),
    }),
};

// ---------- 利润中心（Phase 5） ----------

export type CostRow = {
  id: number;
  skuId: number;
  skuCode: string;
  skuName: string;
  period: string;
  values: { actual: string | null; purchOrder: string | null; default: string | null; estimated: string | null };
  effectiveCost: string | null;
  effectiveSource: string | null;
  version: number;
};

export type ProfitOverview = {
  skuCount: number;
  snapshotCount: number;
  coverage: Record<string, number>;
  contributionProfitEnabled: boolean;
};

export type ProfitCompute = {
  period: string;
  netSales: string | null;
  goodsCost: string | null;
  grossProfit: string | null;
  costMissingSkus: number[];
  unmappedItems: number[];
  quantityMissingItems: number[];
  costMissing: boolean;
  note: string;
};

export const profitApi = {
  overview: () => jsonFetch<ProfitOverview>("/api/v1/profit/overview"),
  costs: (year?: number, month?: number) => {
    const q = new URLSearchParams();
    if (year) q.set("period_year", String(year));
    if (month) q.set("period_month", String(month));
    return jsonFetch<CostRow[]>(`/api/v1/profit/costs${q.toString() ? `?${q}` : ""}`);
  },
  upsertCost: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; version: number }>("/api/v1/profit/costs", { method: "POST", body: JSON.stringify(body) }),
  compute: (year: number, month: number) =>
    jsonFetch<ProfitCompute>(`/api/v1/profit/compute?period_year=${year}&period_month=${month}`),
};

// ---------- 经营看板（Phase 2） ----------

export type TrendPoint = { date: string; orders: number; salesAmount: string | null };
export type PlatformRow = { platform: string; orders: number; salesAmount: string | null };
export type SkuRow = { skuCode: string; goodsName: string; orders: number; salesAmount: string | null };
export type InventorySummary = {
  skuCount: number;
  snapshotAt: string | null;
  totalQuantity: string | null;
  byWarehouse: { warehouseId: number | null; quantity: string | null; skus: number }[];
  note?: string;
};
export type InventorySkuRow = {
  skuId: number;
  jackyunSkuId: string;
  skuCode: string;
  productType: "single" | "bundle" | "virtual_bundle";
  skuName: string;
  goodsName: string;
  barcode: string;
  unit: string;
  status: string;
  quantity: string | null;
  hasSnapshot: boolean;
  warehouses: { warehouseId: number | null; warehouseName: string; quantity: string | null }[];
  snapshotAt: string | null;
};
export type SalesOrderRow = {
  id: number; orderNo: string; platform: string; storeName: string;
  orderStatus: string; payStatus: string;
  orderAmount: string | null; paidAmount: string | null; orderedAt: string | null;
};
export type AftersaleRow = {
  id: number; aftersaleNo: string; orderNo: string; type: string; status: string;
  refundAmount: string | null; reason: string; createdAt: string | null;
};
export type CatalogSkuRow = {
  id: number; jackyunSkuId: string; skuCode: string; skuName: string; goodsName: string;
  productType: "single" | "bundle" | "virtual_bundle";
  barcode: string; unit: string; salePrice: string | null; defaultCost: string | null;
  costMode: "fixed" | "dynamic"; costTolerancePct: string; taxCode: string; status: string;
};

export type LinkedSkuRef = { skuId: number; skuCode: string; skuName: string };

/** 统一货品档案行：kind=goods（正品，库存读吉客云快照）/ kind=consumable（耗材，库存本系统三仓维护）。 */
export type UnifiedCatalogRow = {
  kind: "goods" | "consumable";
  id: number; code: string; name: string; goodsName: string;
  barcode: string; unit: string; category: string; goodsCategory: string; status: string;
  stockOwn: string | null;   // 正品=吉客云快照库存；耗材=自有仓
  stockFactory: string | null; // 仅耗材：工厂仓
  stockTransit: string | null; // 仅耗材：在途
  minStock: string | null;   // 仅耗材：安全库存
  lowStock: boolean;         // 仅耗材：可用≤安全库存或负库存
  hasSnapshot: boolean | null;
  linkedSkus: LinkedSkuRef[];
  costMode: "fixed" | "dynamic" | null;
  costTolerancePct: string | null;
  taxCode: string;
  salePrice: string | null;
  defaultCost: string | null;
};

export type ConsumableRow = {
  id: number; code: string; name: string; barcode: string; category: string; unit: string;
  purchaseUnitCost: string | null; purchasedQty: string; usedQty: string;
  stockQty: string; factoryQty: string; transitQty: string; availableQty: string;
  minStockQty: string; usageRate: string; taxCode: string;
  status: string; mappingCount: number; linkedSkus: LinkedSkuRef[]; lowStock: boolean;
};
export type ConsumableMappingRow = {
  id: number; skuId: number; skuCode: string; skuName: string;
  consumableId: number; consumableCode: string; consumableName: string;
  usagePerUnit: string; note: string;
};
export type ConsumableTransactionRow = {
  id: number; transactionType: string; quantity: string; unitCost: string | null;
  location: string | null;
  stockBefore: string | null; stockAfter: string | null;
  factoryBefore: string | null; factoryAfter: string | null;
  sourceType: string; sourceId: number | null; note: string; occurredAt: string | null;
  purchaseId: number | null; orderId: number | null;
};

export type ConsumablePurchaseItem = {
  id: number; consumableId: number; code: string; name: string; unit: string;
  quantity: string; receivedQty: string; unitCost: string;
};
export type ConsumablePurchaseRow = {
  id: number; number: string; supplierName: string; orderedOn: string;
  sourceOrderId: number | null; sourceOrderNo: string | null; referenceNo: string;
  status: "ordered" | "partial" | "received" | "cancelled";
  note: string; amount: string; receivedAmount: string; items: ConsumablePurchaseItem[];
};
export type ConsumablePurchaseDetail = ConsumablePurchaseRow & {
  receipts: Array<{ id: number; number: string; receivedOn: string; note: string; createdBy: string; location: string;
    items: Array<{ consumableId: number; name: string; quantity: string; unit: string }> }>;
};
export type ConsumablePurchaseSource = { id: number; orderNo: string; supplierName: string };

export const dashboardApi = {
  salesTrend: (days = 30, start?: string, end?: string) =>
    jsonFetch<TrendPoint[]>(`/api/v1/dashboard/sales-trend?days=${days}${start ? `&start=${start}` : ""}${end ? `&end=${end}` : ""}`),
  platformRanking: (start?: string, end?: string) =>
    jsonFetch<PlatformRow[]>(`/api/v1/dashboard/platform-ranking${start ? `?start=${start}${end ? `&end=${end}` : ""}` : end ? `?end=${end}` : ""}`),
  skuRanking: (limit = 20, start?: string, end?: string) =>
    jsonFetch<SkuRow[]>(`/api/v1/dashboard/sku-ranking?limit=${limit}${start ? `&start=${start}` : ""}${end ? `&end=${end}` : ""}`),
  inventory: () => jsonFetch<InventorySummary>("/api/v1/dashboard/inventory"),
  inventorySkus: (search = "", limit = 1000) => {
    const q = new URLSearchParams({ search, limit: String(limit) });
    return jsonFetch<InventorySkuRow[]>(`/api/v1/dashboard/inventory/skus?${q}`);
  },
  products: (search = "", limit = 200) => {
    const q = new URLSearchParams({ search, limit: String(limit) });
    return jsonFetch<CatalogSkuRow[]>(`/api/v1/dashboard/products?${q}`);
  },
  updateCostPolicy: (skuId: number, costMode: "fixed" | "dynamic", costTolerancePct?: string) =>
    jsonFetch<{ id: number; costMode: string; costTolerancePct: string }>(`/api/v1/dashboard/products/${skuId}/cost-policy`, {
      method: "POST", body: JSON.stringify({ cost_mode: costMode, cost_tolerance_pct: costTolerancePct }),
    }),
  saveProduct: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number; skuCode: string; jackyunSkuId: string }>("/api/v1/dashboard/products/save", { method: "POST", body: JSON.stringify(body) }),
  catalogUnified: (kind: "all" | "goods" | "consumable" = "all", search = "", limit = 500) => {
    const q = new URLSearchParams({ kind, search, limit: String(limit) });
    return jsonFetch<UnifiedCatalogRow[]>(`/api/v1/dashboard/catalog-unified?${q}`);
  },
  bulkSetTaxCode: (items: { kind: "goods" | "consumable"; id: number }[], taxCode: string, overwrite: boolean) =>
    jsonFetch<{ updated: number; skipped: number; missing: number }>("/api/v1/dashboard/catalog/tax-code/bulk", {
      method: "POST", body: JSON.stringify({ items, tax_code: taxCode, overwrite }),
    }),
  orders: (status?: string) =>
    jsonFetch<SalesOrderRow[]>(`/api/v1/dashboard/orders${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  aftersales: () => jsonFetch<AftersaleRow[]>("/api/v1/dashboard/aftersales"),
};

export const consumablesApi = {
  list: (search = "") => jsonFetch<ConsumableRow[]>(`/api/v1/consumables?search=${encodeURIComponent(search)}`),
  save: (body: Record<string, unknown>) => jsonFetch<ConsumableRow>("/api/v1/consumables", { method: "POST", body: JSON.stringify(body) }),
  importXlsx: async (file: File) => {
    const form = new FormData(); form.append("file", file);
    const res = await authenticatedFetch("/api/v1/consumables/import-xlsx", { method: "POST", body: form });
    if (!res.ok) { const data = await res.json().catch(() => ({})) as { detail?: unknown }; throw new Error(detailToMessage(data.detail, `导入失败（${res.status}）`)); }
    return res.json() as Promise<{ ok: boolean; created: number; updated: number; skipped: number; mappings: number }>;
  },
  transactions: (id: number) => jsonFetch<ConsumableTransactionRow[]>(`/api/v1/consumables/${id}/transactions`),
  addTransaction: (id: number, body: Record<string, unknown>) => jsonFetch<{ id: number }>(`/api/v1/consumables/${id}/transactions`, { method: "POST", body: JSON.stringify(body) }),
  mappings: (consumableId?: number) => jsonFetch<ConsumableMappingRow[]>(`/api/v1/consumables/mappings/list${consumableId ? `?consumable_id=${consumableId}` : ""}`),
  saveMapping: (body: Record<string, unknown>) => jsonFetch<{ id: number }>("/api/v1/consumables/mappings", { method: "POST", body: JSON.stringify(body) }),
  deleteMapping: (id: number) => jsonFetch<{ ok: boolean }>(`/api/v1/consumables/mappings/${id}`, { method: "DELETE" }),
  purchases: (search = "", sourceOrderId?: number, orderNo?: string) => jsonFetch<ConsumablePurchaseRow[]>(`/api/v1/consumables/purchases?search=${encodeURIComponent(search)}${sourceOrderId ? `&source_order_id=${sourceOrderId}` : ""}${orderNo ? `&order_no=${encodeURIComponent(orderNo)}` : ""}`),
  purchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}`),
  purchaseSources: (search = "") => jsonFetch<ConsumablePurchaseSource[]>(`/api/v1/consumables/purchases/source-orders?search=${encodeURIComponent(search)}`),
  createPurchase: (body: Record<string, unknown>) => jsonFetch<ConsumablePurchaseDetail>("/api/v1/consumables/purchases", { method: "POST", body: JSON.stringify(body) }),
  receivePurchase: (id: number, body: Record<string, unknown>) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/receipts`, { method: "POST", body: JSON.stringify(body) }),
  cancelPurchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/cancel`, { method: "POST" }),
  reopenPurchase: (id: number) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}/reopen`, { method: "POST" }),
  updatePurchase: (id: number, body: Record<string, unknown>) => jsonFetch<ConsumablePurchaseDetail>(`/api/v1/consumables/purchases/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deletePurchase: (id: number) => jsonFetch<{ ok: boolean; purchaseId: number }>(`/api/v1/consumables/purchases/${id}`, { method: "DELETE" }),
};

// ---------- 期初初始化（Phase 6） ----------

export type OpeningRow = {
  id: number; kind: string; ref: string; amount: string | null; quantity: string | null;
  asOfDate: string | null; note: string; createdAt: string | null;
};
export type OpeningData = {
  summary: { byKind: Record<string, string>; adjustmentCount: number; differencePool: string; skuWithCost: number };
  items: OpeningRow[];
};

export const openingApi = {
  list: () => jsonFetch<OpeningData>("/api/v1/opening"),
  upsert: (body: Record<string, unknown>) =>
    jsonFetch<{ id: number }>("/api/v1/opening", { method: "POST", body: JSON.stringify(body) }),
  remove: (id: number) => jsonFetch<{ ok: boolean }>(`/api/v1/opening/${id}`, { method: "DELETE" }),
  adjust: (id: number, delta: string, reason: string) =>
    jsonFetch<{ id: number; delta: string }>(`/api/v1/opening/${id}/adjust`, {
      method: "POST",
      body: JSON.stringify({ delta, reason }),
    }),
};

// ---------- 月结快照（Phase 6） ----------

export type ClosingRow = {
  id: number; periodId: number; version: number; isCurrent: boolean; period: string;
  calculatedAt: string | null; grossProfit: string | null;
  receivable: string | null; received: string | null; createdAt: string | null;
};

export const closingApi = {
  versions: (year?: number, month?: number) => {
    const q = new URLSearchParams();
    if (year) q.set("year", String(year));
    if (month) q.set("month", String(month));
    return jsonFetch<ClosingRow[]>(`/api/v1/closing/versions${q.toString() ? `?${q}` : ""}`);
  },
  snapshot: (year: number, month: number) =>
    jsonFetch<{ id: number; version: number; period: string; note?: string }>("/api/v1/closing/snapshot", {
      method: "POST",
      body: JSON.stringify({ year, month }),
    }),
  recalc: (year: number, month: number) =>
    jsonFetch<{ id: number; version: number; period: string; note: string }>("/api/v1/closing/recalc", {
      method: "POST",
      body: JSON.stringify({ year, month }),
    }),
};

// ---------- 自动化（Phase 6） ----------

export type ScheduleItem = { task: string; args: string; label: string; frequency: string };
export type SyncJobRow = {
  id: number; provider: string; jobType: string; status: string;
  startedAt: string | null; finishedAt: string | null;
  stats: Record<string, unknown>; errorSummary: string;
};
export type SyncLogRow = {
  id: number; provider: string; level: string; message: string; jobId: number | null;
};

export const automationApi = {
  schedule: () => jsonFetch<{ items: ScheduleItem[]; note: string }>("/api/v1/automation/schedule"),
  jobs: (limit = 50) => jsonFetch<SyncJobRow[]>(`/api/v1/automation/jobs?limit=${limit}`),
  logs: (limit = 100) => jsonFetch<SyncLogRow[]>(`/api/v1/automation/logs?limit=${limit}`),
  runJackyun: (jobType: string) =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>(`/api/v1/automation/run/jackyun/${encodeURIComponent(jobType)}`, { method: "POST" }),
  runJkyOrders: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>("/api/v1/jky-orders/sync", { method: "POST" }),
  run1688: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>("/api/v1/automation/run/1688", { method: "POST" }),
};

export type JkyOrderChannel = {
  provider: string;
  label: string;
  priority: number | null;
  configured: boolean;
  status: string;
  verified: boolean;
  lastTestedAt: string | null;
  lastSuccessAt: string | null;
  errorSummary: string | null;
};

export type JkyOrderStatus = {
  providerPriority: string[];
  priorityWarnings: string[];
  channels: JkyOrderChannel[];
  lastRun: {
    id: number | null;
    status: string | null;
    startedAt: string | null;
    finishedAt: string | null;
    stats: Record<string, unknown>;
    errorSummary: string;
  };
  checkpoint: Record<string, unknown>;
};

export const jkyOrderApi = {
  status: () => jsonFetch<JkyOrderStatus>("/api/v1/jky-orders/status"),
  sync: () => automationApi.runJkyOrders(),
};

// ---------- 吉客云客户端文件导入 ----------

export type JackyunFileImportRow = {
  id: number;
  originalName: string;
  size: number;
  sha256: string;
  reportType: string;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  sheetName: string;
  headers: string[];
  rowCount: number;
  stagedRowCount: number;
  errorSummary: string;
  uploader: string;
  parsedAt: string | null;
  createdAt: string | null;
  /** 确认时后端按表头自动映射的结果（采购单 / 入库申请单货品 / 无匹配则仅存档）。 */
  mapResult?: {
    ok: boolean;
    mapper?: string;
    skipped?: boolean;
    reason?: string;
    documents?: number;
    matched?: number;
    matchedItems?: number;
    filledFields?: number;
    sourceRows?: number;
    relationshipRows?: number;
    uniqueRelationships?: number;
    createdLinks?: number;
    alreadyLinked?: number;
    pendingLinks?: number;
    rejectedLinks?: number;
    createdExternalOrders?: number;
    externalOrderNos?: string[];
    allocSeeded?: number;
    missingRk?: string[];
    mapped?: number;
    updated?: number;
    error?: string;
  };
  /** 采购报表确认时后端自动映射为业务采购单的结果（其他类型无此字段）。 */
  mapPurchase?: {
    ok: boolean;
    detailReport?: boolean;
    groups?: number;
    mapped?: number;
    updated?: number;
    skipped?: { row: number; reason: string }[];
    error?: string;
  };
};

export const jackyunFileApi = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<JackyunFileImportRow[]>(`/api/v1/jackyun-files/imports${q}`);
  },
  upload: async (file: File, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = `?auto_confirm=${autoConfirm ? "true" : "false"}`;
    const res = await authenticatedFetch(`/api/v1/jackyun-files/imports${query}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{ duplicate: boolean; lifecycle: string; import: JackyunFileImportRow }>;
  },
  confirm: (id: number) =>
    jsonFetch<JackyunFileImportRow>(`/api/v1/jackyun-files/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/jackyun-files/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<JackyunFileImportRow>(`/api/v1/jackyun-files/imports/${id}/restore`, { method: "POST" }),
  records: (id: number) =>
    jsonFetch<JackyunRecordPreview[]>(`/api/v1/jackyun-files/imports/${id}/records`),
  deleteRow: (id: number, rowIndex: number) =>
    noContentFetch(`/api/v1/jackyun-files/imports/${id}/records/${rowIndex}`),
  restoreRow: (id: number, rowIndex: number) =>
    jsonFetch<{ rowIndex: number; rowStatus: string }>(
      `/api/v1/jackyun-files/imports/${id}/records/${rowIndex}/restore`,
      { method: "POST" }
    ),
};

// ---------- 其他渠道采购订单主档 ----------

export type ExternalPurchaseOrderRow = {
  id: number;
  externalOrderId: string;
  platform: "pdd" | "taobao" | "other" | "1688" | string;
  buyerAccount: string;
  supplierName: string;
  title: string;
  orderAmount: string | null;
  paidAmount: string | null;
  orderedAt: string | null;
  orderStatus: string;
  purchaseStatus: string;
  unallocated: string;
  balanced: boolean;
  source: string;
  sourceImportId: number | null;
};

export const externalPurchaseOrderApi = {
  list: (query = "") => {
    const qs = new URLSearchParams({ platform: "non_1688" });
    if (query.trim()) qs.set("q", query.trim());
    return jsonFetch<ExternalPurchaseOrderRow[]>(`/api/v1/purchase/orders?${qs}`);
  },
  create: (body: {
    external_order_id: string;
    platform: string;
    supplier_name: string;
    title?: string;
    ordered_at?: string | null;
    order_amount?: string | null;
    paid_amount?: string | null;
  }) => jsonFetch<{ id: number; workbenchOrderId: number; externalOrderId: string }>(
    "/api/v1/purchase/orders",
    { method: "POST", body: JSON.stringify(body) }
  ),
  update: (id: number, body: {
    platform?: string;
    supplier_name?: string;
    title?: string;
    ordered_at?: string | null;
    order_amount?: string;
    paid_amount?: string;
  }) => jsonFetch<{ ok: boolean; id: number; externalOrderId: string; platform: string }>(
    `/api/v1/purchase/orders/${id}`,
    { method: "PATCH", body: JSON.stringify(body) }
  ),
};

export type JackyunRecordPreview = {
  rowIndex: number;
  rowStatus: string;
  payload: Record<string, string>;
};

// ---------- 1688 订单导入 ----------

export type Alibaba1688ImportRow = {
  id: number;
  fileName: string;
  fileHash: string;
  fileSize: number;
  orderCount: number;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  errorMessage: string | null;
  uploader: string;
  createdAt: string | null;
};

export const alibaba1688Api = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<Alibaba1688ImportRow[]>(`/api/v1/alibaba1688-imports/imports${q}`);
  },
  upload: async (file: File, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = `?auto_confirm=${autoConfirm ? "true" : "false"}`;
    const res = await authenticatedFetch(`/api/v1/alibaba1688-imports/upload${query}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{
      duplicate: boolean;
      lifecycle: string;
      import: Alibaba1688ImportRow;
      message?: string;
      import_id?: number;
      order_count?: number;
    }>;
  },
  confirm: (id: number) =>
    jsonFetch<Alibaba1688ImportRow>(`/api/v1/alibaba1688-imports/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/alibaba1688-imports/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<Alibaba1688ImportRow>(`/api/v1/alibaba1688-imports/imports/${id}/restore`, { method: "POST" }),
  orders: (id: number) =>
    jsonFetch<Alibaba1688OrderPreview[]>(`/api/v1/alibaba1688-imports/imports/${id}/orders`),
  deleteRow: (id: number, orderId: number) =>
    noContentFetch(`/api/v1/alibaba1688-imports/imports/${id}/orders/${orderId}`),
  restoreRow: (id: number, orderId: number) =>
    jsonFetch<Alibaba1688OrderPreview>(
      `/api/v1/alibaba1688-imports/imports/${id}/orders/${orderId}/restore`,
      { method: "POST" }
    ),
};

// ---------- 1688 浏览器直采通道 ----------

export type Alibaba1688BrowserStatus = {
  enabled: boolean;
  profileDir: string;
  headless: boolean;
  status: string;
  account: string | null;
  lastSyncAt: string | null;
  lastSyncSummary: string | null;
  lastLoginCheckAt: string | null;
  errorSummary: string | null;
  lastSyncJobId: number | null;
  maxPages: number;
  stopAfterKnown: number;
  lookbackDays: number;
};

export type Alibaba1688BrowserJob = {
  id: number;
  jobType: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  stats: Record<string, unknown>;
  errorSummary: string;
};

export const alibaba1688BrowserApi = {
  status: () => jsonFetch<Alibaba1688BrowserStatus>("/api/v1/alibaba1688-browser/status"),
  login: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string; message: string }>(
      "/api/v1/alibaba1688-browser/login",
      { method: "POST" }
    ),
  sync: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string }>(
      "/api/v1/alibaba1688-browser/sync",
      { method: "POST" }
    ),
  jobs: (limit = 10) =>
    jsonFetch<Alibaba1688BrowserJob[]>(`/api/v1/alibaba1688-browser/jobs?limit=${limit}`),
};

// ---------- 吉客云 Web 连接器（V1 主通道：网页登录态直读） ----------

export type JkyWebStatus = {
  adapter: string;
  status: string;
  sessionUpdatedAt: string | null;
  lastSyncAt: string | null;
  lastSyncStatus: string | null;
  lastSyncStats: Record<string, unknown>;
  errorSummary: string;
  counts: {
    salesOrders: number;
    salesItems: number;
    stockinOrders: number;
    stockinItems: number;
    totalStockSkus: number;
    warehouseStockRows: number;
  };
};

export type JkyWebJob = {
  id: number;
  jobType: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  stats: Record<string, unknown>;
  errorSummary: string;
};

export const jkyWebApi = {
  status: () => jsonFetch<JkyWebStatus>("/api/v1/jky-web/status"),
  updateSession: (curl: string) =>
    jsonFetch<{ ok: boolean; endpoints: string[]; hasCommonVerify: boolean }>(
      "/api/v1/jky-web/session",
      { method: "POST", body: JSON.stringify({ curl }) }
    ),
  sync: () =>
    jsonFetch<{ ok: boolean; taskId: string; status: string; message: string }>(
      "/api/v1/jky-web/sync",
      { method: "POST" }
    ),
  jobs: (limit = 10) => jsonFetch<JkyWebJob[]>(`/api/v1/jky-web/jobs?limit=${limit}`),
};

export type Alibaba1688OrderPreview = {
  id: number;
  rowStatus: string;
  externalOrderId: string;
  buyerCompanyName: string;
  buyerMemberName: string;
  sellerCompanyName: string;
  sellerMemberName: string;
  goodsTotal: string;
  freight: string;
  discount: string;
  actualPayment: string;
  orderStatus: string;
  orderRemark: string;
  orderTime: string | null;
  payTime: string | null;
};

// ---------- 税务系统官方发票清单 ----------

export type TaxInvoiceImportRow = {
  id: number;
  originalName: string;
  size: number;
  sha256: string;
  sourceSystem: string;
  period: string;
  sheetName: string;
  headers: string[];
  mapping: Record<string, string>;
  status: string;
  lifecycle: string;
  lifecycleChangedAt: string | null;
  rowCount: number;
  recognizedRowCount: number;
  matchedRowCount: number;
  needsReviewCount: number;
  errorSummary: string;
  uploader: string;
  importedAt: string | null;
  createdAt: string | null;
};

export type TaxInvoiceRow = {
  id: number;
  direction: "input" | "output" | "unknown";
  invoiceCode: string;
  invoiceNumber: string;
  invoiceType: string;
  status: "issued" | "void" | "red" | "unknown";
  issueDate: string | null;
  sellerName: string;
  sellerTaxId: string;
  buyerName: string;
  buyerTaxId: string;
  amountExclTax: string | null;
  taxAmount: string | null;
  totalAmount: string | null;
  currency: string;
  matchStatus: "matched" | "unmatched" | "needs_review";
  matchNote: string;
  sourceImportId: number | null;
  sourceRowIndex: number | null;
  /** 进项发票是否已勾选认证（税务侧抵扣） */
  verified: boolean;
  /** 认证所属月份，如 2026-08 */
  verifiedMonth: string;
};

export type TaxInvoiceSummary = {
  total: number;
  byDirection: Record<string, number>;
  byStatus: Record<string, number>;
  byMatchStatus: Record<string, number>;
};

export const taxInvoiceApi = {
  imports: (lifecycle?: string) => {
    const q = lifecycle ? `?lifecycle=${encodeURIComponent(lifecycle)}` : "";
    return jsonFetch<TaxInvoiceImportRow[]>(`/api/v1/tax-invoices/imports${q}`);
  },
  summary: () => jsonFetch<TaxInvoiceSummary>("/api/v1/tax-invoices/summary"),
  invoices: (params: { direction?: string; status?: string; matchStatus?: string; verified?: boolean } = {}) => {
    const q = new URLSearchParams();
    if (params.direction) q.set("direction", params.direction);
    if (params.status) q.set("status", params.status);
    if (params.matchStatus) q.set("match_status", params.matchStatus);
    if (params.verified !== undefined) q.set("verified", String(params.verified));
    return jsonFetch<TaxInvoiceRow[]>(`/api/v1/tax-invoices${q.toString() ? `?${q}` : ""}`);
  },
  bulkVerify: (invoiceIds: number[], verified: boolean, verifiedMonth: string) =>
    jsonFetch<{ ok: boolean; processed: number; items: unknown[] }>("/api/v1/tax-invoices/bulk-verify", {
      method: "POST",
      body: JSON.stringify({ invoice_ids: invoiceIds, verified, verified_month: verifiedMonth }),
    }),
  upload: async (file: File, period?: string, autoConfirm = false) => {
    const form = new FormData();
    form.append("file", file);
    const query = new URLSearchParams();
    if (period) {
      const [year, month] = period.split("-");
      if (year) query.set("period_year", year);
      if (month) query.set("period_month", String(Number(month)));
    }
    query.set("auto_confirm", autoConfirm ? "true" : "false");
    const res = await authenticatedFetch(`/api/v1/tax-invoices/imports${query.toString() ? `?${query}` : ""}`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
      throw new Error(detailToMessage(body.detail, `上传失败（${res.status}）`));
    }
    return res.json() as Promise<{ duplicate: boolean; lifecycle: string; import: TaxInvoiceImportRow }>;
  },
  confirm: (id: number) =>
    jsonFetch<TaxInvoiceImportRow>(`/api/v1/tax-invoices/imports/${id}/confirm`, { method: "POST" }),
  softDelete: (id: number) => noContentFetch(`/api/v1/tax-invoices/imports/${id}`),
  restore: (id: number) =>
    jsonFetch<TaxInvoiceImportRow>(`/api/v1/tax-invoices/imports/${id}/restore`, { method: "POST" }),
  records: (id: number) =>
    jsonFetch<TaxInvoiceRecordPreview[]>(`/api/v1/tax-invoices/imports/${id}/records`),
  deleteRow: (id: number, rowIndex: number) =>
    noContentFetch(`/api/v1/tax-invoices/imports/${id}/records/${rowIndex}`),
  restoreRow: (id: number, rowIndex: number) =>
    jsonFetch<{ rowIndex: number; rowStatus: string }>(
      `/api/v1/tax-invoices/imports/${id}/records/${rowIndex}/restore`,
      { method: "POST" }
    ),
};

export type TaxInvoiceRecordPreview = {
  rowIndex: number;
  rowStatus: string;
  recognitionStatus: string;
  invoiceId: number | null;
  errorSummary: string;
  payload: Record<string, string>;
};

// ---------- 采购全链路（1688订单 → SKU → 吉客云采购单 → 入库 → 发票 → 付款 → 认证） ----------

/** 环节数据归属维度：1688 导出 / 本平台采购中心 / 吉客云 / 税务 */
export type ChainDimension = "1688" | "purchase" | "jackyun" | "tax";

/** 漏斗统计里的单个环节 */
export type ChainStageStat = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: ChainDimension;
  count: number;
  pct: number;
};

/** 单行订单里单个环节的完成状态 */
export type ChainStageState = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: ChainDimension;
  done: boolean;
  detail: string;
  amount: number | null;
};

export type ChainOverview = {
  total: number;
  stageTotal: number;
  stages: ChainStageStat[];
  refined: number;
  jackyunLinked: number;
  inbound: number;
  invoiced: number;
  paid: number;
  verified: number;
  pending: number;
};

export type ChainInbound = {
  linkId: number | null;
  targetId: number;
  goodsdocNo: string;
  date: string | null;
  warehouseName: string;
  supplier: string;
  amount: number | null;
  matchMethod: string;
  confidence: number | null;
  note: string;
  consumableUsageDecided: boolean;
  consumableUsageEnabled: boolean | null;
  consumableUsageItems: Array<{
    id: number;
    consumableId: number;
    consumableCode: string;
    consumableName: string;
    unit: string;
    quantity: string;
    note: string;
  }>;
};

export type ChainSettlement = {
  linkId: number;
  targetId: number;
  settlementNo: string;
  date: string | null;
  amount: number | null;
  paidAmount: number | null;
  paid: boolean;
  status: string;
  matchMethod: string;
  confidence: number | null;
  note: string;
};

export type ChainInvoice = {
  invoiceId: number;
  invoiceNo: string;
  amount: number | null;
  issueDate: string | null;
  verified: boolean;
  verifiedMonth: string;
  confirmed: boolean;
};

export type ChainAllocation = {
  id: number;
  skuId: number | null;
  skuCode: string;
  goodsName: string;
  quantity: number | null;
  unitPrice: number | null;
  amount: number | null;
  note: string;
};

export type ChainExpense = {
  id: number;
  expenseType: string;
  amount: number | null;
  note: string;
};

export type ChainPurchaseOrder = {
  id: number;
  purchNo: string;
  supplierName: string;
  amount: number | null;
  status: string;
  linkId?: number;
  relationKind?: string; // ''=普通 / merged=合并 / split=拆分
  allocAmount?: number | null; // 分摊金额
};

export type ChainOrderRow = {
  orderId: number;
  fileOrderId: number | null;
  externalPoId: number | null;
  source: "file" | "workflow";
  orderNo: string;
  platform?: string;
  supplier: string;
  buyer: string;
  amount: number | null;
  paidAmount: number | null;
  title: string;
  orderStatus: string;
  orderDate: string | null;
  purchaseStatus: string;
  purchaseContentComplete: boolean;
  unallocatedAmount: number | null;
  allocations: ChainAllocation[];
  expenses: ChainExpense[];
  purchaseOrders: ChainPurchaseOrder[];
  inbound: ChainInbound[];
  settlement: ChainSettlement[];
  invoice: ChainInvoice[];
  verified: boolean;
  pendingCount: number;
  /** 7 环节完成状态，顺序即链路顺序 */
  stages: ChainStageState[];
  doneCount: number;
  stageTotal: number;
};

export type PendingLink = {
  kind: "chain" | "invoice";
  linkId: number;
  orderId: number;
  orderNo: string;
  supplier: string;
  targetId: number;
  targetType: string;
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  confidence: number | null;
  note: string;
};

/** 单订单详情内的待确认关联建议。 */
export type ChainSuggestion = {
  kind: "chain" | "settlement" | "invoice";
  linkId: number;
  targetId: number;
  targetType: string;
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  confidence: number | null;
  note: string;
};

export type ChainLinkItemDetail = {
  goodsName: string;
  spec: string;
  quantity: number | null;
  applyQuantity: number | null;
  remainQuantity: number | null;
  barcode: string;
  unitName: string;
  unitPriceTax: number | null;
  amountTax: number | null;
  skuId: number | null;
  matchStatus: string;
};

export type ChainLinkCandidate = {
  targetId: number;
  targetType: "inbound" | "settlement";
  targetNo: string;
  targetAmount: number | null;
  targetDate: string | null;
  targetSupplier: string;
  warehouseName: string;
  score: number;
  reason: string;
  requiredSkuCount: number;
  matchedSkuCount: number;
  skuOverlapRatio: number | null;
  linkId: number | null;
  currentlyLinked: boolean;
  pendingSuggestion: boolean;
  previouslyRejected: boolean;
  linkedOrderNos: string[];
  details: ChainLinkItemDetail[];
};

/** 多因子预关联执行结果。 */
export type PreLinkItem = {
  orderNo: string;
  orderId: number;
  goodsdocNo: string;
  targetId: number;
  score: string;
  reason: string;
  linkId: number;
};
export type PreLinkResult = {
  autoLinked: PreLinkItem[];
  pendingSuggested: PreLinkItem[];
  stats: {
    autoLinked: number;
    pendingSuggested: number;
    fullyLinkedOrders: number;
    noAllocOrders: number;
  };
  autoConfirmed?: {
    confirmedChain?: number;
    resolvedInboundUsage?: number;
    confirmedInvoices?: number;
    skippedOrphans?: number;
    allocSeeded?: number;
    allocSkipped?: number;
    seedError?: string;
  };
};

/** 1688↔RK 对照表预览/应用结果。 */
export type XrefPreview = {
  totalXrefRows: number;
  totalPairs: number;
  stats: {
    to_create: number;
    missing_order: number;
    missing_rk: number;
    already_linked: number;
    combo_skipped: number;
    existing_noise: number;
  };
  toCreate: Array<{
    order_no: string;
    rk_no: string;
    barcodes: string[];
    products: string[];
    qty_boxes: number;
    note_extra: string;
  }>;
  missingOrder: string[];
  missingRk: string[];
  noiseLinkCount: number;
};
export type XrefApplyResult = {
  created: number;
  total_xref_rows: number;
  total_pairs: number;
  missing_order: string[];
  missing_rk: string[];
  combo_skipped_count: number;
  already_linked_count: number;
  noise_links: Array<{ id: number; target_id: number; order_id: number | null }>;
};
export type XrefFile = {
  exists: boolean;
  content: string;
  modifiedAt: number | null;
  size: number;
};

/** 二级页面：单订单链路详情。 */
export type ChainOrderDetail = ChainOrderRow & {
  suggestions: ChainSuggestion[];
  supplierHistory?: ChainSupplierHistory;
};

/** 采购执行中心顶部统计卡。 */
export type ExecutionOverview = {
  totalOrders: number;
  pendingProcess: number;
  pendingRefine: number;
  pendingSku: number;
  pendingPo: number;
  pendingInvoice: number;
};

/** 常购 SKU（供应商维度）。 */
export type OftenSku = {
  skuCode: string;
  goodsName: string;
  count: number;
};

/** 供应商历史（订单详情右侧展示）。 */
export type ChainSupplierHistory = {
  orderCount: number;
  totalPurchase: number;
  uninvoiced: number;
  lastOrderDate: string | null;
  oftenSkus: OftenSku[];
  recentOrders: ChainOrderRow[];
};

/** 供应商聚合列表项。 */
export type SupplierSummary = ChainSupplierHistory & {
  supplierName: string;
  uninbound: number;
};

/** 平铺全量记录：每行一条单据，标记数据维度与环节。 */
export type ChainRecord = {
  recordId: string;
  dimension: "1688" | "jackyun" | "tax";
  stage: "order" | "inbound" | "settlement" | "invoice";
  no: string;
  counterparty: string;
  amount: number | null;
  date: string | null;
  status: string;
  statusTone: "ok" | "warn" | "info" | "muted";
  linkedOrderNos: string[];
  pendingCount: number;
  invoiceId: number | null;
  verified: boolean;
  verifiedMonth: string;
};

export type ChainRecordList = {
  total: number;
  items: ChainRecord[];
};

export const procurementChainApi = {
  overview: () => jsonFetch<ChainOverview>("/api/v1/procurement-chain/overview"),
  orders: (limit = 500) =>
    jsonFetch<{ total: number; items: ChainOrderRow[] }>(`/api/v1/procurement-chain/orders?limit=${limit}`),
  records: (limit = 2000) =>
    jsonFetch<ChainRecordList>(`/api/v1/procurement-chain/records?limit=${limit}`),
  pending: () => jsonFetch<{ items: PendingLink[] }>("/api/v1/procurement-chain/pending"),
  candidates: (orderId: number, targetType: "inbound" | "settlement", q = "", limit = 100) => {
    const sp = new URLSearchParams({ order_id: String(orderId), target_type: targetType, limit: String(limit) });
    if (q) sp.set("q", q);
    return jsonFetch<{ total: number; items: ChainLinkCandidate[] }>(`/api/v1/procurement-chain/candidates?${sp}`);
  },
  orderDetail: (orderId: number) =>
    jsonFetch<ChainOrderDetail>(`/api/v1/procurement-chain/orders/${orderId}`),
  executionOverview: () => jsonFetch<ExecutionOverview>("/api/v1/procurement-chain/execution-overview"),
  suppliers: (limit = 200) =>
    jsonFetch<{ total: number; items: SupplierSummary[] }>(`/api/v1/procurement-chain/suppliers?limit=${limit}`),
  supplierDetail: (supplierName: string) =>
    jsonFetch<SupplierSummary>(`/api/v1/procurement-chain/suppliers/${encodeURIComponent(supplierName)}`),
  runMatch: (autoConfirm = true) =>
    jsonFetch<{ created: number; skipped: number; requiresConfirmation: boolean; [key: string]: unknown }>(
      `/api/v1/procurement-chain/run-match${autoConfirm ? "" : "?auto_confirm=false"}`,
      { method: "POST" }
    ),
  autoConfirm: () =>
    jsonFetch<{
      confirmedChain: number;
      resolvedInboundUsage: number;
      confirmedInvoices: number;
      skippedOrphans: number;
      allocSeeded: number;
      allocSkipped: number;
      seedError: string;
    }>("/api/v1/procurement-chain/auto-confirm", { method: "POST" }),
  prelink: (auto = true) =>
    jsonFetch<PreLinkResult>("/api/v1/procurement-chain/prelink", {
      method: "POST",
      body: JSON.stringify({ auto }),
    }),
  xrefFile: () => jsonFetch<XrefFile>("/api/v1/procurement-chain/xref/file"),
  xrefPreview: (content: string) =>
    jsonFetch<XrefPreview>("/api/v1/procurement-chain/xref/preview", {
      method: "POST",
      body: JSON.stringify({ content, persist_to_file: false }),
    }),
  xrefApply: (content: string, persistToFile = true) =>
    jsonFetch<XrefApplyResult>("/api/v1/procurement-chain/xref/apply", {
      method: "POST",
      body: JSON.stringify({ content, persist_to_file: persistToFile }),
    }),
  confirmLink: (linkId: number) =>
    jsonFetch<{ id: number; confirmed: boolean }>(`/api/v1/procurement-chain/links/${linkId}/confirm`, { method: "POST" }),
  deleteLink: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-chain/links/${linkId}`, { method: "DELETE" }),
  confirmInvoiceLink: (linkId: number) =>
    jsonFetch<{ id: number; confirmed: boolean }>(`/api/v1/procurement-chain/invoice-links/${linkId}/confirm`, { method: "POST" }),
  deleteInvoiceLink: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-chain/invoice-links/${linkId}`, { method: "DELETE" }),
  verifyInvoice: (invoiceId: number, verified: boolean, verifiedMonth: string) =>
    jsonFetch<{ invoiceId: number; verified: boolean; verifiedMonth: string }>(
      `/api/v1/procurement-chain/invoices/${invoiceId}/verify`,
      { method: "POST", body: JSON.stringify({ verified, verified_month: verifiedMonth }) }
    ),
  manualLink: (orderId: number, targetType: string, targetId: number, note: string, consumableUsageEnabled?: boolean, consumableUsageItems?: Array<{ consumable_id: number; quantity: string }>) =>
    jsonFetch<{ id: number; confirmed: boolean }>("/api/v1/procurement-chain/links", {
      method: "POST",
      body: JSON.stringify({ order_id: orderId, target_type: targetType, target_id: targetId, note, consumable_usage_enabled: consumableUsageEnabled, consumable_usage_items: consumableUsageItems ?? [] }),
    }),
  autoLinkSettlement: (orderId: number) =>
    jsonFetch<{ linked: boolean; settlementNo?: string; reason?: string }>(`/api/v1/procurement-chain/orders/${orderId}/auto-link-settlement`, { method: "POST" }),
  manualInvoiceLink: (orderId: number, invoiceId: number, note: string) =>
    jsonFetch<{ id: number; confirmed: boolean }>("/api/v1/procurement-chain/invoice-links", {
      method: "POST",
      body: JSON.stringify({ order_id: orderId, invoice_id: invoiceId, note }),
    }),
  replaceLink: (linkId: number, targetId: number, note: string, consumableUsageEnabled?: boolean, consumableUsageItems?: Array<{ consumable_id: number; quantity: string }>) =>
    jsonFetch<{ id: number; confirmed: boolean; targetId: number }>(`/api/v1/procurement-chain/links/${linkId}`, {
      method: "PUT",
      body: JSON.stringify({ target_id: targetId, note, consumable_usage_enabled: consumableUsageEnabled, consumable_usage_items: consumableUsageItems ?? [] }),
    }),
  setInboundConsumableUsage: (linkId: number, enabled: boolean, items: Array<{ consumable_id: number; quantity: string }>, note = "") =>
    jsonFetch<{ linkId: number; decided: boolean; enabled: boolean }>(`/api/v1/procurement-chain/links/${linkId}/consumable-usage`, {
      method: "POST",
      body: JSON.stringify({ enabled, items, note }),
    }),
  correctInboundAmount: (documentId: number, amount: string, note = "") =>
    jsonFetch<{ ok: boolean; documentId: number; amount: string }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/amount`, {
      method: "PATCH",
      body: JSON.stringify({ amount, note }),
    }),
  recalcInboundAmount: (documentId: number) =>
    jsonFetch<{ ok: boolean; before: string | null; after: string }>(`/api/v1/procurement-chain/inbound-documents/${documentId}/recalc-amount`, {
      method: "POST",
    }),
};

// ---------- 采购执行中心（5 步骤任务视角） ----------

export type WorkbenchDimension = "1688" | "purchase" | "jackyun" | "tax";

export type WorkbenchStep = {
  key: string;
  no: number;
  label: string;
  short: string;
  dimension: WorkbenchDimension;
  href: string;
  act: string;
  count?: number;
  pct?: number;
};

export type WorkbenchStepState = {
  done: boolean;
  detail: string;
  amount: number | null;
};

export type WorkbenchFunnel = {
  total: number;
  stepTotal: number;
  steps: (WorkbenchStep & { count: number; pct: number })[];
};

export type WorkbenchTodo = {
  total: number;
  pending: number;
  items: (WorkbenchStep & { count: number })[];
};

export type WorkbenchSummary = {
  totalOrders: number;
  newOrders: number;
  pendingSku: number;
  pendingPo: number;
  pendingInbound: number;
  pendingInvoice: number;
  exceptionCount: number;
};

export type WorkbenchOrderItem = {
  orderId: number;
  externalPoId: number | null;
  orderNo: string;
  /** 采购渠道：1688 / pdd（拼多多）/ taobao（淘宝）。后端暂未下发时兜底为 1688。 */
  platform?: string;
  /** 订单类型：goods=正常货品 / consumable=耗材（包材）采购 */
  orderKind?: "goods" | "consumable";
  /** 人工覆盖的类型（goods/consumable/空=自动判定） */
  orderKindOverride?: string;
  supplier: string;
  amount: number | null;
  freight: number | null;
  orderDate: string | null;
  orderStatus: string;
  purchaseStatus: string;
  hasException: boolean;
  firstUndone: string | null;
  firstUndoneLabel: string;
  firstUndoneShort: string;
  firstUndoneDimension: WorkbenchDimension;
  stepStates: Record<string, WorkbenchStepState>;
  inboundDone: boolean;
  invoiceDone: boolean;
  /** 收尾环节卡点：awaiting_inbound / awaiting_invoice / awaiting_payment / awaiting_verification / done / open */
  closeoutStage?: string | null;
  /** 供应商待开发票：pending=完全未开票 / partial=部分 / done=已开够 / none=无实付可比 */
  invoiceStatus?: "pending" | "partial" | "done" | "none" | string;
  /** 已收票金额（关联分摊额优先，未分摊用票面全额） */
  invoicedAmount?: number | null;
  /** 未开票金额 = 实付 - 已收票（负数归 0） */
  invoiceOutstanding?: number | null;
};

export type WorkbenchOrderGroup = { label: string; items: WorkbenchOrderItem[] };

export type WorkbenchOrderList = {
  total: number;
  page: number;
  pageSize: number;
  /** 当前筛选结果合计未开票金额（催票总额） */
  invoiceOutstandingTotal?: number;
  groups: WorkbenchOrderGroup[];
};

export type WorkbenchOrder = {
  orderId: number;
  fileOrderId: number | null;
  externalPoId: number | null;
  orderNo: string;
  /** 采购渠道：1688 / pdd（拼多多）/ taobao（淘宝）。后端暂未下发时兜底为 1688。 */
  platform?: string;
  /** 订单类型：goods=正常货品 / consumable=耗材（包材）采购 */
  orderKind?: "goods" | "consumable";
  /** 人工覆盖的类型（goods/consumable/空=自动判定） */
  orderKindOverride?: string;
  supplier: string | null;
  buyer: string | null;
  amount: number | null;
  goodsTotal: number | null;
  freight: number | null;
  discount: number | null;
  paidAmount: number | null;
  /** 1688 微调金额：红包等导致开票金额与订单实付的零头差；平衡目标 = 实付 + 微调 */
  adjustmentAmount?: number | null;
  adjustmentNote?: string;
  orderDate: string | null;
  orderStatus: string | null;
  purchaseStatus: string;
  /** 采购单步骤按 Excel 口径跳过（吉客云未建采购单、入库闭环即放行） */
  jackyunPoBypassed?: boolean;
  title: string | null;
  hasException: boolean;
};

export type WorkbenchDetail = {
  order: WorkbenchOrder;
  stepStates: Record<string, WorkbenchStepState>;
  detail: {
    allocations: unknown[];
    expenses: unknown[];
    purchaseOrders: unknown[];
    poAmountClosure?: { relevant: boolean; allocTotal: number | null; gap: number | null; closed: boolean } | null;
    inbound: unknown[];
    invoice: unknown[];
    settlement: unknown[];
    unallocatedAmount: number | null;
  };
  stepTotal: number;
  supplierHistory: {
    orderCount: number;
    totalPurchase: number;
    uninvoiced: number;
    lastOrderDate: string | null;
    oftenSkus: { skuCode: string; goodsName: string; count: number }[];
    recentOrders: WorkbenchOrder[];
  } | null;
};

export type WorkbenchSupplierSummary = {
  supplierName: string;
  orderCount: number;
  totalPurchase: number;
  uninvoiced: number;
  uninbound: number;
  lastOrderDate: string | null;
  oftenSkus: { skuCode: string; goodsName: string; count: number }[];
};

export type WorkbenchSupplierDetail = WorkbenchSupplierSummary & {
  recentOrders: WorkbenchOrder[];
};

export type InvoiceReconciliation = {
  tolerance: number; skippedZeroOrders: number;
  suppliers: Array<{
    supplier: string; supplierNorm: string; hasOrders: boolean;
    orderCount: number; orderTotal: number; invoiceCount: number; invoiceTotal: number;
    matchedTotal: number; remainingOrders: number; remainingOrderTotal: number;
    pendingOrders: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
      orderAmount: number; remaining: number; partial: boolean }>;
    orders: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
      orderAmount: number; remaining: number }>;
    months: Array<{ month: string; invoices: Array<{
      invoiceId: number; invoiceNo: string; issueDate: string | null; seller: string;
      amount: number; coveredTotal: number; diff: number; status: "matched" | "short";
      manualLinked: boolean;
      covered: Array<{ orderId: number; orderNo: string; platform: string; date: string | null;
        orderAmount: number; consumed: number; partial: boolean;
        source: "manual" | "auto"; linkId?: number }>;
    }> }>;
  }>;
  expenseSellers: Array<{ seller: string; invoiceCount: number; invoiceTotal: number }>;
};

export const procurementWorkbenchApi = {
  funnel: () => jsonFetch<WorkbenchFunnel>("/api/v1/procurement-workbench/funnel"),
  todos: () => jsonFetch<WorkbenchTodo>("/api/v1/procurement-workbench/todos"),
  summary: () => jsonFetch<WorkbenchSummary>("/api/v1/procurement-workbench/summary"),
  orders: (
    params: {
      status?: "all" | "pending" | "done" | "order" | "content" | "sku" | "jackyun_po" | "closeout" | "refine" | "po" | "inbound" | "invoice" | "exception";
      q?: string;
      sortBy?: "date" | "amount" | "status";
      page?: number;
      pageSize?: number;
      startDate?: string;
      endDate?: string;
    } = {}
  ) => {
    const sp = new URLSearchParams();
    if (params.status && params.status !== "all") sp.set("status", params.status);
    if (params.q) sp.set("q", params.q);
    if (params.sortBy && params.sortBy !== "date") sp.set("sort_by", params.sortBy);
    if (params.page) sp.set("page", String(params.page));
    if (params.pageSize) sp.set("page_size", String(params.pageSize));
    if (params.startDate) sp.set("start_date", params.startDate);
    if (params.endDate) sp.set("end_date", params.endDate);
    const qs = sp.toString();
    return jsonFetch<WorkbenchOrderList>(`/api/v1/procurement-workbench/orders${qs ? "?" + qs : ""}`);
  },
  workbench: (orderId: number) =>
    jsonFetch<WorkbenchDetail>(`/api/v1/procurement-workbench/orders/${orderId}/workbench`),
  softDeleteOrder: (orderId: number) =>
    jsonFetch<{
      ok: boolean;
      orderId: number;
      orderNo: string;
      removedPoIds?: number[];
      recoverable?: boolean;
    }>(`/api/v1/procurement-workbench/orders/${orderId}/soft-delete`, { method: "POST" }),
  /** 编辑订单主档（供应商/标题/金额等）：1688 单改源单事实并同步副本，独有单改副本；后端留审计 */
  editOrderMain: (
    orderId: number,
    body: Partial<{
      supplier_name: string;
      title: string;
      ordered_at: string;
      goods_total: string;
      freight: string;
      discount: string;
      actual_payment: string;
      order_amount: string;
      paid_amount: string;
      platform: string;
    }>
  ) =>
    jsonFetch<{ ok: boolean; mode: "file" | "external"; orderNo: string }>(
      `/api/v1/procurement-workbench/orders/${orderId}/main-fields`,
      { method: "PATCH", body: JSON.stringify(body) }
    ),
  /** 人工覆盖订单类型（正品/耗材）：goods / consumable / 空串=恢复自动判定 */
  setOrderKindOverride: (orderId: number, kind: "goods" | "consumable" | "") =>
    jsonFetch<{ orderId: number; orderKindOverride: string }>(
      `/api/v1/procurement-workbench/orders/${orderId}/kind-override`,
      { method: "PATCH", body: JSON.stringify({ kind }) }
    ),
  /** 发票维度对账：进项发票按供应商 FIFO 顺序配平采购订单（纯推导） */
  invoiceReconciliation: (supplier?: string) =>
    jsonFetch<import("./api").InvoiceReconciliation>(
      `/api/v1/procurement-workbench/invoice-reconciliation${supplier ? `?supplier=${encodeURIComponent(supplier)}` : ""}`
    ),
  /** 供应商画像手工微调：把采购订单挂到进项发票（manual 关联，落库） */
  createInvoiceMatch: (invoiceId: number, poId: number) =>
    jsonFetch<{ ok: boolean; id: number }>("/api/v1/procurement-workbench/invoice-match", {
      method: "POST",
      body: JSON.stringify({ invoice_id: invoiceId, po_id: poId }),
    }),
  /** 解除供应商画像里的手工发票匹配 */
  deleteInvoiceMatch: (linkId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/procurement-workbench/invoice-match/${linkId}`, { method: "DELETE" }),
  suppliers: (limit = 200) =>
    jsonFetch<{ total: number; items: WorkbenchSupplierSummary[] }>(
      `/api/v1/procurement-workbench/suppliers?limit=${limit}`
    ),
  supplierDetail: (supplierName: string) =>
    jsonFetch<WorkbenchSupplierDetail>(
      `/api/v1/procurement-workbench/suppliers/${encodeURIComponent(supplierName)}`
    ),
  /** 供应商改名/归一：该供应商全部订单统一改为新名称，同名自动合并（双副本同步+审计） */
  renameSupplier: (oldName: string, newName: string) =>
    jsonFetch<{ ok: boolean; oldName: string; newName: string; renamedOrders: number; renamedFileOrders: number; merged: boolean }>(
      "/api/v1/procurement-workbench/suppliers/rename",
      { method: "POST", body: JSON.stringify({ old_name: oldName, new_name: newName }) }
    ),
};

// ---------- 采购工作台（截图版）----------

export type BoardOverview = {
  todayNewOrders: number;
  todayDelta: number;
  pendingRefine: number;
  pendingPo: number;
  pendingInbound: number;
  pendingInvoice: number;
  paidRate: number;
  paidAmount: number;
  totalAmount: number;
};

export type BoardStepProgress = { label: string; done: boolean };

export type BoardOrderItem = {
  orderId: number;
  orderNo: string;
  supplier: string;
  amount: number | null;
  orderDate: string | null;
  orderStatus: string;
  firstUndone: string | null;
  firstUndoneLabel: string;
  stepProgress: BoardStepProgress[];
};

export type BoardOrderGroup = { label: string; items: BoardOrderItem[] };
export type BoardOrderList = {
  total: number;
  page: number;
  pageSize: number;
  groups: BoardOrderGroup[];
};

export type BoardFlowStep = { no: number; label: string; done: boolean; detail: string };

export type BoardSupplierProfile = {
  supplierName: string;
  badge: string;
  orderCount30d: number;
  totalPurchase: number;
  lastOrderDate: string | null;
  lastRecentLabel: string;
};

export type BoardOftenSku = {
  skuCode: string;
  goodsName: string;
  purchaseCount: number;
  qty: number;
  unitPrice: number;
  amount: number;
};

export type BoardPaymentBreakdown = {
  goodsAmount: number;
  freight: number;
  extra: number;
  discount: number;
  totalDue: number;
  paidAmount: number;
  unpaidAmount: number;
  diffAmount: number;
};

export type BoardOrder = {
  orderId: number;
  orderNo: string;
  supplier: string | null;
  buyer: string | null;
  amount: number | null;
  orderDate: string | null;
  orderStatus: string | null;
  title: string | null;
  freight: number | null;
  discount: number | null;
  goodsTotal: number | null;
};

export type BoardOrderDetail = {
  order: BoardOrder;
  flowStatus: BoardFlowStep[];
  supplierProfile: BoardSupplierProfile | null;
  oftenSkus: BoardOftenSku[];
  allocations: ChainAllocation[];
  purchaseOrders: ChainPurchaseOrder[];
  inbound: ChainInbound[];
  invoice: ChainInvoice[];
  verified: boolean;
  paymentBreakdown: BoardPaymentBreakdown;
};

export const procurementBoardApi = {
  overview: () => jsonFetch<BoardOverview>("/api/v1/procurement-board/overview"),
  orders: (
    params: {
      status?: "all" | "pending" | "refine" | "po" | "inbound" | "invoice" | "done";
      q?: string;
      page?: number;
      pageSize?: number;
    } = {}
  ) => {
    const sp = new URLSearchParams();
    if (params.status && params.status !== "all") sp.set("status", params.status);
    if (params.q) sp.set("q", params.q);
    if (params.page) sp.set("page", String(params.page));
    if (params.pageSize) sp.set("page_size", String(params.pageSize));
    const qs = sp.toString();
    return jsonFetch<BoardOrderList>(`/api/v1/procurement-board/orders${qs ? "?" + qs : ""}`);
  },
  orderDetail: (orderId: number) =>
    jsonFetch<BoardOrderDetail>(`/api/v1/procurement-board/orders/${orderId}/detail`),
};

// ---------- SKU 匹配工作台 ----------

export type InboundMatchAnomaly = {
  itemId: number;
  documentId: number;
  goodsdocNo: string;
  goodsNo: string;
  goodsName: string;
  quantity: number | null;
  amountTax: number | null;
  matchedSkuId: number | null;
  status: string;
  note: string;
};

export type InboundMatchSummary = {
  total: number;
  counts: Record<string, number>;
  anomalies: InboundMatchAnomaly[];
  manualMatches: InboundMatchAnomaly[];
};

export type SkuCandidate = {
  skuId: number;
  skuCode: string;
  skuName: string;
  barcode: string;
  unit: string;
  defaultCost: number | null;
  reason: string;
};

export type PendingAllocation = {
  poId: number;
  fileOrderId: number | null;
  externalOrderId: string;
  supplierName: string;
  orderedAt: string | null;
  paidAmount: number | null;
  allocationCount: number;
  purchaseStatus: string;
  editable: boolean;
  balance: {
    allocated: number;
    paid: number;
    diff: number;
    balanced: boolean;
    abnormalNote: string;
  };
};

export type AllocationBodyInput = {
  sku_id: number;
  quantity: string;
  unit_price: string;
};

export const skuMatchingApi = {
  inboundSummary: () => jsonFetch<InboundMatchSummary>("/api/v1/purchase/sku-matching/inbound"),
  runInboundAuto: () =>
    jsonFetch<{ ok: boolean; stats: Record<string, number> }>(
      "/api/v1/purchase/sku-matching/inbound-auto",
      { method: "POST" }
    ),
  pending: (limit = 50) =>
    jsonFetch<PendingAllocation[]>(`/api/v1/purchase/sku-matching/pending?limit=${limit}`),
  candidates: (poId: number) =>
    jsonFetch<{ poId: number; candidates: SkuCandidate[] }>(
      `/api/v1/purchase/sku-matching/${poId}/candidates`
    ),
  manualInbound: (itemId: number, skuId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/purchase/sku-matching/inbound/${itemId}/manual`, {
      method: "POST",
      body: JSON.stringify({ sku_id: skuId }),
    }),
  clearManualInbound: (itemId: number) =>
    jsonFetch<{ ok: boolean }>(`/api/v1/purchase/sku-matching/inbound/${itemId}/manual`, {
      method: "DELETE",
    }),
  acceptCost: (itemId: number) =>
    jsonFetch<{ ok: boolean; oldCost: string | null; newCost: string }>(
      `/api/v1/purchase/sku-matching/inbound/${itemId}/accept-cost`,
      { method: "POST", body: JSON.stringify({ confirm: true }) }
    ),
  batchAcceptCost: (documentId: number) =>
    jsonFetch<{ ok: boolean; documentId: number; accepted: number; failed: { itemId: number; reason: string }[] }>(
      "/api/v1/purchase/sku-matching/inbound/batch-accept-cost",
      { method: "POST", body: JSON.stringify({ document_id: documentId, confirm: true }) }
    ),
  addAllocation: (poId: number, body: AllocationBodyInput) =>
    jsonFetch<{ id: number; amount: string; balance: Record<string, unknown> }>(
      `/api/v1/purchase/orders/${poId}/allocations`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  skus: () =>
    jsonFetch<CatalogSkuRow[]>("/api/v1/dashboard/products?limit=500"),
};
