"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import MetricCard from "@/components/metric-card";
import ConsumableWorkbench from "./consumable-workbench";
import {
  CatalogSkuRow,
  consumablesApi,
  dashboardApi,
  InventorySummary,
  LinkedSkuRef,
  UnifiedCatalogRow,
} from "@/lib/api";

/** 类型中文：single=单品 / bundle=套装 / virtual_bundle=虚拟组合套装（不同商品不同数量组合）。 */
const TYPE_LABEL: Record<string, string> = { single: "单品", bundle: "套装", virtual_bundle: "虚拟组合套装" };

const emptyProduct = { jackyun_sku_id: "", sku_code: "", product_type: "single", sku_name: "", barcode: "", unit: "盒", sale_price: "", default_cost: "", cost_mode: "fixed", cost_tolerance_pct: "0.0200", tax_code: "", goods_category: "", status: "active" };
const qty = (v: string | null) => (v == null || v === "" ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 4 }));

type KindFilter = "all" | "goods" | "consumable";

type ConsumableDraft = {
  id: number | null;
  code: string;
  name: string;
  barcode: string;
  category: string;
  unit: string;
  purchase_unit_cost: string;
  min_stock_qty: string;
  tax_code: string;
  sku_ids: number[];
};

const emptyConsumable: ConsumableDraft = {
  id: null, code: "", name: "", barcode: "", category: "", unit: "个",
  purchase_unit_cost: "", min_stock_qty: "", tax_code: "", sku_ids: [],
};

function KindBadge({ kind }: { kind: "goods" | "consumable" }) {
  return kind === "consumable"
    ? <span className="mr-1.5 inline-block shrink-0 rounded bg-amber-100 px-1 py-px align-[1px] text-[10px] font-semibold leading-4 text-amber-700">耗</span>
    : <span className="mr-1.5 inline-block shrink-0 rounded bg-slate-100 px-1 py-px align-[1px] text-[10px] font-semibold leading-4 text-slate-500">品</span>;
}

function StockCell({ row }: { row: UnifiedCatalogRow }) {
  if (row.kind === "goods") {
    return row.hasSnapshot
      ? <span className="tabular-nums text-gray-800">{qty(row.stockOwn)}</span>
      : <span className="text-xs text-gray-300">无快照</span>;
  }
  return (
    <span className="tabular-nums">
      <span className={row.lowStock ? "font-semibold text-amber-600" : "text-gray-800"}>{qty(row.stockOwn)}</span>
      <span className="text-[11px] text-gray-400"> +厂{qty(row.stockFactory)} +途{qty(row.stockTransit)}</span>
    </span>
  );
}

