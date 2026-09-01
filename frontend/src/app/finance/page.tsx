"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Pkg = { id: number; version: number; status: string; sha256: string; createdAt: string | null };
type Period = {
  company: string;
  year: number;
  month: number;
  status: string;
  missing: Record<string, number>;
  fileCount: number;
  packages: Pkg[];
};
type FileRow = {
  id: number;
  category: string;
  originalName: string;
  size: number;
  sha256: string;
  version: number;
  uploader: string;
  uploadedAt: string | null;
};

const STATUS_STYLE: Record<string, string> = {
  INCOMPLETE: "bg-amber-50 text-amber-700 ring-amber-200",
  READY: "bg-sky-50 text-sky-700 ring-sky-200",
  PACKAGED: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  SENT: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ERROR: "bg-red-50 text-red-700 ring-red-200",
};
const CAT_LABEL: Record<string, string> = { bank: "银行资料", jackyun: "吉客云导出", invoice: "发票", other: "其他" };

export default function FinancePage() {
  const [periods, setPeriods] = useState<Period[]>([]);
  const [sel, setSel] = useState<{ year: number; month: number } | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const [month, setMonth] = useState("");
  const [category, setCategory] = useState("bank");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadPeriods = useCallback(() => {
    fetch("/api/v1/finance/periods", { cache: "no-store" })
      .then((r) => r.json())
      .then((d: Period[]) => {
        setPeriods(d);
        if (!sel && d.length > 0) setSel({ year: d[0].year, month: d[0].month });
      })
      .catch(() => {});
  }, [sel]);

  const loadFiles = useCallback(() => {
    if (!sel) return;
    fetch(`/api/v1/finance/${sel.year}/${sel.month}/files`, { cache: "no-store" })
      .then((r) => r.json())
      .then(setFiles)
      .catch(() => {});
  }, [sel]);

  useEffect(loadPeriods, [loadPeriods]);
  useEffect(loadFiles, [loadFiles]);

  async function upload() {
    const f = fileRef.current?.files?.[0];
    if (!f || !month) {
      setMsg("请选择账期月份和文件");
      return;
    }
    const [year, mm] = month.split("-").map(Number);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("period_year", String(year));
    fd.append("period_month", String(mm));
    fd.append("category", category);
    setBusy(true);
    try {
      const res = await fetch("/api/v1/finance/files", { method: "POST", body: fd });
      const d = await res.json();
      if (res.ok) {
        setMsg(`已归档 v${d.version} · SHA256 ${d.sha256.slice(0, 16)}…`);
        if (fileRef.current) fileRef.current.value = "";
      } else {
        setMsg(`失败：${d.detail}`);
      }
    } finally {
      setBusy(false);
      loadPeriods();
      loadFiles();
    }
  }

  async function act(path: string, ok: (d: Record<string, unknown>) => string, body?: unknown) {
    if (!sel) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/v1/finance/${sel.year}/${sel.month}/${path}`, {
        method: "POST",
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const d = await res.json();
      setMsg(res.ok ? ok(d) : `失败：${d.detail}`);
    } finally {
      setBusy(false);
      loadPeriods();
      loadFiles();
    }
  }

  const current = periods.find((p) => sel && p.year === sel.year && p.month === sel.month);

  return (
    <div>
      <h1 className="text-xl font-semibold">财务资料</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        给财务的是原始资料：上传即归档（SHA256 + 版本化，同名不覆盖），资料齐全才可原样 ZIP 打包；
        SMTP 发送待邮件配置后开放（Phase 6）。
      </p>

      <div className="mt-5 grid max-w-4xl grid-cols-4 items-end gap-3 rounded-xl border border-gray-200 bg-white p-4">
        <label className="text-xs text-gray-500">
          账期月份
          <input
            type="month"
            value={month}
            onChange={(e) => setMonth(e.target.value)}
            className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
          />
        </label>
        <label className="text-xs text-gray-500">
          资料类别
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="mt-1 w-full rounded-lg border border-gray-200 px-2 py-1.5 text-sm"
          >
            {Object.entries(CAT_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </label>
        <label className="text-xs text-gray-500">
          文件（XLSX / PDF / ZIP）
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xls,.pdf,.zip"
            className="mt-1 w-full text-sm"
          />
        </label>
        <button
          onClick={upload}
          disabled={busy}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          上传归档
        </button>
      </div>

      {msg && <div className="mt-3 rounded-lg bg-gray-100 px-3 py-2 text-xs text-gray-600">{msg}</div>}

      <div className="mt-6 grid max-w-4xl grid-cols-1 gap-3">
        {periods.map((p) => (
          <div
            key={`${p.year}-${p.month}`}
            onClick={() => setSel({ year: p.year, month: p.month })}
            className={`cursor-pointer rounded-xl border p-4 ${
              sel && p.year === sel.year && p.month === sel.month
                ? "border-indigo-300 bg-indigo-50/40"
                : "border-gray-200 bg-white"
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="text-sm font-medium">
                {p.year} 年 {p.month} 月 · {p.company}
              </div>
              <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ring-inset ${STATUS_STYLE[p.status] ?? ""}`}>
                {p.status}
              </span>
            </div>
            <div className="mt-1 text-xs text-gray-400">
              已归档 {p.fileCount} 个文件
              {Object.keys(p.missing).length > 0 &&
                ` · 缺少：${Object.entries(p.missing).map(([c, n]) => `${CAT_LABEL[c] ?? c}×${n}`).join("、")}`}
              {p.packages.length > 0 && ` · 交付包 V${p.packages.map((x) => x.version).join("/V")}`}
            </div>
            {p.packages.map((pkg) => (
              <a
                key={pkg.id}
                href={`/api/v1/finance/packages/${pkg.id}/download`}
                onClick={(e) => e.stopPropagation()}
                className="mt-2 inline-block text-xs text-indigo-600 hover:underline"
              >
                下载 finance ZIP V{pkg.version}（{pkg.status}）
              </a>
            ))}
          </div>
        ))}
      </div>

      {sel && (
        <div className="mt-6 max-w-4xl">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-medium text-gray-700">
              {sel.year} 年 {sel.month} 月 文件清单
            </h2>
            <button
              onClick={() => act("check", (d) => `完整性检查：${d.status}`)}
              disabled={busy}
              className="rounded-lg bg-white px-2.5 py-1 text-xs text-gray-600 ring-1 ring-gray-200"
            >
              检查完整性
            </button>
            <button
              onClick={() => act("package", (d) => `已生成 ZIP V${d.version}`)}
              disabled={busy}
              className="rounded-lg bg-white px-2.5 py-1 text-xs text-gray-600 ring-1 ring-gray-200"
            >
              打包 ZIP
            </button>
            <button
              onClick={() => act("send", (d) => `已发送（${d.kind === "resent" ? "重发 RESENT" : "首次 first"}）V${d.version}`)}
              disabled={busy}
              className="rounded-lg bg-emerald-600 px-2.5 py-1 text-xs text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              发送给财务
            </button>
          </div>
          <div className="mt-2 overflow-hidden rounded-xl border border-gray-200 bg-white">
            {files.length === 0 ? (
              <div className="p-6 text-center text-sm text-gray-400">该账期暂无归档文件</div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-xs text-gray-500">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">类别</th>
                    <th className="px-4 py-2.5 font-medium">原始文件名</th>
                    <th className="px-4 py-2.5 font-medium">版本</th>
                    <th className="px-4 py-2.5 font-medium">SHA256</th>
                    <th className="px-4 py-2.5 font-medium">上传时间</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {files.map((r) => (
                    <tr key={r.id}>
                      <td className="px-4 py-2.5 text-xs">{CAT_LABEL[r.category] ?? r.category}</td>
                      <td className="px-4 py-2.5">{r.originalName}</td>
                      <td className="px-4 py-2.5 text-xs text-gray-500">v{r.version}</td>
                      <td className="px-4 py-2.5 font-mono text-xs text-gray-400">{r.sha256}…</td>
                      <td className="px-4 py-2.5 text-xs text-gray-400">
                        {r.uploadedAt ? new Date(r.uploadedAt).toLocaleString("zh-CN") : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
