"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { authenticatedFetch } from "@/lib/api";

type Rule = {
  id: number;
  pattern: string;
  categoryName: string;
  itemName: string;
  matchKeyword: string;
  matchMode: "contains" | "exact" | "prefix";
  priority: number;
  enabled: boolean;
  note: string;
};

type FormState = {
  pattern: string;
  matchKeyword: string;
  matchMode: "contains" | "exact" | "prefix";
  priority: number;
  enabled: boolean;
  note: string;
};

const emptyForm: FormState = {
  pattern: "*软饮料*咖啡",
  matchKeyword: "咖啡",
  matchMode: "contains",
  priority: 100,
  enabled: true,
  note: "",
};

export default function TaxAccountingCategoriesPage() {
  const [items, setItems] = useState<Rule[]>([]);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await authenticatedFetch("/api/v1/tax-accounting/category-rules", { cache: "no-store" });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `加载失败（${res.status}）`);
      setItems(payload.items || []);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  function startEdit(row: Rule) {
    setEditingId(row.id);
    setForm({
      pattern: row.pattern,
      matchKeyword: row.matchKeyword,
      matchMode: row.matchMode,
      priority: row.priority,
      enabled: row.enabled,
      note: row.note,
    });
    setMessage("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function resetForm() {
    setEditingId(null);
    setForm(emptyForm);
    setError("");
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const url = editingId
        ? `/api/v1/tax-accounting/category-rules/${editingId}`
        : "/api/v1/tax-accounting/category-rules";
      const res = await authenticatedFetch(url, {
        method: editingId ? "PATCH" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          pattern: form.pattern,
          match_keyword: form.matchKeyword,
          match_mode: form.matchMode,
          priority: form.priority,
          enabled: form.enabled,
          note: form.note,
        }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `保存失败（${res.status}）`);
      setMessage(editingId ? "分类规则已修改" : "分类规则已新增");
      resetForm();
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setSaving(false);
    }
  }

  async function toggle(row: Rule) {
    setError("");
    try {
      const res = await authenticatedFetch(`/api/v1/tax-accounting/category-rules/${row.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !row.enabled }),
      });
      const payload = await res.json();
      if (!res.ok) throw new Error(payload?.detail || `更新失败（${res.status}）`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }

  return (
    <div className="mx-auto max-w-[1300px] space-y-5">
      <header className="sticky top-0 z-20 -mx-8 -mt-6 border-b border-slate-200 bg-white/95 px-8 py-5 backdrop-blur">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-xs font-medium text-indigo-600">ACCOUNTING CATEGORY RULES</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-900">财务分类规则</h1>
            <p className="mt-1 text-sm text-slate-500">你自己维护 · 输入格式如 *软饮料*咖啡 · 无需修改代码</p>
          </div>
          <Link href="/finance/tax-accounting" className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700">
            返回税务做账
          </Link>
        </div>
      </header>

      <section className="rounded-2xl border border-indigo-100 bg-indigo-50/50 p-5">
        <div className="text-sm font-semibold text-indigo-900">怎么填</div>
        <div className="mt-2 grid gap-2 text-sm text-indigo-800 md:grid-cols-3">
          <div><span className="font-medium">开票格式：</span>*软饮料*咖啡</div>
          <div><span className="font-medium">匹配关键字：</span>咖啡</div>
          <div><span className="font-medium">结果：</span>归入“软饮料”</div>
        </div>
        <p className="mt-2 text-xs leading-5 text-indigo-700">官方发票本身已经带税收分类或 *大类*项目 前缀时，以官方内容优先；只有官方分类缺失时，才使用你这里维护的规则。</p>
      </section>

      <form onSubmit={save} className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-slate-900">{editingId ? "修改规则" : "新增规则"}</h2>
          {editingId && <button type="button" onClick={resetForm} className="text-xs text-slate-500">取消修改</button>}
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-6">
          <label className="xl:col-span-2 text-xs text-slate-500">开票分类格式
            <input value={form.pattern} onChange={(e) => setForm({ ...form, pattern: e.target.value })} placeholder="*软饮料*咖啡" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm text-slate-900" />
          </label>
          <label className="text-xs text-slate-500">匹配关键字
            <input value={form.matchKeyword} onChange={(e) => setForm({ ...form, matchKeyword: e.target.value })} placeholder="咖啡" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm" />
          </label>
          <label className="text-xs text-slate-500">匹配方式
            <select value={form.matchMode} onChange={(e) => setForm({ ...form, matchMode: e.target.value as FormState["matchMode"] })} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm">
              <option value="contains">包含</option>
              <option value="exact">完全等于</option>
              <option value="prefix">开头是</option>
            </select>
          </label>
          <label className="text-xs text-slate-500">优先级
            <input type="number" min={0} max={9999} value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) || 0 })} className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm" />
          </label>
          <label className="flex items-end gap-2 pb-2 text-sm text-slate-700">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> 启用
          </label>
        </div>
        <label className="mt-4 block text-xs text-slate-500">备注
          <input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} placeholder="可选" className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2.5 text-sm" />
        </label>
        <div className="mt-4 flex items-center gap-3">
          <button disabled={saving} className="rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white disabled:opacity-50">{saving ? "保存中…" : editingId ? "保存修改" : "添加规则"}</button>
          <span className="text-xs text-slate-400">优先级数字越小越先匹配</span>
        </div>
      </form>

      {error && <div className="rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      {message && <div className="rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="text-base font-semibold text-slate-900">已维护规则</h2>
            <p className="mt-1 text-xs text-slate-500">可以修改或停用；停用后历史发票明细不删除，只是不再用于新的分类计算。</p>
          </div>
          <span className="text-xs text-slate-400">{loading ? "加载中…" : `${items.length} 条`}</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
              <tr>
                <th className="px-4 py-2.5">开票分类格式</th>
                <th className="px-4 py-2.5">财务大类</th>
                <th className="px-4 py-2.5">匹配关键字</th>
                <th className="px-4 py-2.5">匹配方式</th>
                <th className="px-4 py-2.5 text-right">优先级</th>
                <th className="px-4 py-2.5">状态</th>
                <th className="px-4 py-2.5 text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((row) => (
                <tr key={row.id} className={!row.enabled ? "bg-slate-50/60 text-slate-400" : ""}>
                  <td className="px-4 py-3 font-medium text-slate-900">{row.pattern}</td>
                  <td className="px-4 py-3">{row.categoryName}</td>
                  <td className="px-4 py-3">{row.matchKeyword}</td>
                  <td className="px-4 py-3">{row.matchMode === "contains" ? "包含" : row.matchMode === "exact" ? "完全等于" : "开头是"}</td>
                  <td className="px-4 py-3 text-right tabular-nums">{row.priority}</td>
                  <td className="px-4 py-3"><span className={`rounded-full px-2 py-1 text-[10px] font-medium ${row.enabled ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{row.enabled ? "启用" : "停用"}</span></td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      <button onClick={() => startEdit(row)} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-700">修改</button>
                      <button onClick={() => void toggle(row)} className="rounded-md border border-slate-200 px-2.5 py-1.5 text-xs text-slate-600">{row.enabled ? "停用" : "启用"}</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && items.length === 0 && <div className="p-8 text-center text-sm text-slate-400">还没有规则，先添加一条 *软饮料*咖啡</div>}
        </div>
      </section>
    </div>
  );
}