function CatalogTable({ rows, onEditConsumable, onEditProduct, onToggleCostMode, selected, onToggleRow, onToggleAll }: {
  rows: UnifiedCatalogRow[];
  onEditConsumable: (row: UnifiedCatalogRow) => void;
  onEditProduct: (row: UnifiedCatalogRow) => void;
  onToggleCostMode: (row: UnifiedCatalogRow) => void;
  selected: Set<string>;
  onToggleRow: (row: UnifiedCatalogRow) => void;
  onToggleAll: () => void;
}) {
  const allChecked = rows.length > 0 && rows.every((r) => selected.has(`${r.kind}-${r.id}`));
  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full min-w-[1060px] text-sm">
        <thead className="text-left text-xs text-gray-500">
          <tr className="border-b border-gray-100">
            <th className="w-8 py-2">
              <input type="checkbox" checked={allChecked} onChange={onToggleAll} title="全选/取消本页" className="h-3.5 w-3.5 accent-blue-600" />
            </th>
            <th className="py-2">货品名称</th>
            <th className="py-2">类型</th>
            <th className="py-2">品类</th>
            <th className="py-2">编码</th>
            <th className="py-2">条码</th>
            <th className="py-2">税务代码</th>
            <th className="py-2">关联正品</th>
            <th className="py-2">单位</th>
            <th className="py-2 text-right">库存</th>
            <th className="py-2 text-right">安全库存</th>
            <th className="py-2">状态</th>
            <th className="py-2 text-right">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row) => (
            <tr key={`${row.kind}-${row.id}`} className={row.lowStock ? "bg-amber-50/60" : ""}>
              <td className="py-2.5">
                <input type="checkbox" checked={selected.has(`${row.kind}-${row.id}`)} onChange={() => onToggleRow(row)} className="h-3.5 w-3.5 accent-blue-600" />
              </td>
              <td className="max-w-[240px] py-2.5">
                <div className="truncate">
                  <KindBadge kind={row.kind} />
                  <span className={row.status !== "active" ? "text-gray-400" : "text-gray-800"}>{row.name}</span>
                </div>
                {row.kind === "goods" && row.goodsName && row.goodsName !== row.name && <div className="mt-0.5 truncate pl-6 text-[10px] text-gray-400">{row.goodsName}</div>}
              </td>
              <td className="py-2.5 text-[11px] text-gray-500">{row.kind === "goods" ? TYPE_LABEL[row.category] ?? row.category : "耗材"}</td>
              <td className="py-2.5 text-[11px] text-gray-500">{row.goodsCategory || "—"}</td>
              <td className="py-2.5 font-mono text-xs text-gray-600">{row.code}</td>
              <td className="py-2.5 font-mono text-[11px] text-gray-500">{row.barcode || "—"}</td>
              <td className="py-2.5 font-mono text-[11px] text-gray-500" title={row.taxCode ? "税收分类编码（开票用）" : "未设置税务代码，点「编辑」补充"}>{row.taxCode || "—"}</td>
              <td className="max-w-[160px] py-2.5">
                {row.kind === "consumable"
                  ? (row.linkedSkus.length
                    ? <span className="text-[11px] text-gray-500" title={row.linkedSkus.map((s) => s.skuCode).join(", ")}>{row.linkedSkus.slice(0, 2).map((s) => s.skuCode).join(", ")}{row.linkedSkus.length > 2 ? ` +${row.linkedSkus.length - 2}` : ""}</span>
                    : <span className="text-[11px] text-gray-300">未关联</span>)
                  : <span className="text-[11px] text-gray-300">—</span>}
              </td>
              <td className="py-2.5 text-gray-500">{row.unit || "—"}</td>
              <td className="py-2.5 text-right"><StockCell row={row} /></td>
              <td className="py-2.5 text-right tabular-nums text-gray-500">{row.kind === "consumable" ? qty(row.minStock) : "—"}</td>
              <td className="py-2.5">
                {row.lowStock
                  ? <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-700">库存预警</span>
                  : row.status !== "active"
                    ? <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] text-gray-400">停用</span>
                    : <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-700">正常</span>}
              </td>
              <td className="py-2.5 text-right">
                {row.kind === "consumable"
                  ? <button onClick={() => onEditConsumable(row)} className="rounded-md px-2 py-1 text-xs font-medium text-amber-600 hover:bg-amber-50">编辑</button>
                  : <div className="flex justify-end gap-1">
                      <button onClick={() => onEditProduct(row)} className="rounded-md px-2 py-1 text-xs text-gray-500 hover:bg-gray-100">编辑</button>
                      <button onClick={() => onToggleCostMode(row)} className="rounded-md px-2 py-1 text-xs text-blue-600 hover:bg-blue-50">
                        切{row.costMode === "dynamic" ? "固定" : "动态"}
                      </button>
                    </div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <p className="p-8 text-center text-sm text-gray-400">没有匹配的货品</p>}
    </div>
  );
}

