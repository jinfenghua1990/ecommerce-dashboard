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
  const res = await fetch("/api/v1/system/overview", { cache: "no-store" });
  if (!res.ok) throw new Error(`overview ${res.status}`);
  return res.json();
}

export async function testJackyun(): Promise<{ ok: boolean; tools?: string[]; error?: string }> {
  const res = await fetch("/api/v1/integrations/jackyun/test", { method: "POST" });
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
  const res = await fetch("/api/v1/exceptions", { cache: "no-store" });
  if (!res.ok) throw new Error(`exceptions ${res.status}`);
  return res.json();
}

export async function updateExceptionStatus(
  id: number,
  status: string,
  note: string
): Promise<void> {
  await fetch(`/api/v1/exceptions/${id}/status`, {
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
  const res = await fetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
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
export type SalesOrderRow = {
  id: number; orderNo: string; platform: string; storeName: string;
  orderStatus: string; payStatus: string;
  orderAmount: string | null; paidAmount: string | null; orderedAt: string | null;
};
export type AftersaleRow = {
  id: number; aftersaleNo: string; orderNo: string; type: string; status: string;
  refundAmount: string | null; reason: string; createdAt: string | null;
};

export const dashboardApi = {
  salesTrend: (days = 30) => jsonFetch<TrendPoint[]>(`/api/v1/dashboard/sales-trend?days=${days}`),
  platformRanking: () => jsonFetch<PlatformRow[]>("/api/v1/dashboard/platform-ranking"),
  skuRanking: (limit = 20) => jsonFetch<SkuRow[]>(`/api/v1/dashboard/sku-ranking?limit=${limit}`),
  inventory: () => jsonFetch<InventorySummary>("/api/v1/dashboard/inventory"),
  orders: (status?: string) =>
    jsonFetch<SalesOrderRow[]>(`/api/v1/dashboard/orders${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  aftersales: () => jsonFetch<AftersaleRow[]>("/api/v1/dashboard/aftersales"),
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
};
