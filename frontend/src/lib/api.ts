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
