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