export default function ProductsPage() {
  const [tab, setTab] = useState<"catalog" | "bundles" | "consumables">("catalog");
  const [inv, setInv] = useState<InventorySummary | null>(null);
  const [catalog, setCatalog] = useState<UnifiedCatalogRow[]>([]);
  const [products, setProducts] = useState<CatalogSkuRow[]>([]);
  const [consumables, setConsumables] = useState<Awaited<ReturnType<typeof consumablesApi.list>>>([]);
  const [kind, setKind] = useState<KindFilter>("all");
  const [search, setSearch] = useState("");
  const [bundleSearch, setBundleSearch] = useState("");
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [productForm, setProductForm] = useState(emptyProduct);
  const [consumableEditor, setConsumableEditor] = useState<"new" | number | null>(null);
  const [consumableDraft, setConsumableDraft] = useState<ConsumableDraft>(emptyConsumable);
  const [savingConsumable, setSavingConsumable] = useState(false);
  const [skuSearch, setSkuSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkTaxCode, setBulkTaxCode] = useState("");
  const [bulkOverwrite, setBulkOverwrite] = useState(false);
  const [bulkSaving, setBulkSaving] = useState(false);

  useEffect(() => {
    const productTab = new URLSearchParams(window.location.search).get("productTab");
    if (productTab === "inventory") window.location.replace("/purchase/workbench?view=inventory_goods");
    else if (productTab === "consumables") setTab("consumables");
    else if (productTab === "bundles") setTab("bundles");
  }, []);

  const load = useCallback(() => {
    Promise.all([
      dashboardApi.inventory(),
      dashboardApi.catalogUnified("all", search),
      dashboardApi.products(""),
      consumablesApi.list(""),
    ])
      .then(([inventory, unified, skuRows, materialRows]) => {
        setInv(inventory); setCatalog(unified); setProducts(skuRows); setConsumables(materialRows);
      })
      .catch((e) => setErr(String(e)));
  }, [search]);
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t); }, [load]);

  const counts = useMemo(() => ({
    goods: catalog.filter((r) => r.kind === "goods").length,
    bundles: catalog.filter((r) => r.kind === "goods" && r.category === "virtual_bundle").length,
    consumable: catalog.filter((r) => r.kind === "consumable").length,
    low: catalog.filter((r) => r.lowStock).length,
  }), [catalog]);

  const bundleRows = useMemo(() => {
    const term = bundleSearch.trim().toLowerCase();
    return catalog.filter((r) => r.kind === "goods" && r.category === "virtual_bundle")
      .filter((r) => !term || `${r.code} ${r.name} ${r.barcode}`.toLowerCase().includes(term));
  }, [catalog, bundleSearch]);
  const catalogRows = useMemo(() => {
    const base = catalog.filter((r) => !(r.kind === "goods" && r.category === "virtual_bundle"));
    if (kind === "all") return base;
    return base.filter((r) => r.kind === kind);
  }, [catalog, kind]);

  const saveCostMode = async (row: UnifiedCatalogRow) => {
    const mode = (row.costMode || "fixed") === "fixed" ? "dynamic" : "fixed";
    try { await dashboardApi.updateCostPolicy(row.id, mode, row.costTolerancePct || "0.0200"); setMsg(`${row.code} 已切换为${mode === "fixed" ? "固定成本" : "动态成本"}`); load(); } catch (e) { setErr(String(e)); }
  };

  const toggleRow = (row: UnifiedCatalogRow) => {
    const key = `${row.kind}-${row.id}`;
    setSelected((prev) => { const next = new Set(prev); if (next.has(key)) next.delete(key); else next.add(key); return next; });
  };
  const toggleAll = (rows: UnifiedCatalogRow[]) => {
    setSelected((prev) => {
      const all = rows.every((r) => prev.has(`${r.kind}-${r.id}`));
      const next = new Set(prev);
      rows.forEach((r) => { const key = `${r.kind}-${r.id}`; if (all) next.delete(key); else next.add(key); });
      return next;
    });
  };
  const applyBulkTaxCode = async () => {
    const items = [...selected].map((key) => { const [kind, id] = key.split("-"); return { kind: kind as "goods" | "consumable", id: Number(id) }; });
    if (!items.length || !bulkTaxCode.trim()) return;
    setBulkSaving(true);
    try {
      const r = await dashboardApi.bulkSetTaxCode(items, bulkTaxCode.trim(), bulkOverwrite);
      setMsg(`税务代码已设置：更新 ${r.updated} 项${r.skipped ? `，跳过已有值 ${r.skipped} 项` : ""}${r.missing ? `，未找到 ${r.missing} 项` : ""}`);
      setSelected(new Set());
      load();
    } catch (e) { setErr(String(e)); } finally { setBulkSaving(false); }
  };
  const saveProduct = async (event: React.FormEvent) => {
    event.preventDefault();
    try { await dashboardApi.saveProduct({ ...productForm, sku_id: editing || undefined }); setMsg(editing ? "货品档案已修改" : "货品档案已新建"); setEditing(null); load(); } catch (e) { setErr(String(e)); }
  };
  const startEditProduct = (row: UnifiedCatalogRow) => {
    setEditing(row.id);
    setProductForm({ jackyun_sku_id: "", sku_code: row.code, product_type: row.category in TYPE_LABEL ? row.category : "single", sku_name: row.name, barcode: row.barcode, unit: row.unit, sale_price: row.salePrice || "", default_cost: row.defaultCost || "", cost_mode: row.costMode || "fixed", cost_tolerance_pct: row.costTolerancePct || "0.0200", tax_code: row.taxCode || "", goods_category: row.goodsCategory || "", status: row.status });
  };
  const startNewProduct = () => { setEditing(0); setProductForm(emptyProduct); setTab("catalog"); };
  const importWorkbook = async (event: React.ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (!file) return; try { const result = await consumablesApi.importXlsx(file); setMsg(`已导入：新增 ${result.created}、更新 ${result.updated}、映射 ${result.mappings}`); load(); } catch (e) { setErr(String(e)); } event.target.value = ""; };

  const openConsumableEditor = (row?: UnifiedCatalogRow) => {
    if (row) {
      setConsumableDraft({
        id: row.id, code: row.code, name: row.name, barcode: row.barcode,
        category: row.category, unit: row.unit,
        purchase_unit_cost: "", min_stock_qty: row.minStock || "", tax_code: row.taxCode || "",
        sku_ids: row.linkedSkus.map((s) => s.skuId),
      });
      setConsumableEditor(row.id);
    } else {
      setConsumableDraft(emptyConsumable);
      setConsumableEditor("new");
    }
    setTab("catalog");
    setSkuSearch("");
  };
  const openConsumableEditorById = (id: number) => {
    const row = consumables.find((c) => c.id === id);
    if (!row) return;
    setConsumableDraft({
      id: row.id, code: row.code, name: row.name, barcode: row.barcode || "",
      category: row.category, unit: row.unit,
      purchase_unit_cost: row.purchaseUnitCost ?? "", min_stock_qty: row.minStockQty,
      tax_code: row.taxCode || "",
      sku_ids: (row.linkedSkus || []).map((s) => s.skuId),
    });
    setConsumableEditor(row.id);
    setSkuSearch("");
  };
  const toggleLinkedSku = (skuId: number) => {
    setConsumableDraft((d) => ({ ...d, sku_ids: d.sku_ids.includes(skuId) ? d.sku_ids.filter((x) => x !== skuId) : [...d.sku_ids, skuId] }));
  };
  const saveConsumable = async (event: React.FormEvent) => {
    event.preventDefault();
    setSavingConsumable(true);
    try {
      await consumablesApi.save({
        consumable_id: consumableEditor === "new" ? undefined : (consumableDraft.id ?? consumableEditor),
        code: consumableDraft.code,
        name: consumableDraft.name,
        barcode: consumableDraft.barcode,
        category: consumableDraft.category,
        unit: consumableDraft.unit,
        purchase_unit_cost: consumableDraft.purchase_unit_cost || null,
        min_stock_qty: consumableDraft.min_stock_qty || "0",
        tax_code: consumableDraft.tax_code,
        sku_ids: consumableDraft.sku_ids,
      });
      setMsg(consumableEditor === "new" ? "耗材档案已建立（库存请通过采购收货或库存流水登记）" : "耗材档案已更新");
      setConsumableEditor(null);
      load();
    } catch (e) { setErr(String(e)); } finally { setSavingConsumable(false); }
  };

  const skuCandidates = useMemo(() => {
    const term = skuSearch.trim().toLowerCase();
    return products.filter((p) => !term || `${p.skuCode} ${p.skuName} ${p.goodsName}`.toLowerCase().includes(term)).slice(0, 30);
  }, [products, skuSearch]);
  const linkedRefs = useMemo(() => {
    const map = new Map<number, LinkedSkuRef>();
    products.forEach((p) => map.set(p.id, { skuId: p.id, skuCode: p.skuCode, skuName: p.skuName || p.goodsName }));
    return consumableDraft.sku_ids.map((id) => map.get(id)).filter(Boolean) as LinkedSkuRef[];
  }, [consumableDraft.sku_ids, products]);

  const bulkBar = selected.size > 0 && <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs">
    <span className="font-medium text-blue-700">已选 {selected.size} 项</span>
    <input placeholder="税务代码（19 位税收分类编码）" value={bulkTaxCode} onChange={(e) => setBulkTaxCode(e.target.value.replace(/\D/g, ""))} maxLength={21} className="w-56 rounded border bg-white px-2 py-1.5 font-mono" />
    <label className="flex items-center gap-1 text-gray-600"><input type="checkbox" checked={bulkOverwrite} onChange={(e) => setBulkOverwrite(e.target.checked)} className="h-3.5 w-3.5 accent-blue-600" />覆盖已有值（默认仅填空缺）</label>
    <button onClick={applyBulkTaxCode} disabled={bulkSaving || !bulkTaxCode.trim()} className="rounded bg-blue-600 px-3 py-1.5 font-medium text-white hover:bg-blue-700 disabled:opacity-50">{bulkSaving ? "保存中…" : "批量设置税务代码"}</button>
    <button onClick={() => setSelected(new Set())} className="rounded border bg-white px-3 py-1.5 text-gray-600 hover:bg-gray-50">取消选择</button>
  </div>;

  return <div>
    <div className="sticky top-0 z-20 -mx-8 -mt-6 flex flex-wrap items-end justify-between gap-3 border-b border-gray-200 bg-white/95 px-8 py-5 backdrop-blur">
      <div>
        <h1 className="text-xl font-semibold">货品档案</h1>
        <p className="mt-1 text-sm text-gray-400">正品、耗材、组合套装的新增、编辑、关联和导入都在本页完成；业务配置不再放到系统设置。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button onClick={startNewProduct} className="rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700">+ 新建正品</button>
        <button onClick={() => openConsumableEditor()} className="rounded-lg bg-amber-500 px-3 py-2 text-sm font-medium text-white hover:bg-amber-600">+ 新建耗材</button>
        <label className="cursor-pointer rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 hover:bg-gray-50">导入耗材档案<input type="file" accept=".xlsx,.xls" className="hidden" onChange={importWorkbook} /></label>
      </div>
    </div>

    {err && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700">{err}<button className="ml-3" onClick={() => setErr("")}>关闭</button></div>}
    {msg && <div className="mt-4 rounded-lg bg-green-50 p-3 text-sm text-green-700">{msg}</div>}
    <div className="mt-5 grid grid-cols-4 gap-4">
      <MetricCard label="正品 SKU 数" value={String(counts.goods)} />
      <MetricCard label="组合套装" value={String(counts.bundles)} />
      <MetricCard label="耗材种类" value={String(counts.consumable)} />
      <MetricCard label="最新吉客云快照" value={inv?.snapshotAt ? new Date(inv.snapshotAt).toLocaleString("zh-CN") : "—"} />
    </div>

    <div className="mt-6 flex gap-2 border-b border-gray-200">
      {([["catalog", "货品档案"], ["bundles", `组合套装 ${counts.bundles}`], ["consumables", "耗材库"]] as const).map(([key, label]) => (
        <button key={key} className={`px-4 py-2 text-sm ${tab === key ? "border-b-2 border-blue-600 font-medium text-blue-600" : "text-gray-500"}`} onClick={() => setTab(key)}>{label}</button>
      ))}
    </div>
    <p className="mt-2 text-xs text-gray-400">说明：条码允许正品与耗材相同，系统按「类型 + 编码」区分，条码不作主键；组合套装（ES 开头，A+B+C 等不同商品不同数量的组合）已单独归纳到「组合套装」页签；耗材编码建议 HC-CH- + 条形码（同品多耗材加后缀 -BX/-LB/-CT）。</p>

    {tab !== "consumables" && bulkBar}

    {tab === "catalog" && <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="text-sm font-medium text-gray-700">统一货品档案</h2><p className="mt-1 text-xs text-gray-400">正品（品）继续走吉客云流程；耗材（耗）在本系统维护采购、收货与库存。</p></div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg border border-gray-200 p-0.5 text-xs">{([["all", `全部 ${catalogRows.length}`], ["goods", `正品 ${counts.goods - counts.bundles}`], ["consumable", `耗材 ${counts.consumable}`]] as const).map(([key, label]) => (<button key={key} onClick={() => setKind(key)} className={`rounded-md px-2.5 py-1.5 ${kind === key ? "bg-blue-50 font-medium text-blue-600" : "text-gray-500 hover:bg-gray-50"}`}>{label}</button>))}</div>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索编码、条码或名称" className="w-56 rounded-lg border px-3 py-1.5 text-sm" />
        </div>
      </div>

      {editing !== null && <form onSubmit={saveProduct} className="mt-4 grid grid-cols-6 gap-2 rounded-lg bg-blue-50 p-3 text-xs">
        <input placeholder="吉客云 SKU ID（可空）" value={productForm.jackyun_sku_id} onChange={(e) => setProductForm({ ...productForm, jackyun_sku_id: e.target.value })} className="rounded border px-2 py-1.5" />
        <input required placeholder="SKU 编码" value={productForm.sku_code} onChange={(e) => setProductForm({ ...productForm, sku_code: e.target.value })} className="rounded border px-2 py-1.5" />
        <select value={productForm.product_type} onChange={(e) => setProductForm({ ...productForm, product_type: e.target.value })} className="rounded border px-2 py-1.5"><option value="single">单品</option><option value="bundle">套装</option><option value="virtual_bundle">虚拟组合套装</option></select>
        <input required placeholder="货品名称/规格" value={productForm.sku_name} onChange={(e) => setProductForm({ ...productForm, sku_name: e.target.value })} className="rounded border px-2 py-1.5" />
        <input placeholder="条码" value={productForm.barcode} onChange={(e) => setProductForm({ ...productForm, barcode: e.target.value })} className="rounded border px-2 py-1.5" />
        <input placeholder="单位" value={productForm.unit} onChange={(e) => setProductForm({ ...productForm, unit: e.target.value })} className="rounded border px-2 py-1.5" />
        <input placeholder="售价" value={productForm.sale_price} onChange={(e) => setProductForm({ ...productForm, sale_price: e.target.value })} className="rounded border px-2 py-1.5" />
        <input placeholder="默认成本" value={productForm.default_cost} onChange={(e) => setProductForm({ ...productForm, default_cost: e.target.value })} className="rounded border px-2 py-1.5" />
        <select value={productForm.cost_mode} onChange={(e) => setProductForm({ ...productForm, cost_mode: e.target.value })} className="rounded border px-2 py-1.5"><option value="fixed">固定成本</option><option value="dynamic">动态成本</option></select>
        <input placeholder="容差(如0.02)" value={productForm.cost_tolerance_pct} onChange={(e) => setProductForm({ ...productForm, cost_tolerance_pct: e.target.value })} className="rounded border px-2 py-1.5" />
        <input placeholder="税务代码（税收分类编码）" title="开票用商品和服务税收分类编码（19 位，兼容旧 10 位简称）" value={productForm.tax_code} onChange={(e) => setProductForm({ ...productForm, tax_code: e.target.value })} className="rounded border px-2 py-1.5 font-mono" />
        <input placeholder="品类（如 咖啡豆/饼干）" title="货品品类，来自吉客云同步，可本地修改" value={productForm.goods_category} onChange={(e) => setProductForm({ ...productForm, goods_category: e.target.value })} className="rounded border px-2 py-1.5" />
        <select value={productForm.status} onChange={(e) => setProductForm({ ...productForm, status: e.target.value })} className="rounded border px-2 py-1.5"><option value="active">启用</option><option value="inactive">停用</option></select>
        <div className="flex gap-2"><button type="submit" className="rounded bg-blue-600 px-3 py-1.5 text-white">保存</button><button type="button" onClick={() => setEditing(null)} className="rounded border px-3 py-1.5">取消</button></div>
      </form>}

      {bulkBar}
      <CatalogTable rows={catalogRows} onEditConsumable={openConsumableEditor} onEditProduct={startEditProduct} onToggleCostMode={saveCostMode} selected={selected} onToggleRow={toggleRow} onToggleAll={() => toggleAll(catalogRows)} />
    </section>}

    {tab === "bundles" && <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-sm font-medium text-gray-700">组合套装（虚拟）</h2><p className="mt-1 text-xs text-gray-400">ES 开头的虚拟组合套装：不同商品、不同数量的组合（如 A+B+C）；库存仍归吉客云管理，此处仅做档案归纳，不出现在采购单 SKU 候选中。</p></div><input value={bundleSearch} onChange={(e) => setBundleSearch(e.target.value)} placeholder="搜索编码、条码或名称" className="w-56 rounded-lg border px-3 py-1.5 text-sm" /></div>
      <CatalogTable rows={bundleRows} onEditConsumable={openConsumableEditor} onEditProduct={startEditProduct} onToggleCostMode={saveCostMode} selected={selected} onToggleRow={toggleRow} onToggleAll={() => toggleAll(bundleRows)} />
    </section>}

    {tab === "consumables" && <><ConsumableWorkbench rows={consumables} products={products} reload={load} notify={setMsg} fail={setErr} onImport={importWorkbook} onEdit={openConsumableEditorById} onCreate={() => openConsumableEditor()} />
    <section className="mt-4 rounded-xl border border-gray-200 bg-white p-4"><h2 className="text-sm font-medium text-gray-700">耗材使用进度</h2><p className="mt-1 text-xs text-gray-400">使用率 = 已使用量 ÷ 采购量。</p><table className="mt-4 w-full text-sm"><thead className="text-left text-xs text-gray-500"><tr><th className="py-2">代码</th><th className="py-2">名称</th><th className="py-2 text-right">采购量</th><th className="py-2 text-right">已使用</th><th className="py-2 text-right">剩余</th><th className="py-2">进度</th></tr></thead><tbody className="divide-y divide-gray-100">{consumables.map((row) => { const rate = Math.min(Math.max(Number(row.usageRate || 0), 0), 1); return <tr key={row.id}><td className="py-2 font-mono text-xs">{row.code}</td><td className="py-2">{row.name}</td><td className="py-2 text-right">{qty(row.purchasedQty)} {row.unit}</td><td className="py-2 text-right">{qty(row.usedQty)} {row.unit}</td><td className="py-2 text-right">{qty(row.stockQty)} {row.unit}</td><td className="w-64 py-2"><div className="flex items-center gap-2"><div className="h-2 flex-1 rounded bg-gray-100"><div className="h-2 rounded bg-blue-600" style={{ width: `${rate * 100}%` }} /></div><span className="w-12 text-right text-xs text-gray-500">{(rate * 100).toFixed(1)}%</span></div></td></tr>; })}</tbody></table></section>
    <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700"><span>耗材采购的 1688 订单与正常货品单同列在采购工作台跟进。</span><a href="/purchase/workbench?view=orders" className="ml-2 font-medium underline">前往订单视图</a></div></>}

    {consumableEditor !== null && <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) setConsumableEditor(null); }}>
      <form onSubmit={saveConsumable} className="max-h-[92vh] w-full max-w-2xl overflow-auto rounded-2xl bg-white p-5 shadow-2xl">
        <div className="flex items-start justify-between"><div><h3 className="text-base font-semibold text-slate-800">{consumableEditor === "new" ? "新建耗材档案" : "编辑耗材档案"}</h3><p className="mt-1 text-xs text-slate-400">编码首次手填（建议 HC-CH- + 条形码）；库存不在此修改，请走采购收货或库存流水。</p></div><button type="button" onClick={() => setConsumableEditor(null)} className="text-xl leading-none text-slate-300 hover:text-slate-500">×</button></div>
        <div className="mt-5 grid grid-cols-2 gap-3 text-xs">
          <label className="col-span-2">耗材编码<span className="text-red-500">*</span><div className="mt-1.5 flex gap-2"><input required value={consumableDraft.code} onChange={(e) => setConsumableDraft({ ...consumableDraft, code: e.target.value })} placeholder="如 HC-CH-2020240528003（多耗材加 -BX/-LB/-CT 后缀）" className="h-9 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /><button type="button" disabled={!consumableDraft.barcode.trim()} onClick={() => setConsumableDraft((d) => ({ ...d, code: `HC-CH-${d.barcode.trim()}` }))} className="h-9 shrink-0 rounded-lg border border-slate-200 px-3 text-slate-600 hover:border-amber-300 hover:text-amber-600 disabled:opacity-40">HC-CH-+条码</button></div></label>
          <label className="col-span-2">耗材名称<span className="text-red-500">*</span><input required value={consumableDraft.name} onChange={(e) => setConsumableDraft({ ...consumableDraft, name: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>条形码<input value={consumableDraft.barcode} onChange={(e) => setConsumableDraft({ ...consumableDraft, barcode: e.target.value })} placeholder="可与正品条码相同" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>单位<input required value={consumableDraft.unit} onChange={(e) => setConsumableDraft({ ...consumableDraft, unit: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>分类<input value={consumableDraft.category} onChange={(e) => setConsumableDraft({ ...consumableDraft, category: e.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label>税务代码<input value={consumableDraft.tax_code} onChange={(e) => setConsumableDraft({ ...consumableDraft, tax_code: e.target.value })} placeholder="税收分类编码（开票用）" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 font-mono outline-none focus:border-amber-400" /></label>
          <label>安全库存<input value={consumableDraft.min_stock_qty} onChange={(e) => setConsumableDraft({ ...consumableDraft, min_stock_qty: e.target.value })} placeholder="0 = 不预警" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
          <label className="col-span-2">参考采购单价<input value={consumableDraft.purchase_unit_cost} onChange={(e) => setConsumableDraft({ ...consumableDraft, purchase_unit_cost: e.target.value })} placeholder="可空，收货时按实付更新" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 outline-none focus:border-amber-400" /></label>
        </div>
        <div className="mt-4 rounded-xl border border-slate-200 p-3">
          <div className="flex items-center justify-between"><div><h4 className="text-xs font-semibold text-slate-700">关联正品</h4><p className="mt-0.5 text-[10px] text-slate-400">一个耗材可关联多个正品 SKU（多对多），保存后永久生效。</p></div><span className="text-[10px] text-slate-400">已选 {consumableDraft.sku_ids.length}</span></div>
          {linkedRefs.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{linkedRefs.map((ref) => <span key={ref.skuId} className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10px] text-amber-700"><span className="font-mono">{ref.skuCode}</span> {ref.skuName}<button type="button" onClick={() => toggleLinkedSku(ref.skuId)} className="text-amber-400 hover:text-red-500">×</button></span>)}</div>}
          <input value={skuSearch} onChange={(e) => setSkuSearch(e.target.value)} placeholder="搜索正品 SKU 编码或名称" className="mt-2 h-8 w-full rounded-lg border border-slate-200 px-3 text-xs outline-none focus:border-amber-400" />
          <div className="mt-2 max-h-40 divide-y divide-slate-100 overflow-auto rounded-lg border border-slate-100">{skuCandidates.map((p) => { const checked = consumableDraft.sku_ids.includes(p.id); return <button type="button" key={p.id} onClick={() => toggleLinkedSku(p.id)} className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-[11px] ${checked ? "bg-amber-50" : "hover:bg-slate-50"}`}><span className="min-w-0 truncate text-slate-600"><span className="font-mono text-indigo-500">{p.skuCode}</span> · {p.skuName || p.goodsName}</span><span className={`ml-2 shrink-0 ${checked ? "text-amber-600" : "text-slate-300"}`}>{checked ? "已选 ✓" : "选择"}</span></button>; })}{!skuCandidates.length && <div className="px-3 py-3 text-[11px] text-slate-400">没有匹配的正品 SKU</div>}</div>
        </div>
        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setConsumableEditor(null)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button><button type="submit" disabled={savingConsumable} className="rounded-lg bg-amber-500 px-4 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50">{savingConsumable ? "保存中…" : "保存档案"}</button></div>
      </form>
    </div>}
  </div>;
}
