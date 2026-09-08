"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { authenticatedFetch } from "@/lib/api";

type Warehouse = {
  id: number;
  code: string;
  name: string;
  warehouseType: "factory" | "b2c" | "other";
  purpose: "goods" | "consumable" | "both";
  isSellable: boolean;
  status: "active" | "inactive";
  note: string;
};

type Draft = {
  code: string;
  name: string;
  warehouse_type: "factory" | "b2c" | "other";
  purpose: "goods" | "consumable" | "both";
  is_sellable: boolean;
  status: "active" | "inactive";
  note: string;
};

const emptyDraft = (): Draft => ({
  code: "",
  name: "",
  warehouse_type: "other",
  purpose: "both",
  is_sellable: false,
  status: "active",
  note: "",
});

const input = "h-9 rounded-md border border-slate-200 bg-white px-2 text-sm outline-none focus:border-indigo-400";

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await authenticatedFetch(url, {
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

function toDraft(row: Warehouse): Draft {
  return {
    code: row.code,
    name: row.name,
    warehouse_type: row.warehouseType,
    purpose: row.purpose,
    is_sellable: row.isSellable,
    status: row.status,
    note: row.note,
  };
}

export default function WarehouseSettingsPage() {
  const [rows, setRows] = useState<Warehouse[]>([]);
  const [drafts, setDrafts] = useState<Record<number, Draft>>({});
  const [newRow, setNewRow] = useState<Draft>(emptyDraft());
  const [adding, setAdding] = useState(false);
  const [busyId, setBusyId] = useState<number | "new" | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const data = await api<Warehouse[]>("/api/v1/warehouses?include_inactive=true");
    setRows(data);
    setDrafts(Object.fromEntries(data.map((row) => [row.id, toDraft(row)])));
  }, []);

  useEffect(() => { void load().catch((e) => setError(String(e))); }, [load]);

  async function save(id: number) {
    const draft = drafts[id];
    if (!draft) return;
    setBusyId(id); setError(""); setMessage("");
    try {
      await api(`/api/v1/warehouses/${id}`, { method: "PATCH", body: JSON.stringify(draft) });
      setMessage("仓库配置已保存。后续采购、收货和库存流水继续引用这个仓库 ID。");
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusyId(null); }
  }

  async function create() {
    setBusyId("new"); setError(""); setMessage("");
    try {
      await api("/api/v1/warehouses", { method: "POST", body: JSON.stringify(newRow) });
      setAdding(false); setNewRow(emptyDraft()); setMessage("新仓库已添加。");
      await load();
    } catch (e) { setError(String(e)); }
    finally { setBusyId(null); }
  }

  function patch(id: number, values: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [id]: { ...current[id], ...values } }));
  }

  return (
    <div className="mx-auto max-w-6xl px-5 py-5">
      <div className="sticky top-0 z-10 -mx-5 flex items-center justify-between border-b border-slate-200 bg-white/95 px-5 py-3 backdrop-blur">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-slate-400"><Link href="/settings" className="hover:text-indigo-600">设置</Link><span>/</span><span>仓库配置</span></div>
          <h1 className="mt-1 text-xl font-semibold text-slate-900">仓库配置</h1>
          <p className="mt-1 text-xs text-slate-500">默认只有工厂仓库和 B2C 仓库；名称、用途、可售属性都可以自行修改，后续也可继续新增。</p>
        </div>
        <button onClick={() => setAdding(true)} className="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">+ 添加仓库</button>
      </div>

      {message && <div className="mt-4 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{message}</div>}
      {error && <div className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead className="sticky top-[88px] bg-slate-50 text-xs text-slate-500">
            <tr><th className="px-3 py-2.5">编码</th><th>仓库名称</th><th>类型</th><th>用途</th><th>参与可售</th><th>状态</th><th>备注</th><th className="pr-3 text-right">操作</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {adding && <EditableRow draft={newRow} setDraft={setNewRow} busy={busyId === "new"} onSave={() => void create()} onCancel={() => { setAdding(false); setNewRow(emptyDraft()); }} />}
            {rows.map((row) => {
              const draft = drafts[row.id] ?? toDraft(row);
              return <EditableRow key={row.id} draft={draft} setDraft={(next) => patch(row.id, next)} busy={busyId === row.id} onSave={() => void save(row.id)} />;
            })}
          </tbody>
        </table>
        {!rows.length && !adding && <div className="p-10 text-center text-sm text-slate-400">暂无仓库配置</div>}
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs leading-6 text-slate-600">
        <b>当前建议：</b>工厂仓库用于工厂耗材和工厂侧货品；B2C 仓库用于最终电商发货库存。停用仓库不会删除历史单据，只会禁止新业务继续选择它。
      </div>
    </div>
  );
}

function EditableRow({ draft, setDraft, busy, onSave, onCancel }: {
  draft: Draft;
  setDraft: ((next: Draft) => void) | ((next: Partial<Draft>) => void);
  busy: boolean;
  onSave: () => void;
  onCancel?: () => void;
}) {
  const change = (next: Partial<Draft>) => setDraft({ ...draft, ...next } as Draft);
  return <tr className={draft.status === "inactive" ? "bg-slate-50/70 text-slate-400" : ""}>
    <td className="px-3 py-2"><input value={draft.code} onChange={(e) => change({ code: e.target.value.toUpperCase() })} className={`${input} w-28 font-mono`} placeholder="FACTORY" /></td>
    <td><input value={draft.name} onChange={(e) => change({ name: e.target.value })} className={`${input} w-40`} placeholder="仓库名称" /></td>
    <td><select value={draft.warehouse_type} onChange={(e) => change({ warehouse_type: e.target.value as Draft["warehouse_type"] })} className={`${input} w-28`}><option value="factory">工厂仓</option><option value="b2c">B2C仓</option><option value="other">其他</option></select></td>
    <td><select value={draft.purpose} onChange={(e) => change({ purpose: e.target.value as Draft["purpose"] })} className={`${input} w-32`}><option value="both">正品 + 耗材</option><option value="goods">仅正品</option><option value="consumable">仅耗材</option></select></td>
    <td><label className="inline-flex items-center gap-2"><input type="checkbox" checked={draft.is_sellable} onChange={(e) => change({ is_sellable: e.target.checked })} /><span className="text-xs">{draft.is_sellable ? "是" : "否"}</span></label></td>
    <td><select value={draft.status} onChange={(e) => change({ status: e.target.value as Draft["status"] })} className={`${input} w-24`}><option value="active">启用</option><option value="inactive">停用</option></select></td>
    <td><input value={draft.note} onChange={(e) => change({ note: e.target.value })} className={`${input} w-full min-w-[180px]`} placeholder="可选备注" /></td>
    <td className="pr-3 text-right whitespace-nowrap"><button disabled={busy || !draft.code.trim() || !draft.name.trim()} onClick={onSave} className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs text-white disabled:opacity-40">{busy ? "保存中…" : "保存"}</button>{onCancel && <button disabled={busy} onClick={onCancel} className="ml-2 text-xs text-slate-500">取消</button>}</td>
  </tr>;
}
