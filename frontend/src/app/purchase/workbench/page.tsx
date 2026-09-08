"use client";

import NextLink from "next/link";
import { useSearchParams } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ComponentProps, type ReactNode } from "react";
import { WORKBENCH_VIEWS, parseWorkbenchView, workbenchHref, type WorkbenchView } from "@/lib/workbench-navigation";
import { WorkspaceModule } from "./workspace-modules";
import { NewPurchaseModal } from "./new-purchase-modal";
import { RelatedRecordsPanel } from "./related-records-panel";
import { SearchableSelect } from "./searchable-select";
import { QuickTriage, canonicalChainOrderId } from "./quick-triage";
import { newRequestKey } from "@/lib/request-key";
import {
  authenticatedFetch,
  consumablesApi,
  dashboardApi,
  procurementBoardApi,
  procurementChainApi,
  procurementWorkbenchApi,
  skuMatchingApi,
  type BoardOverview,
  type CatalogSkuRow,
  type ConsumableMappingRow,
  type ConsumablePurchaseItem,
  type ConsumablePurchaseRow,
  type ConsumableRow,
  type InboundMatchSummary,
  type InvoiceReconciliation,
  type PendingAllocation,
  type SkuCandidate,
  type SyncJobRow,
  type ChainOrderRow,
  type ChainLinkCandidate,
  type ChainOverview,
  type ChainPurchaseOrder,
  type PendingLink,
  type WorkbenchOrder as WorkbenchOrderRow,
  type XrefPreview,
  type XrefApplyResult,
  type WorkbenchDetail,
  type WorkbenchFunnel,
  type WorkbenchOrderItem,
  type WorkbenchTodo,
  type WorkbenchStepState,
  type WorkbenchSupplierDetail,
  type WorkbenchSupplierSummary,
  type WorkbenchSummary,
} from "@/lib/api";

type ViewMode = WorkbenchView;
type FilterKey = "all" | "refine" | "po" | "inbound" | "invoice" | "exception" | "done";
type ChainFilter = "all" | "gap" | "full";

/** 采购单状态流转（与 /purchase 执行中心一致） */
const PO_STATUS: Record<string, string> = {
  pending_refine: "待完善", confirmed: "已确认采购内容", jackyun_linked: "已关联吉客云采购单",
  producing: "待发货/生产中", shipped: "已发货", arrived: "已到货", inbound: "已入库", done: "完成",
};
const NEXT_STATUS: Record<string, string> = {
  jackyun_linked: "producing", producing: "shipped",
  shipped: "arrived", arrived: "inbound", inbound: "done",
};
/** 附加费用类型（不伪造成 SKU） */
const EXPENSE_LABEL: Record<string, string> = {
  pack: "包装", processing: "加工", plate: "制版", mold: "模具", freight: "运费", testing: "检测", other: "其他",
};

/** 链路 7 环节：① 订单 → ② SKU → ③ 采购单 → ④ 入库 → ⑤ 发票 → ⑥ 付款 → ⑦ 认证 */
const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥", "⑦"];
/** 维度色：1688 导出 / 本平台采购中心 / 吉客云 / 税务 */
const DIM_DOT: Record<string, string> = {
  "1688": "bg-indigo-500",
  purchase: "bg-violet-500",
  jackyun: "bg-teal-500",
  tax: "bg-amber-500",
};
/** 环节补数据入口：站内跳导入页，或切到工作台其它视图 */
const STAGE_ACTION: Record<string, { href?: string; view?: ViewMode; act: string }> = {
  order: { href: "/alibaba1688-import", act: "导入 1688 订单" },
  sku: { view: "matching", act: "配置 SKU 匹配" },
  jackyunPo: { href: "/jackyun-import", act: "导入吉客云入库单" },
  inbound: { href: "/jackyun-import", act: "导入入库单" },
  invoice: { href: "/tax-invoices", act: "导入发票清单" },
  paid: { href: "/jackyun-import", act: "导入结算单" },
  verified: { href: "/tax-invoices", act: "查看发票清单" },
};
type DatePreset = "all" | "today" | "yesterday" | "week" | "month" | "custom";
type IconName =
  | "dashboard"
  | "sales"
  | "box"
  | "purchase"
  | "orders"
  | "calendar"
  | "users"
  | "wallet"
  | "reconcile"
  | "receipt"
  | "cloud"
  | "chart"
  | "settings"
  | "import"
  | "sync"
  | "magic"
  | "refresh"
  | "download"
  | "plus"
  | "filter"
  | "search"
  | "copy"
  | "chevron";

type AllocationRow = {
  id?: number;
  skuId?: number | null;
  goodsName?: string;
  skuCode?: string;
  quantity?: number;
  unitPrice?: number;
  amount?: number;
  note?: string;
  /** 来源入库单 ID：入库单明细自动反填的行有值；人工新增行为 null（归入手工补录区） */
  inboundDocumentId?: number | null;
};
type EditorConsumableDraft = { consumableId: number; quantity: string };
type PendingConsumableUpdate = { documentId: number; allocationId: number; draft: EditorConsumableDraft | null };
type ExpenseRow = { id?: number; expenseType?: string; amount?: number | null };
type SettlementRow = { paidAmount?: number | null; amount?: number | null };
type InboundRow = {
  linkId?: number | null;
  documentId?: number;
  targetId?: number;
  goodsdocNo?: string;
  warehouseName?: string;
  supplier?: string;
  amount?: number | null;
  itemAmount?: number | null;
  itemCount?: number;
  date?: string | null;
  status?: string;
  matchMethod?: string;
  note?: string;
  consumableUsageDecided?: boolean;
  consumableUsageEnabled?: boolean | null;
  consumableUsageItems?: Array<{ consumableId: number; consumableCode: string; consumableName: string; unit: string; quantity: string }>;
};


const FILTERS: { key: FilterKey; label: string; summaryKey?: keyof WorkbenchSummary }[] = [
  { key: "all", label: "全部" },
  { key: "refine", label: "待完善", summaryKey: "pendingSku" },
  { key: "po", label: "待生成采购单", summaryKey: "pendingPo" },
  { key: "inbound", label: "待入库", summaryKey: "pendingInbound" },
  { key: "invoice", label: "待开发票", summaryKey: "pendingInvoice" },
  { key: "exception", label: "异常", summaryKey: "exceptionCount" },
  { key: "done", label: "已完成" },
];

type ChannelKey = "all" | "1688" | "pdd" | "taobao" | "other";

/** 采购渠道：1688 / 拼多多 / 淘宝 / 其他。 */
const CHANNELS: Record<string, { key: ChannelKey; label: string; dot: string; badge: string }> = {
  "1688": { key: "1688", label: "1688", dot: "bg-indigo-500", badge: "border-indigo-200 bg-indigo-50 text-indigo-600" },
  pdd: { key: "pdd", label: "拼多多", dot: "bg-red-500", badge: "border-red-200 bg-red-50 text-red-600" },
  taobao: { key: "taobao", label: "淘宝", dot: "bg-orange-500", badge: "border-orange-200 bg-orange-50 text-orange-600" },
  other: { key: "other", label: "其他", dot: "bg-slate-500", badge: "border-slate-200 bg-slate-50 text-slate-600" },
};

function channelOf(value: string | null | undefined): ChannelKey {
  const raw = (value || "").trim().toLowerCase();
  if (/(pdd|拼多多|duoduo)/i.test(raw)) return "pdd";
  if (/(taobao|淘宝|tmall|天猫)/i.test(raw)) return "taobao";
  if (raw === "1688" || /阿里/.test(raw)) return "1688";
  return "other";
}

function PlatformBadge({ value, className }: { value: string | null | undefined; className?: string }) {
  const meta = CHANNELS[channelOf(value)];
  return (
    <span className={cx("inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[8.5px] font-medium leading-none", meta.badge, className)}>
      {meta.label}
    </span>
  );
}

/** 订单类型标签：正品 / 耗材（包材）。缺省按 goods（正品）处理，避免旧后端未下发时空白。 */
const ORDER_KIND_META: Record<string, { label: string; badge: string }> = {
  goods: { label: "正品", badge: "border-slate-200 bg-white text-slate-500" },
  consumable: { label: "耗材", badge: "border-amber-200 bg-amber-50 text-amber-600" },
};
function OrderKindTag({ value, className }: { value?: string | null; className?: string }) {
  const meta = ORDER_KIND_META[value === "consumable" ? "consumable" : "goods"];
  return (
    <span className={cx("inline-flex shrink-0 items-center rounded border px-1.5 py-0.5 text-[8.5px] font-medium leading-none", meta.badge, className)}>
      {meta.label}
    </span>
  );
}

function cx(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(" ");
}

function Link({ href, ...props }: Omit<ComponentProps<typeof NextLink>, "href"> & { href: string }) {
  const params = useSearchParams();
  let destination = workbenchHref(href);
  if (destination.startsWith("/purchase/workbench") && params.get("order")) {
    const url = new URL(destination, "http://workbench.local");
    if (!url.searchParams.has("order")) url.searchParams.set("order", params.get("order")!);
    destination = url.pathname + url.search;
  }
  return <NextLink href={destination} {...props} />;
}

function parseDate(value: string | null | undefined) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function pad(value: number) {
  return String(value).padStart(2, "0");
}

function inputDate(value: Date) {
  return value.getFullYear() + "-" + pad(value.getMonth() + 1) + "-" + pad(value.getDate());
}


function fmtMoney(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  return "¥" + value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtDate(value: string | null | undefined) {
  const date = parseDate(value);
  if (!date) return "—";
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
}

function fmtDateTime(value: string | null | undefined) {
  const date = parseDate(value);
  if (!date) return "未记录";
  return fmtDate(value) + " " + pad(date.getHours()) + ":" + pad(date.getMinutes()) + ":" + pad(date.getSeconds());
}

// 1688 源单已关闭/取消：这类订单不再按正常待办推进，界面必须显式标为「已关闭」，
// 而不是沿用 firstUndone 让它看起来还停在某个正常环节上。
function isOrderClosed(orderStatus?: string | null) {
  const value = (orderStatus || "").trim();
  if (!value) return false;
  return /关闭|已取消|交易取消|closed|canceled|cancelled/i.test(value);
}

type StatusInput = {
  orderStatus?: string | null;
  hasException?: boolean;
  firstUndone?: string | null;
  firstUndoneLabel?: string;
  stepStates?: Record<string, WorkbenchStepState>;
  /** 收尾环节的具体卡点（后端统一算好，避免前后端各算一套） */
  closeoutStage?: string | null;
};

/** 收尾子状态 → 状态标签文案 */
const CLOSEOUT_LABELS: Record<string, string> = {
  awaiting_inbound: "待入库",
  awaiting_invoice: "待开发票",
  awaiting_payment: "待付款",
  awaiting_verification: "待认证",
};

const PURCHASE_STEP_ORDER = ["content", "sku", "jackyun_po", "inbound", "invoice"] as const;
const PURCHASE_STEP_LABELS: Record<string, string> = {
  content: "待确认采购内容",
  sku: "待匹配SKU",
  jackyun_po: "待生成采购单",
  inbound: "待入库",
  invoice: "待发票",
  closeout: "待收尾(发票/付款/认证)",
};

function statusLabel(input: StatusInput) {
  if (isOrderClosed(input.orderStatus)) return "已关闭";
  if (input.hasException) return "异常";
  // 优先用后端给的最早未完成步骤；否则在 stepStates 里找明确未完成的采购推进步骤
  //（缺键=该视图不含此维度，不算未完成，避免 workbench 5 步视图误判成「待入库」）。
  let stepKey = input.firstUndone ?? null;
  if (!stepKey && input.stepStates) {
    stepKey =
      PURCHASE_STEP_ORDER.find((key) => {
        const state = input.stepStates?.[key];
        return state !== undefined && !state.done;
      }) ?? null;
    // workbench 视图把收尾合并为 closeout（入库/发票/付款/认证）
    if (!stepKey && input.stepStates.closeout && !input.stepStates.closeout.done) {
      stepKey = "closeout";
    }
  }
  if (!stepKey) return "已完成";
  // 收尾环节拆到具体卡点：光写「待收尾」看不出是缺票还是缺认证
  if (stepKey === "closeout" && input.closeoutStage) {
    const sub = CLOSEOUT_LABELS[input.closeoutStage];
    if (sub) return sub;
  }
  return PURCHASE_STEP_LABELS[stepKey] ?? input.firstUndoneLabel ?? "待处理";
}

function statusClass(label: string) {
  if (label === "已关闭") return "bg-slate-100 text-slate-500";
  if (label === "已完成") return "bg-emerald-50 text-emerald-600";
  if (label === "异常") return "bg-red-50 text-red-600";
  if (label === "待确认采购内容") return "bg-orange-50 text-orange-600";
  if (label === "待匹配SKU") return "bg-amber-50 text-amber-600";
  if (label === "待生成采购单") return "bg-blue-50 text-blue-600";
  if (label === "待入库") return "bg-emerald-50 text-emerald-600";
  if (label === "待收票" || label === "待开发票") return "bg-violet-50 text-violet-600";
  if (label === "待认证") return "bg-sky-50 text-sky-600";
  if (label === "待付款") return "bg-rose-50 text-rose-600";
  if (label === "待发票" || label === "待收尾(发票/付款/认证)") return "bg-violet-50 text-violet-600";
  return "bg-orange-50 text-orange-600";
}

function groupOrders(items: WorkbenchOrderItem[]) {
  const now = new Date();
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const groups: Record<string, WorkbenchOrderItem[]> = { today: [], yesterday: [], earlier: [], unknown: [] };
  for (const item of items) {
    const date = parseDate(item.orderDate);
    if (!date) groups.unknown.push(item);
    else if (date.getTime() >= todayStart) groups.today.push(item);
    else if (date.getTime() >= todayStart - 86400000) groups.yesterday.push(item);
    else groups.earlier.push(item);
  }
  const weekdays = ["日", "一", "二", "三", "四", "五", "六"];
  const labelFor = (key: string, rows: WorkbenchOrderItem[]) => {
    if (key === "today") return "今天　" + fmtDate(rows[0]?.orderDate).slice(5) + "（周" + weekdays[now.getDay()] + "）";
    if (key === "yesterday") return "昨天　" + fmtDate(rows[0]?.orderDate).slice(5);
    if (key === "earlier") return "更早采购";
    return "未记录时间";
  };
  return ["today", "yesterday", "earlier", "unknown"]
    .filter((key) => groups[key].length > 0)
    .map((key) => ({ key, label: labelFor(key, groups[key]), items: groups[key] }));
}

function exportOrders(items: WorkbenchOrderItem[]) {
  if (typeof window === "undefined" || items.length === 0) return;
  const rows = items.map((item) => [
    fmtDateTime(item.orderDate), CHANNELS[channelOf(item.platform)].label, item.orderKind === "consumable" ? "耗材" : "正品", item.orderNo, item.supplier, item.amount ?? "",
    item.invoiceStatus === "done" ? "已开票" : (item.invoiceOutstanding ?? "") === "" ? "" : `未开票 ${item.invoiceOutstanding ?? 0}`,
    statusLabel(item), item.orderStatus,
  ]);
  const csvRows = [["采购时间", "渠道", "类型", "订单号", "供应商", "订单金额", "开票（未开票）", "当前状态", "订单状态"], ...rows];
  const csv = csvRows
    .map((row) => row.map((value) => '"' + String(value).replaceAll('"', '""') + '"').join(","))
    .join("\n");
  const url = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "采购工作台-" + new Date().toISOString().slice(0, 10) + ".csv";
  link.click();
  URL.revokeObjectURL(url);
}

export default function PurchaseWorkbenchPage() {
  const searchParams = useSearchParams();
  const initialView: ViewMode = (() => {
    const v = searchParams.get("view");
    return parseWorkbenchView(v);
  })();
  const initialOrderId = (() => {
    const raw = searchParams.get("order");
    if (!raw) return null;
    const n = Number(raw);
    return Number.isInteger(n) && n !== 0 ? n : null;
  })();
  const [view, setView] = useState<ViewMode>(initialView);
  const [newOrderOpen, setNewOrderOpen] = useState(false);
  const [page, setPage] = useState(1);
  const pageSize = 20;
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null);
  const [orders, setOrders] = useState<WorkbenchOrderItem[]>([]);
  const [outstandingTotal, setOutstandingTotal] = useState(0);
  const [total, setTotal] = useState(0);
  const [suppliers, setSuppliers] = useState<WorkbenchSupplierSummary[]>([]);
  const [selectedOrderId, setSelectedOrderId] = useState<number | null>(initialOrderId);
  const requestedOrder = useRef<number | null>(initialOrderId);
  const [selectedSupplierName, setSelectedSupplierName] = useState<string | null>(null);
  const [orderDetail, setOrderDetail] = useState<WorkbenchDetail | null>(null);
  const [supplierDetail, setSupplierDetail] = useState<WorkbenchSupplierDetail | null>(null);
  const [statusFilter, setStatusFilter] = useState<FilterKey>("all");
  const [channelFilter, setChannelFilter] = useState<ChannelKey>("all");
  const [query, setQuery] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [supplierQuery, setSupplierQuery] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [datePreset, setDatePreset] = useState<DatePreset>("all");
  const [showFilter, setShowFilter] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [chainOverview, setChainOverview] = useState<ChainOverview | null>(null);
  const [chainOrders, setChainOrders] = useState<ChainOrderRow[]>([]);
  const [chainTotal, setChainTotal] = useState(0);
  const [chainPending, setChainPending] = useState<PendingLink[]>([]);
  const [chainFilter, setChainFilter] = useState<ChainFilter>("all");
  const [chainBusy, setChainBusy] = useState(false);
  const [inboundMatch, setInboundMatch] = useState<InboundMatchSummary | null>(null);
  const [pendingAlloc, setPendingAlloc] = useState<PendingAllocation[]>([]);
  const [matchingBusy, setMatchingBusy] = useState(false);
  const [boardOverview, setBoardOverview] = useState<BoardOverview | null>(null);
  const [funnel, setFunnel] = useState<WorkbenchFunnel | null>(null);
  const [todos, setTodos] = useState<WorkbenchTodo | null>(null);

  useEffect(() => {
    setView(parseWorkbenchView(searchParams.get("view")));
    const orderId = Number(searchParams.get("order"));
    if (Number.isInteger(orderId) && orderId !== 0) { requestedOrder.current = orderId; setSelectedOrderId(orderId); }
    if (searchParams.get("action") === "new") setNewOrderOpen(true);
  }, [searchParams]);

  // 顶部固定栏高度写入 CSS 变量，sticky 详情栏据此定位，避免遮挡或漏缝
  useEffect(() => {
    const el = document.querySelector<HTMLElement>("main > header");
    if (!el) return;
    const update = () => document.documentElement.style.setProperty("--wb-header-h", `${el.offsetHeight}px`);
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, [view]);

  function changeView(next: ViewMode) {
    setView(next);
    const params = new URLSearchParams(window.location.search);
    params.set("view", next);
    params.delete("action");
    if (selectedOrderId !== null) params.set("order", String(selectedOrderId));
    window.history.pushState(null, "", `/purchase/workbench?${params}`);
  }

  function selectOrder(orderId: number) {
    requestedOrder.current = orderId;
    setSelectedOrderId(orderId);
    const params = new URLSearchParams(window.location.search);
    params.set("view", "orders");
    params.set("order", String(orderId));
    params.delete("action");
    window.history.replaceState(null, "", `/purchase/workbench?${params}`);
    setView("orders");
  }

  useEffect(() => { setPage(1); }, [statusFilter, startDate, endDate, query]);

  const loadSummary = useCallback(async () => {
    try { setSummary(await procurementWorkbenchApi.summary()); } catch { setSummary(null); }
  }, []);





  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError("");
    if (startDate && endDate && startDate > endDate) {
      setOrders([]);
      setTotal(0);
      setError("开始日期不能晚于结束日期");
      setLoading(false);
      return;
    }
    try {
      const result = await procurementWorkbenchApi.orders({
        status: statusFilter === "all" ? "all" : statusFilter === "refine" ? "sku" : statusFilter,
        q: query || undefined,
        page,
        pageSize,
        startDate: startDate || undefined,
        endDate: endDate || undefined,
      });
      const items = result.groups.flatMap((group) => group.items);
      setOrders(items);
      setTotal(result.total);
      setOutstandingTotal(result.invoiceOutstandingTotal ?? 0);
      const requested = requestedOrder.current;
      requestedOrder.current = null;
      setSelectedOrderId((current) => requested ?? items.find(item => item.orderId === current)?.orderId ?? items[0]?.orderId ?? null);
    } catch {
      setOrders([]);
      setTotal(0);
      setOutstandingTotal(0);
      setError("订单数据加载失败，请刷新后重试");
    } finally {
      setLoading(false);
    }
  }, [endDate, query, startDate, statusFilter, page]);

  const loadSuppliers = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await procurementWorkbenchApi.suppliers(200);
      setSuppliers(result.items);
      setSelectedSupplierName((current) => {
        if (result.items.length === 0) return null;
        return current && result.items.some((item) => item.supplierName === current)
          ? current
          : result.items[0].supplierName;
      });
    } catch {
      setSuppliers([]);
      setSelectedSupplierName(null);
      setError("供应商数据加载失败，请刷新后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadMatching = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [match, pending] = await Promise.all([
        skuMatchingApi.inboundSummary(),
        skuMatchingApi.pending(100),
      ]);
      setInboundMatch(match);
      setPendingAlloc(pending);
    } catch {
      setError("匹配数据加载失败，请刷新后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  const runInboundAuto = useCallback(async () => {
    setMatchingBusy(true);
    setError("");
    try {
      const result = await skuMatchingApi.runInboundAuto();
      const s = result.stats;
      setNotice(`入库自动匹配完成：共 ${s.total} 条，金额校验通过 ${s.price_ok}，金额异常 ${s.price_mismatch}，人工结果保留 ${s.manual ?? 0}`);
      await loadMatching();
    } catch {
      setError("自动匹配执行失败，请重试");
    } finally {
      setMatchingBusy(false);
    }
  }, [loadMatching]);

  const loadChain = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [overview, orderResult, pendingResult] = await Promise.all([
        procurementChainApi.overview(),
        procurementChainApi.orders(500),
        procurementChainApi.pending(),
      ]);
      setChainOverview(overview);
      setChainOrders(orderResult.items);
      setChainTotal(orderResult.total);
      setChainPending(pendingResult.items);
    } catch {
      setChainOverview(null);
      setChainOrders([]);
      setChainPending([]);
      setError("链路数据加载失败，请刷新后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadBoard = useCallback(async () => {
    try {
      setBoardOverview(await procurementBoardApi.overview());
    } catch {
      // 看板数据为增强展示（付款率环），失败不阻塞工作台主体。
      setBoardOverview(null);
    }
  }, []);

  const loadFunnelTodos = useCallback(async () => {
    try {
      const [f, t] = await Promise.all([
        procurementWorkbenchApi.funnel(),
        procurementWorkbenchApi.todos(),
      ]);
      setFunnel(f);
      setTodos(t);
    } catch {
      // 漏斗/待办为辅助指标，失败不阻塞主表。
      setFunnel(null);
      setTodos(null);
    }
  }, []);

  async function confirmChainLink(kind: string, linkId: number) {
    setChainBusy(true);
    try {
      if (kind === "invoice") await procurementChainApi.confirmInvoiceLink(linkId);
      else await procurementChainApi.confirmLink(linkId);
      setNotice("关联已确认");
      await loadChain();
    } catch (caught) {
      setNotice("确认失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  async function rejectChainLink(kind: string, linkId: number) {
    setChainBusy(true);
    try {
      if (kind === "invoice") await procurementChainApi.deleteInvoiceLink(linkId);
      else await procurementChainApi.deleteLink(linkId);
      setNotice("关联建议已拒绝");
      await loadChain();
    } catch (caught) {
      setNotice("拒绝失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  useEffect(() => {
    void loadSummary();
    void loadBoard();
    void loadFunnelTodos();
  }, [loadBoard, loadFunnelTodos, loadSummary]);

  useEffect(() => {
    if (view === "orders") void loadOrders();
    else if (view === "chain") void loadChain();
    else if (view === "matching") void loadMatching();
    else if (view === "suppliers") void loadSuppliers();
  }, [loadChain, loadMatching, loadOrders, loadSuppliers, view]);

  useEffect(() => {
    if (view !== "orders" || selectedOrderId === null) {
      setOrderDetail(null);
      return;
    }
    let cancelled = false;
    setOrderDetail(null);
    setDetailLoading(true);
    procurementWorkbenchApi.workbench(selectedOrderId)
      .then((detail) => { if (!cancelled) setOrderDetail(detail); })
      .catch(() => { if (!cancelled) setOrderDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedOrderId, view]);

  useEffect(() => {
    if (view !== "suppliers" || !selectedSupplierName) {
      setSupplierDetail(null);
      return;
    }
    let cancelled = false;
    setSupplierDetail(null);
    setDetailLoading(true);
    procurementWorkbenchApi.supplierDetail(selectedSupplierName)
      .then((detail) => { if (!cancelled) setSupplierDetail(detail); })
      .catch(() => { if (!cancelled) setSupplierDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [selectedSupplierName, view]);

  async function runMatch() {
    setBusyAction("match");
    try {
      const result = await procurementChainApi.runMatch();
      const autoConfirm = result.autoConfirm as { confirmedChain?: number; confirmedInvoices?: number; resolvedInboundUsage?: number } | undefined;
      const created = Number(result.created ?? 0);
      const confirmed = Number(autoConfirm?.confirmedChain ?? 0) + Number(autoConfirm?.confirmedInvoices ?? 0);
      const parts = [`自动化完成：新建 ${created} 条关联`];
      if (confirmed > 0) parts.push(`确认 ${confirmed} 条订单/发票关联`);
      if (Number(autoConfirm?.resolvedInboundUsage ?? 0) > 0) parts.push(`补齐 ${autoConfirm?.resolvedInboundUsage} 条入库耗材决策`);
      setNotice(parts.join("，"));
      await Promise.all([
        loadSummary(),
        view === "orders" ? loadOrders() : view === "chain" ? loadChain() : view === "matching" ? loadMatching() : loadSuppliers(),
      ]);
    } catch (caught) {
      setNotice("生成匹配建议失败：" + String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function runPrelink(auto: boolean) {
    setBusyAction("prelink");
    try {
      const result = await procurementChainApi.prelink(auto);
      const s = result.stats;
      const parts: string[] = [];
      if (s.autoLinked > 0) parts.push(`自动关联并确认 ${s.autoLinked} 条`);
      if (s.pendingSuggested > 0) parts.push(`生成 ${s.pendingSuggested} 条待人工确认`);
      if (parts.length === 0) parts.push("无新增候选（订单有 SKU 分配后会继续按重合度自动处理）");
      setNotice("预关联完成：" + parts.join("，"));
      await Promise.all([loadChain(), loadSummary()]);
    } catch (caught) {
      setNotice("预关联失败：" + String(caught));
    } finally {
      setBusyAction("");
    }
  }

  async function chainManualLink(orderId: number, targetId: number) {
    setChainBusy(true);
    try {
      await procurementChainApi.manualLink(orderId, "inbound", targetId, "链路建链视图人工选择入库单");
      setNotice("已关联入库单");
      await loadChain();
    } catch (caught) {
      setNotice("关联失败：" + String(caught));
    } finally {
      setChainBusy(false);
    }
  }

  async function refresh() {
    setBusyAction("refresh");
    await Promise.all([
      loadSummary(),
      loadBoard(),
      view === "orders" ? loadOrders() : view === "chain" ? loadChain() : view === "matching" ? loadMatching() : loadSuppliers(),
    ]);
    setNotice("采购工作台已刷新");
    setBusyAction("");
  }

  function applyDatePreset(preset: Exclude<DatePreset, "custom">) {
    const today = new Date();
    if (preset === "all") {
      setStartDate("");
      setEndDate("");
    } else if (preset === "today") {
      setStartDate(inputDate(today));
      setEndDate(inputDate(today));
    } else if (preset === "yesterday") {
      const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
      setStartDate(inputDate(yesterday));
      setEndDate(inputDate(yesterday));
    } else if (preset === "week") {
      const weekAgo = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6);
      setStartDate(inputDate(weekAgo));
      setEndDate(inputDate(today));
    } else {
      setStartDate(inputDate(new Date(today.getFullYear(), today.getMonth(), 1)));
      setEndDate(inputDate(today));
    }
    setDatePreset(preset);
  }

  const refreshSelectedOrder = useCallback(async () => {
    if (selectedOrderId === null) return;
    setDetailLoading(true);
    try {
      const nextDetail = await procurementWorkbenchApi.workbench(selectedOrderId);
      setOrderDetail(nextDetail);
      await Promise.all([loadSummary(), loadOrders()]);
    } finally {
      setDetailLoading(false);
    }
  }, [loadOrders, loadSummary, selectedOrderId]);

  // 订单被删除后：清掉选中项与 URL 上的 order 参数，重新拉列表（loadOrders 会自动选中下一张）。
  const removeDeletedOrder = useCallback(async () => {
    requestedOrder.current = null;
    setSelectedOrderId(null);
    setOrderDetail(null);
    const params = new URLSearchParams(window.location.search);
    params.set("view", "orders");
    params.delete("order");
    params.delete("action");
    window.history.replaceState(null, "", `/purchase/workbench?${params}`);
    await Promise.all([loadSummary(), loadOrders()]);
  }, [loadOrders, loadSummary]);

  const groups = useMemo(() => groupOrders(orders), [orders]);
  const filteredSuppliers = useMemo(() => {
    const normalized = supplierQuery.trim().toLowerCase();
    return normalized ? suppliers.filter((supplier) => supplier.supplierName.toLowerCase().includes(normalized)) : suppliers;
  }, [supplierQuery, suppliers]);

  return (
    <div className="min-w-0 bg-[#f7f8fc] text-[#26324b]">
      <main className="min-w-0 px-5 pb-8 pt-5 sm:px-6 2xl:px-7">
          <select aria-label="工作台功能导航" className="mb-3 w-full rounded-lg border border-slate-200 bg-white p-2 lg:hidden" value={view} onChange={event => changeView(event.target.value as ViewMode)}>
            {Object.entries(WORKBENCH_VIEWS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <WorkbenchHeader
            view={view}
            busyAction={busyAction}
            onViewChange={changeView}
            onNewOrder={() => setNewOrderOpen(true)}
            onMatch={runMatch}
            onRefresh={refresh}
            onExport={() => exportOrders(orders)}
          />
          {notice && (
            <div className="mt-3 flex items-center justify-between rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-[12px] text-indigo-700">
              <span>{notice}</span>
              <button onClick={() => setNotice("")} className="text-lg leading-none text-indigo-400 hover:text-indigo-700">×</button>
            </div>
          )}
          {error && <div className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-600">{error}</div>}
          {["orders", "suppliers", "chain", "matching"].includes(view) && (
            <KpiGrid
              summary={summary}
              board={boardOverview}
              funnel={funnel}
              todos={todos}
              onOpenChain={() => changeView("chain")}
            />
          )}

          {view === "orders" ? (
            <div className="mt-5 grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,4fr)_minmax(520px,6fr)]">
              <section className="min-w-0">
                <OrderToolbar
                  summary={summary}
                  total={total}
                  statusFilter={statusFilter}
                  channelFilter={channelFilter}
                  startDate={startDate}
                  endDate={endDate}
                  searchDraft={searchDraft}
                  query={query}
                  showFilter={showFilter}
                  datePreset={datePreset}
                  onStatusChange={setStatusFilter}
                  onChannelChange={setChannelFilter}
                  onStartDateChange={(value) => { setStartDate(value); setDatePreset("custom"); }}
                  onEndDateChange={(value) => { setEndDate(value); setDatePreset("custom"); }}
                  onDatePreset={applyDatePreset}
                  onSearchDraftChange={setSearchDraft}
                  onToggleFilter={() => setShowFilter((shown) => !shown)}
                  onApplySearch={() => setQuery(searchDraft.trim())}
                  onClearSearch={() => { setSearchDraft(""); setQuery(""); }}
                />
                <div className="mt-4 space-y-4">
                  {loading ? <Loading text="正在加载采购订单…" /> : groups.length === 0 ? <Empty text="没有符合条件的采购订单" /> : (() => {
                    // 渠道筛选作用于当前页；订单接口已返回平台维度。
                    const visibleGroups = channelFilter === "all"
                      ? groups
                      : groups
                          .map((group) => ({ ...group, items: group.items.filter((item) => channelOf(item.platform) === channelFilter) }))
                          .filter((group) => group.items.length > 0);
                    return visibleGroups.length === 0
                      ? <Empty text="该渠道下没有符合条件的采购订单" />
                      : visibleGroups.map((group) => (
                          <OrderGroup
                            key={group.key}
                            label={group.label}
                            items={group.items}
                            selectedId={selectedOrderId}
                            onSelect={selectOrder}
                          />
                        ));
                  })()}
                </div>
                <div className="mt-3 flex items-center justify-between px-1 text-[11px] text-slate-400">
                  <span>{channelFilter === "all" ? `共 ${total} 条采购订单` : `本页筛出 ${groups.reduce((n, g) => n + g.items.filter((item) => channelOf(item.platform) === channelFilter).length, 0)} 条（渠道筛选仅作用于当前页）`}{outstandingTotal > 0 && <span className="ml-2 font-medium text-rose-500">未开票合计 {fmtMoney(outstandingTotal)}</span>}</span>
                  {channelFilter === "all" && <div className="flex items-center gap-3"><button disabled={page <= 1 || loading} onClick={() => { setSelectedOrderId(null); setPage(p => p - 1); }} className="disabled:opacity-40">上一页</button><span>{page} / {Math.max(1, Math.ceil(total / pageSize))}</span><button disabled={page * pageSize >= total || loading} onClick={() => { setSelectedOrderId(null); setPage(p => p + 1); }} className="disabled:opacity-40">下一页</button><button onClick={() => exportOrders(orders)} className="font-medium text-indigo-600 hover:text-indigo-700">导出本页</button></div>}
                </div>
              </section>
              <OrderDetailPanel
                key={selectedOrderId}
                detail={orderDetail}
                loading={detailLoading}
                onOrderChanged={refreshSelectedOrder}
                onOrderDeleted={(message) => { setNotice(message); void removeDeletedOrder(); }}
                onOpenSupplier={(name) => { setSelectedSupplierName(name); changeView("suppliers"); }}
              />
            </div>
          ) : view === "chain" ? (
            <ChainPanel
              overview={chainOverview}
              orders={chainOrders}
              total={chainTotal}
              pending={chainPending}
              filter={chainFilter}
              loading={loading}
              busy={chainBusy}
              prelinkBusy={busyAction === "prelink"}
              xrefBusy={busyAction === "xref"}
              onFilterChange={setChainFilter}
              onConfirm={confirmChainLink}
              onReject={rejectChainLink}
              onPrelink={runPrelink}
              onManualLink={chainManualLink}
              onReload={loadChain}
              onNotice={setNotice}
              onStageView={changeView}
            />
          ) : view === "matching" ? (
            <MatchingView
              summary={inboundMatch}
              pending={pendingAlloc}
              busy={matchingBusy}
              loading={loading}
              onRunAuto={runInboundAuto}
              onRefresh={loadMatching}
              onNotice={setNotice}
              onOpenOrder={selectOrder}
            />
          ) : view === "suppliers" ? (
            <div className="mt-5 grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(520px,1fr)]">
              <SupplierList
                suppliers={filteredSuppliers}
                loading={loading}
                query={supplierQuery}
                selectedName={selectedSupplierName}
                onQueryChange={setSupplierQuery}
                onSelect={setSelectedSupplierName}
              />
              <SupplierDetailPanel
                detail={supplierDetail}
                loading={detailLoading}
                onRenamed={async (newName) => {
                  setSelectedSupplierName(newName);
                  await loadSuppliers();
                }}
              />
            </div>
          ) : <WorkspaceModule key={view} view={view} />}
      </main>
      {newOrderOpen && <NewPurchaseModal onClose={() => { setNewOrderOpen(false); const url = new URL(window.location.href); url.searchParams.delete("action"); window.history.replaceState(null, "", url.pathname + url.search); }} onCreated={(orderId) => {
        setNewOrderOpen(false);
        setStatusFilter("all"); setQuery(""); setSearchDraft(""); setStartDate(""); setEndDate(""); setPage(1);
        selectOrder(orderId);
        setNotice("采购记录已保存，可在右侧继续分配 SKU 和费用");
        void loadOrders(); void loadSummary();
      }} />}
    </div>
  );
}


function WorkbenchHeader({ view, busyAction, onViewChange, onNewOrder, onMatch, onRefresh, onExport }: {
  view: ViewMode;
  busyAction: string;
  onViewChange: (view: ViewMode) => void;
  onNewOrder: () => void;
  onMatch: () => void;
  onRefresh: () => void;
  onExport: () => void;
}) {
  if (!["orders", "suppliers", "chain", "matching"].includes(view)) return (
    <header className="flex items-center justify-between gap-4"><div><p className="text-xs text-slate-400">电商经营数据平台 / {WORKBENCH_VIEWS[view]}</p><h1 className="mt-1 text-2xl font-semibold">{WORKBENCH_VIEWS[view]}</h1></div><HeaderButton icon="orders" onClick={() => onViewChange("orders")}>返回采购订单</HeaderButton></header>
  );
  return (
    <header className="sticky top-0 z-30 -mx-5 -mt-5 flex flex-wrap items-center justify-between gap-4 border-b border-slate-200/60 bg-[#f7f8fc] px-5 pb-3 pt-5 sm:-mx-6 sm:px-6 2xl:-mx-7 2xl:px-7">
      <div className="flex flex-wrap items-center gap-4">
        <div>
          <div className="mb-1 text-[10px] text-slate-400">采购　/　采购工作台</div>
          <div className="flex items-baseline gap-4">
            <h1 className="text-[25px] font-semibold tracking-tight text-slate-900">采购工作台</h1>
            <p className="hidden text-[12px] text-slate-400 2xl:block">以订单时间为主线，高效推进采购全流程</p>
          </div>
          <p className="mt-1 text-[11px] text-slate-400 2xl:hidden">以订单时间为主线，高效推进采购全流程</p>
        </div>
        <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
          <ViewButton active={view === "orders"} onClick={() => onViewChange("orders")} icon="orders">订单视图</ViewButton>
          <ViewButton active={view === "suppliers"} onClick={() => onViewChange("suppliers")} icon="users">供应商视图</ViewButton>
          <ViewButton active={view === "chain"} onClick={() => onViewChange("chain")} icon="chart">链路建链</ViewButton>
          <ViewButton active={view === "matching"} onClick={() => onViewChange("matching")} icon="box">SKU 匹配</ViewButton>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <HeaderLink href="/data-center-import" icon="import">数据接入</HeaderLink>
        <HeaderButton onClick={onNewOrder} icon="plus">新建采购记录</HeaderButton>
        <HeaderButton icon="magic" primary busy={busyAction === "match"} onClick={onMatch}>自动匹配并确认</HeaderButton>
        <HeaderButton icon="download" onClick={onExport}>导出</HeaderButton>
        <HeaderButton icon="refresh" busy={busyAction === "refresh"} onClick={onRefresh}>刷新</HeaderButton>
      </div>
    </header>
  );
}

function ViewButton({ active, icon, onClick, children }: { active: boolean; icon: IconName; onClick: () => void; children: ReactNode }) {
  return (
    <button onClick={onClick} className={cx(
      "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[11px] font-medium transition-colors",
      active ? "bg-indigo-50 text-indigo-600 ring-1 ring-inset ring-indigo-200" : "text-slate-500 hover:bg-slate-50"
    )}><Icon name={icon} size={14} />{children}</button>
  );
}

function HeaderLink({ href, icon, children }: { href: string; icon: IconName; children: ReactNode }) {
  return <Link href={workbenchHref(href)} className="flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[11px] font-medium text-slate-600 shadow-sm hover:bg-slate-50"><Icon name={icon} size={15} />{children}</Link>;
}

function HeaderButton({ icon, primary, busy, disabled, onClick, children }: {
  icon: IconName; primary?: boolean; busy?: boolean; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  const isDisabled = busy || disabled;
  return (
    <button
      disabled={isDisabled}
      onClick={isDisabled ? undefined : onClick}
      title={disabled ? "今日配额已耗尽，请走文件入库" : undefined}
      className={cx(
        "flex h-9 items-center gap-1.5 rounded-lg px-3 text-[11px] font-medium shadow-sm transition-colors",
        isDisabled ? "cursor-not-allowed border border-slate-200 bg-slate-50 text-slate-400" :
        primary ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white hover:from-indigo-700 hover:to-violet-700" : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
      )}
    ><Icon name={icon} size={15} />{busy ? "处理中…" : children}</button>
  );
}

function KpiGrid({ summary, board, funnel, todos, onOpenChain }: {
  summary: WorkbenchSummary | null;
  board: BoardOverview | null;
  funnel: WorkbenchFunnel | null;
  todos: WorkbenchTodo | null;
  onOpenChain?: () => void;
}) {
  const cards: Array<{ label: string; value: number | string; hint: string; icon: IconName; tone: string }> = [
    {
      label: "新订单", value: summary?.newOrders ?? "—", hint: "按采购时间统计", icon: "orders", tone: "blue",
    },
    { label: "待匹配SKU", value: summary?.pendingSku ?? "—", hint: "待完善采购内容", icon: "magic", tone: "violet" },
    { label: "待生成采购单", value: summary?.pendingPo ?? "—", hint: "SKU齐全待生成", icon: "receipt", tone: "orange" },
    { label: "已到货待入库", value: summary?.pendingInbound ?? "—", hint: "等待入库确认", icon: "box", tone: "green" },
    { label: "待开票", value: summary?.pendingInvoice ?? "—", hint: "等待发票清单", icon: "receipt", tone: "amber" },
    { label: "异常数", value: summary?.exceptionCount ?? "—", hint: "需人工处理", icon: "reconcile", tone: "red" },
    {
      label: "已付款率",
      value: board ? `${board.paidRate}%` : "—",
      hint: board ? `已付 ${fmtMoney(board.paidAmount)} / 应付 ${fmtMoney(board.totalAmount)}` : "看板数据未就绪",
      icon: "reconcile",
      tone: "teal",
    },
  ];
  const steps = funnel?.steps ?? [];
  const pending = todos?.pending ?? 0;
  const doneStages = steps.filter((step) => step.pct >= 100).length;
  return (
    <section className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
      {cards.map((card) => <KpiCard key={card.label} {...card} />)}
      {steps.length > 0 && (
        <article
          role="button"
          tabIndex={0}
          onClick={onOpenChain}
          onKeyDown={(event) => { if (event.key === "Enter") onOpenChain?.(); }}
          title={steps.map((step) => `${CIRCLED[step.no - 1] ?? step.no} ${step.label}：${step.count}/${funnel?.total ?? 0}（${step.pct}%）｜${step.act}`).join("\n")}
          className="flex min-h-[84px] cursor-pointer flex-col justify-center gap-1 rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)] transition-colors hover:border-indigo-200 hover:bg-indigo-50/30"
        >
          <div className="flex items-center gap-1.5">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-600"><Icon name="magic" size={12} /></span>
            <span className="truncate text-[10.5px] font-medium text-slate-500">环节完成度</span>
          </div>
          <div className="text-[19px] font-semibold leading-6 tabular-nums text-slate-900">{doneStages}/7<span className="ml-1.5 text-[9.5px] font-normal text-slate-400">{pending > 0 ? <span className="font-medium text-amber-600">{pending} 个待办</span> : "暂无待办"}</span></div>
          <div className="mt-2 flex items-end gap-1">
            {steps.map((step) => {
              const dimColor = step.dimension === "1688" ? "bg-indigo-500" : step.dimension === "purchase" ? "bg-violet-500" : step.dimension === "jackyun" ? "bg-teal-500" : "bg-amber-500";
              return (
                <div key={step.key} className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <div className="h-1 overflow-hidden rounded-full bg-slate-100">
                    <div className={cx("h-full", dimColor)} style={{ width: step.pct + "%" }} />
                  </div>
                  <div className="truncate text-center font-mono text-[8px] leading-3 text-slate-400">{CIRCLED[step.no - 1] ?? step.no}{step.pct}%</div>
                </div>
              );
            })}
          </div>
        </article>
      )}
    </section>
  );
}

function KpiCard({ label, value, hint, icon, tone }: {
  label: string; value: number | string; hint: string; icon: IconName; tone: string;
}) {
  const tones: Record<string, string> = {
    blue: "bg-blue-50 text-blue-600", violet: "bg-violet-50 text-violet-600", orange: "bg-orange-50 text-orange-600",
    green: "bg-emerald-50 text-emerald-600", amber: "bg-amber-50 text-amber-600", red: "bg-red-50 text-red-600",
    teal: "bg-teal-50 text-teal-600",
  };
  return (
    <article title={`${label}（${hint}）`} className="flex min-h-[84px] flex-col justify-center gap-0.5 rounded-xl border border-slate-200/80 bg-white px-3 py-2.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
      <div className="flex items-center gap-1.5">
        <span className={cx("flex h-5 w-5 shrink-0 items-center justify-center rounded-md", tones[tone])}><Icon name={icon} size={12} /></span>
        <span className="truncate text-[10.5px] font-medium text-slate-500">{label}</span>
      </div>
      <div className="text-[19px] font-semibold leading-6 tabular-nums text-slate-900">{value}</div>
      <div className="truncate text-[9.5px] text-slate-400" title={hint}>{hint}</div>
    </article>
  );
}

function OrderToolbar(props: {
  summary: WorkbenchSummary | null; total: number; statusFilter: FilterKey; channelFilter: ChannelKey; startDate: string; endDate: string;
  searchDraft: string; query: string; showFilter: boolean; datePreset: DatePreset; onStatusChange: (value: FilterKey) => void;
  onChannelChange: (value: ChannelKey) => void;
  onStartDateChange: (value: string) => void; onEndDateChange: (value: string) => void;
  onDatePreset: (value: Exclude<DatePreset, "custom">) => void;
  onSearchDraftChange: (value: string) => void; onToggleFilter: () => void; onApplySearch: () => void; onClearSearch: () => void;
}) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((filter) => {
          const count = filter.key === "all" ? props.summary?.totalOrders ?? props.total : filter.summaryKey ? props.summary?.[filter.summaryKey] ?? "—" : null;
          return (
            <button key={filter.key} onClick={() => props.onStatusChange(filter.key)} className={cx(
              "flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[11px] font-medium transition-colors",
              props.statusFilter === filter.key ? "border-indigo-400 bg-white text-indigo-600 shadow-sm ring-1 ring-indigo-100" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300"
            )}>
              {filter.label}
              {count !== null && <span className={props.statusFilter === filter.key ? "text-indigo-500" : "text-slate-400"}>{count}</span>}
            </button>
          );
        })}
        <div className="ml-auto flex h-8 items-center gap-2">
          <div className="flex h-8 items-center rounded-lg border border-slate-200 bg-white text-[10px] text-slate-400 shadow-sm">
            <label className="px-3"><input type="date" value={props.startDate} onChange={(event) => props.onStartDateChange(event.target.value)} aria-label="开始日期" className="w-[93px] bg-transparent text-[10px] text-slate-500 outline-none" /></label>
            <span>→</span>
            <label className="px-3"><input type="date" value={props.endDate} onChange={(event) => props.onEndDateChange(event.target.value)} aria-label="结束日期" className="w-[93px] bg-transparent text-[10px] text-slate-500 outline-none" /></label>
          </div>
          <button onClick={props.onToggleFilter} className={cx(
            "flex h-8 items-center gap-1.5 rounded-lg border px-3 text-[11px] font-medium shadow-sm",
            props.showFilter || props.query || props.channelFilter !== "all" ? "border-indigo-300 bg-indigo-50 text-indigo-600" : "border-slate-200 bg-white text-slate-500"
          )}><Icon name="filter" size={14} />筛选</button>
        </div>
      </div>
      {props.showFilter && (
        <div className="mt-2 rounded-lg border border-indigo-100 bg-white p-3 shadow-sm">
          <div className="mb-2 flex flex-wrap items-center gap-1.5 px-1">
            <span className="mr-1 text-[10px] text-slate-400">快捷日期</span>
            {([
              ["all", "全部日期"], ["today", "今天"], ["yesterday", "昨天"], ["week", "近7天"], ["month", "本月"],
            ] as const).map(([value, label]) => (
              <button key={value} onClick={() => props.onDatePreset(value)} className={cx(
                "rounded-md px-2.5 py-1 text-[10px] transition-colors",
                props.datePreset === value ? "bg-indigo-50 font-medium text-indigo-600" : "text-slate-500 hover:bg-slate-50"
              )}>{label}</button>
            ))}
            {props.datePreset === "custom" && <span className="rounded-md bg-slate-100 px-2.5 py-1 text-[10px] text-slate-500">自定义日期</span>}
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-1.5 px-1">
            <span className="mr-1 text-[10px] text-slate-400">采购渠道</span>
            {([
              ["all", "全部渠道"], ["1688", "1688"], ["pdd", "拼多多"], ["taobao", "淘宝"], ["other", "其他"],
            ] as const).map(([value, label]) => (
              <button key={value} onClick={() => props.onChannelChange(value)} className={cx(
                "flex items-center gap-1 rounded-md px-2.5 py-1 text-[10px] transition-colors",
                props.channelFilter === value ? "bg-indigo-50 font-medium text-indigo-600" : "text-slate-500 hover:bg-slate-50"
              )}>
                {value !== "all" && <span className={cx("h-1.5 w-1.5 rounded-full", CHANNELS[value].dot)} />}
                {label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <div className="flex min-w-0 flex-1 items-center gap-2 rounded-md bg-slate-50 px-3">
              <Icon name="search" size={14} />
              <input value={props.searchDraft} onChange={(event) => props.onSearchDraftChange(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") props.onApplySearch(); }} placeholder="搜索订单号或供应商" className="h-8 min-w-0 flex-1 bg-transparent text-[11px] text-slate-700 outline-none placeholder:text-slate-400" />
            </div>
            <button onClick={props.onApplySearch} className="h-8 rounded-md bg-indigo-600 px-4 text-[11px] font-medium text-white">应用</button>
            {(props.searchDraft || props.query) && <button onClick={props.onClearSearch} className="h-8 px-2 text-[11px] text-slate-400 hover:text-slate-600">清空</button>}
          </div>
        </div>
      )}
    </div>
  );
}

function OrderGroup({ label, items, selectedId, onSelect }: {
  label: string; items: WorkbenchOrderItem[]; selectedId: number | null; onSelect: (id: number) => void;
}) {
  return (
    <section>
      <div className="mb-2 flex items-center gap-2 px-1 text-[13px] font-semibold text-slate-700"><span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />{label}</div>
      <div className="overflow-x-auto rounded-xl border border-slate-200/90 bg-white shadow-[0_3px_14px_rgba(40,53,85,0.035)]">
        <div className="grid min-w-[720px] grid-cols-[22px_108px_minmax(190px,1fr)_96px_118px_92px] items-center gap-3 border-b border-slate-100 bg-slate-50/70 px-3.5 py-2 text-[9.5px] font-medium text-slate-400">
          <span />
          <span>采购时间</span>
          <span>订单号 / 供应商</span>
          <span className="text-right">下单金额</span>
          <span className="text-right">开票（未开票）</span>
          <span className="text-right">当前状态</span>
        </div>
        {items.map((item) => <OrderRow key={item.orderId} item={item} active={item.orderId === selectedId} onSelect={() => onSelect(item.orderId)} />)}
      </div>
    </section>
  );
}

function invoiceCell(item: WorkbenchOrderItem, closed: boolean) {
  if (closed) return <span className="text-[11px] tabular-nums text-slate-400">—</span>;
  const outstanding = item.invoiceOutstanding ?? null;
  const status = item.invoiceStatus ?? "none";
  if (status === "done" || (outstanding !== null && outstanding <= 0 && (item.invoicedAmount ?? 0) > 0)) {
    return <span className="text-[11px] font-medium tabular-nums text-emerald-600">已开票</span>;
  }
  if (outstanding === null || (status === "none" && !(item.invoicedAmount ?? 0))) {
    return <span className="text-[11px] tabular-nums text-slate-400">—</span>;
  }
  return (
    <span className="text-right">
      <span className="block text-[11px] font-semibold tabular-nums text-rose-600">{fmtMoney(outstanding)}</span>
      {status === "partial" && <span className="block text-[9px] text-slate-400">部分开票 {fmtMoney(item.invoicedAmount ?? 0)}</span>}
    </span>
  );
}

function OrderRow({ item, active, onSelect }: { item: WorkbenchOrderItem; active: boolean; onSelect: () => void }) {
  const label = statusLabel(item);
  const closed = isOrderClosed(item.orderStatus);
  return (
    <button onClick={onSelect} className={cx(
      "grid w-full min-w-[720px] grid-cols-[22px_108px_minmax(190px,1fr)_96px_118px_92px] items-center gap-3 border-b border-slate-100 px-3.5 py-3 text-left transition-all last:border-b-0",
      closed && "bg-slate-50/60",
      active ? "relative z-[1] bg-[#fbfaff] ring-1 ring-inset ring-indigo-500" : "hover:bg-slate-50/70"
    )}>
      <span className={cx("flex h-4 w-4 items-center justify-center rounded-full border text-[9px]", active ? "border-indigo-500 bg-indigo-500 text-white" : "border-slate-300 text-transparent")}>✓</span>
      <span title={fmtDateTime(item.orderDate)}><span className={cx("block text-[11px] font-semibold tabular-nums", closed ? "text-slate-500" : "text-slate-800")}>{fmtDateTime(item.orderDate)}</span></span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5"><PlatformBadge value={item.platform} /><OrderKindTag value={item.orderKind} /><span className={cx("truncate text-[12px] font-medium", closed ? "text-slate-500 line-through" : "text-slate-800")}>{item.supplier || "未记录供应商"}</span>{closed && <span className="shrink-0 rounded bg-slate-200 px-1 py-0.5 text-[9px] font-medium text-slate-600">已关闭</span>}{item.hasException && !closed && <span className="shrink-0 rounded bg-red-50 px-1 py-0.5 text-[9px] text-red-500">异常</span>}</span>
        <span className="mt-1 block truncate font-mono text-[9.5px] text-slate-400">订单号：{item.orderNo}</span>
      </span>
      <span className="text-right"><span className={cx("block text-[13px] font-semibold tabular-nums", closed ? "text-slate-500" : "text-slate-800")}>{fmtMoney(item.amount)}</span></span>
      <span className="text-right">{invoiceCell(item, closed)}</span>
      <span className="text-right"><span className={cx("inline-flex rounded-md px-2 py-1 text-[9.5px] font-medium", statusClass(label))}>{label}</span>{closed && item.orderStatus && <span className="mt-1 block text-[9px] text-slate-400">{item.orderStatus}</span>}</span>
    </button>
  );
}

function OrderDetailPanel({ detail, loading, onOrderChanged, onOrderDeleted, onOpenSupplier }: {
  detail: WorkbenchDetail | null;
  loading: boolean;
  onOrderChanged: () => Promise<void>;
  onOrderDeleted: (message: string) => void;
  onOpenSupplier: (name: string) => void;
}) {
  const [skuEditorOpen, setSkuEditorOpen] = useState(false);
  const [skuEditorTargetDoc, setSkuEditorTargetDoc] = useState<number | null>(null);
  const [skuCatalog, setSkuCatalog] = useState<CatalogSkuRow[]>([]);
  const [skuCatalogLoading, setSkuCatalogLoading] = useState(false);
  const [selectedSkuId, setSelectedSkuId] = useState<number | null>(null);
  const [skuEntry, setSkuEntry] = useState("");
  const [skuQty, setSkuQty] = useState("1");
  const [skuPrice, setSkuPrice] = useState("");
  const [skuConsumableId, setSkuConsumableId] = useState<number | null>(null);
  const [skuConsumableEntry, setSkuConsumableEntry] = useState("");
  const [skuConsumableQty, setSkuConsumableQty] = useState("");
  const [skuConsumableAutoQty, setSkuConsumableAutoQty] = useState(false);
  const [skuConsumableTouched, setSkuConsumableTouched] = useState(false);
  const [pendingConsumableUpdate, setPendingConsumableUpdate] = useState<PendingConsumableUpdate | null>(null);
  const [skuAction, setSkuAction] = useState("");
  const [editingAllocation, setEditingAllocation] = useState<number | null>(null);
  const [skuMessage, setSkuMessage] = useState("");
  const [expenseType, setExpenseType] = useState("pack");
  const [expenseAmount, setExpenseAmount] = useState("");
  const [expenseMessage, setExpenseMessage] = useState("");
  const [adjEditing, setAdjEditing] = useState(false);
  const [adjValue, setAdjValue] = useState("");
  const [adjNote, setAdjNote] = useState("");
  const [inboundEditorOpen, setInboundEditorOpen] = useState(false);
  const [inboundCandidates, setInboundCandidates] = useState<ChainLinkCandidate[]>([]);
  const [inboundCandidateLoading, setInboundCandidateLoading] = useState(false);
  const [inboundQuery, setInboundQuery] = useState("");
  const [selectedInboundId, setSelectedInboundId] = useState<number | null>(null);
  const [replaceInboundLinkId, setReplaceInboundLinkId] = useState<number | null>(null);
  const [inboundMessage, setInboundMessage] = useState("");
  const [editorBusy, setEditorBusy] = useState(false);
  const [usageDecision, setUsageDecision] = useState<"" | "yes" | "no">("");
  const [usageMaterials, setUsageMaterials] = useState<ConsumableRow[]>([]);
  const [usageMappings, setUsageMappings] = useState<ConsumableMappingRow[]>([]);
  const [usageMaterialIds, setUsageMaterialIds] = useState<number[]>([]);
  const [usageQtys, setUsageQtys] = useState<Record<number, string>>({});
  const [usageLoading, setUsageLoading] = useState(false);
  const [docAmountBusy, setDocAmountBusy] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [poBusy, setPoBusy] = useState(false);
  const [poMessage, setPoMessage] = useState("");
  const [poEditorOpen, setPoEditorOpen] = useState(false);
  const [poListOpen, setPoListOpen] = useState(false);
  const [poLinkNo, setPoLinkNo] = useState("");
  const [poRelationKind, setPoRelationKind] = useState("");
  const [poAllocAmount, setPoAllocAmount] = useState("");
  const [mainEditOpen, setMainEditOpen] = useState(false);
  const [mainSaving, setMainSaving] = useState(false);
  const [mainError, setMainError] = useState("");
  const [mainForm, setMainForm] = useState({ supplier: "", title: "", date: "", goods: "", freight: "", discount: "", paid: "", orderAmount: "", platform: "other" });
  // 单据 tab：吉客云单据 / 耗材入库单（两类单据都始终可见，替代原先按订单类型的互斥隐藏）
  const [docTab, setDocTab] = useState<"jackyun" | "consumable">("jackyun");

  useEffect(() => {
    setSkuEditorOpen(false);
    setSelectedSkuId(null);
    setSkuEntry("");
    setSkuQty("1");
    setSkuPrice("");
    setSkuConsumableId(null);
    setSkuConsumableEntry("");
    setSkuConsumableQty("");
    setSkuConsumableAutoQty(false);
    setSkuConsumableTouched(false);
    setPendingConsumableUpdate(null);
    setSkuMessage("");
    setExpenseAmount("");
    setExpenseMessage("");
    setEditingAllocation(null);
    setInboundEditorOpen(false);
    setInboundCandidates([]);
    setInboundQuery("");
    setSelectedInboundId(null);
    setReplaceInboundLinkId(null);
    setInboundMessage("");
    setUsageDecision("");
    setUsageMaterials([]);
    setUsageMappings([]);
    setUsageMaterialIds([]);
    setUsageQtys({});
    // 注意：order 在组件后段才解构（detail 非空分支），此处用 detail?.order 避免早退分支下 TDZ 崩溃
    setDocTab(detail?.order.orderKind === "consumable" ? "consumable" : "jackyun");
    setPoEditorOpen(false);
    setPoListOpen(false);
    setPoMessage("");
    setPoLinkNo("");
    setPoRelationKind("");
    setPoAllocAmount("");
    setMainEditOpen(false);
    setMainSaving(false);
    setMainError("");
  }, [detail?.order.orderId]);

  useEffect(() => {
    if (!skuEditorOpen) return;
    let cancelled = false;
    setSkuCatalogLoading(true);
    dashboardApi.products("", 500)
      .then(rows => { if (!cancelled) setSkuCatalog(rows); })
      .catch(() => { if (!cancelled) setSkuMessage("吉客云 SKU 主档加载失败，请重新打开分配重试"); })
      .finally(() => { if (!cancelled) setSkuCatalogLoading(false); });
    return () => { cancelled = true; };
  }, [skuEditorOpen]);

  useEffect(() => {
    const orderId = detail?.order.orderId;
    if (!inboundEditorOpen || !orderId) return;
    let cancelled = false;
    setInboundCandidateLoading(true);
    procurementChainApi.candidates(orderId, "inbound", inboundQuery, 500)
      .then((result) => { if (!cancelled) setInboundCandidates(result.items); })
      .catch(() => { if (!cancelled) setInboundMessage("入库单候选加载失败，请刷新后重试"); })
      .finally(() => { if (!cancelled) setInboundCandidateLoading(false); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId, inboundEditorOpen, inboundQuery]);

  useEffect(() => {
    let cancelled = false;
    setUsageLoading(true);
    Promise.all([consumablesApi.list(), consumablesApi.mappings()])
      .then(([materials, mappings]) => {
        if (!cancelled) { setUsageMaterials(materials); setUsageMappings(mappings); }
      })
      .catch(() => { if (!cancelled) setInboundMessage("耗材档案加载失败，请刷新后重试"); })
      .finally(() => { if (!cancelled) setUsageLoading(false); });
    return () => { cancelled = true; };
  }, [detail?.order.orderId]);

  if (loading && !detail) return <aside className="rounded-xl border border-slate-200 bg-white"><Loading text="正在加载订单详情…" /></aside>;
  if (!detail) return <aside className="flex min-h-[520px] items-center justify-center rounded-xl border border-slate-200 bg-white px-8 text-center text-[12px] text-slate-400">选择左侧订单查看执行详情</aside>;
  const { order, detail: records, stepStates } = detail;
  const allocations = records.allocations as AllocationRow[];
  const expenses = records.expenses as ExpenseRow[];
  const purchaseOrders = (records.purchaseOrders ?? []) as ChainPurchaseOrder[];
  const poClosure = records.poAmountClosure ?? null;
  const inbound = records.inbound as InboundRow[];
  // SKU 分配归属：后端返回结构化 inboundDocumentId 时直接采用；旧版后端没有该字段时
  // 退化为解析 note（"由入库单 #N 明细自动反填"），保证后端未同步时分组依然正确。
  const docIdOf = (row: AllocationRow): number | null => {
    if (row.inboundDocumentId != null) return row.inboundDocumentId;
    const matched = /由入库单\s*#(\d+)/.exec(row.note ?? "");
    return matched ? Number(matched[1]) : null;
  };
  const linkedDocIds = new Set(inbound.map((row) => row.documentId).filter((id): id is number => id != null));
  const allocationsByDoc = new Map<number, AllocationRow[]>();
  const manualAllocations: AllocationRow[] = [];
  for (const row of allocations) {
    // 归属单据必须仍在本单已关联的入库单内，避免解除关联后残留空分组
    const docId = docIdOf(row);
    if (docId != null && linkedDocIds.has(docId)) {
      const list = allocationsByDoc.get(docId) ?? [];
      list.push(row);
      allocationsByDoc.set(docId, list);
    } else {
      manualAllocations.push(row);
    }
  }
  const manualTotal = manualAllocations.reduce((sum, row) => sum + (row.amount ?? 0), 0);
  const inboundLinked = inbound.filter((row) => row.linkId);
  const allInboundConsumableDecided = inboundLinked.length === 0
    || inboundLinked.every((row) => Boolean(row.consumableUsageDecided));
  const settlements = records.settlement as SettlementRow[];
  const extraAmount = expenses.reduce((sum, row) => sum + (typeof row.amount === "number" ? row.amount : 0), 0);
  const paidAmount = settlements.reduce((sum, row) => {
    const amount = row.paidAmount;
    return sum + (typeof amount === "number" ? amount : 0);
  }, 0);
  // 原单总额已含运费优惠，分摊费用不再次加到应付；未知付款不算已付。
  const payable = order.paidAmount ?? order.amount ?? 0;
  const unpaidAmount = Math.max(0, payable - paidAmount);
  const remainingAmount = records.unallocatedAmount ?? order.paidAmount ?? order.amount;
  // 1688 源单已关闭：整单只读，不允许再确认采购内容或修改分配。
  const closed = isOrderClosed(order.orderStatus);
  const editable = !closed && (!order.purchaseStatus || order.purchaseStatus === "pending_refine");
  const nextStatus = order.purchaseStatus ? NEXT_STATUS[order.purchaseStatus] : undefined;
  // 详情页默认全可修改；确认按钮仅作为手动推进快捷入口（SKU 分配保存后也会自动静默尝试确认）
  const canConfirm = Boolean(
    order.externalPoId && editable && allocations.length > 0 &&
    remainingAmount !== null && remainingAmount !== undefined && Math.abs(remainingAmount) < 0.005 &&
    allInboundConsumableDecided
  );
  const confirmBlockReason = !canConfirm
    ? (
      !order.externalPoId ? "缺少平台采购记录 ID"
      : closed ? `订单已关闭（1688 源单：${order.orderStatus || "已关闭"}），不可再确认`
      : !editable ? "当前状态不可确认"
      : allocations.length === 0 ? "尚未完成 SKU 分配"
      : !allInboundConsumableDecided ? "有入库单尚未登记耗材（请在对应卡片底部「耗材登记」选「有耗材/不使用」并保存）"
      : order.paidAmount == null && remainingAmount !== null && remainingAmount < -0.005
        ? `实付金额未登记：分配合计 ${fmtMoney(Math.abs(remainingAmount))}，请在「金额与付款分层」的「1688微调」补录实付后再确认`
      : (remainingAmount !== null && Math.abs(remainingAmount) > 0.005)
        ? (remainingAmount < 0 ? "超分配" : "待分配") + ` ${fmtMoney(Math.abs(remainingAmount))}：请在 SKU 分配区把金额调平`
        : "暂不可确认"
    )
    : "";
  const selectedSku = skuCatalog.find((sku) => sku.id === selectedSkuId) ?? null;
  const selectableSkuCatalog = skuCatalog.filter((sku) => sku.productType === "single" && sku.status === "active");
  const normalizedInboundQuery = inboundQuery.trim().toLowerCase();
  const matchingInboundCandidates = inboundCandidates.filter((candidate) => {
    if (!normalizedInboundQuery) return true;
    return `${candidate.targetNo} ${candidate.targetSupplier} ${candidate.warehouseName}`.toLowerCase().includes(normalizedInboundQuery);
  }).slice(0, 30);
  const selectedInbound = inboundCandidates.find((candidate) => candidate.targetId === selectedInboundId) ?? null;
  const editorInboundDoc = skuEditorTargetDoc == null
    ? null
    : inbound.find((row) => row.documentId === skuEditorTargetDoc) ?? null;
  const editorConsumableDisabled = editorInboundDoc?.consumableUsageEnabled === false;
  const editorConsumable = skuConsumableId == null
    ? null
    : usageMaterials.find((material) => material.id === skuConsumableId) ?? null;

  function mappedConsumableQty(quantity: string, mapping?: ConsumableMappingRow) {
    if (!mapping) return "";
    const value = Number(quantity) * Number(mapping.usagePerUnit || 0);
    return Number.isFinite(value) && value > 0 ? String(Number(value.toFixed(3))) : "1";
  }

  function seedSkuConsumable(skuId: number | null, quantity: string, existing?: EditorConsumableDraft | null) {
    if (existing) {
      setSkuConsumableId(existing.consumableId);
      const material = usageMaterials.find((item) => item.id === existing.consumableId);
      setSkuConsumableEntry(material ? `${material.code} · ${material.name}` : String(existing.consumableId));
      setSkuConsumableQty(existing.quantity);
      setSkuConsumableAutoQty(false);
    } else {
      const mapping = skuId == null ? undefined : usageMappings.find((item) => item.skuId === skuId);
      setSkuConsumableId(mapping?.consumableId ?? null);
      const material = mapping ? usageMaterials.find((item) => item.id === mapping.consumableId) : null;
      setSkuConsumableEntry(material ? `${material.code} · ${material.name}` : "");
      setSkuConsumableQty(mappedConsumableQty(quantity, mapping));
      setSkuConsumableAutoQty(Boolean(mapping));
    }
    setSkuConsumableTouched(false);
  }

  function resetSkuConsumable() {
    setSkuConsumableId(null);
    setSkuConsumableEntry("");
    setSkuConsumableQty("");
    setSkuConsumableAutoQty(false);
    setSkuConsumableTouched(false);
  }

  const skuListId = `purchase-sku-catalog-${order.orderId}`;
  const consumableListId = `purchase-consumable-catalog-${order.orderId}`;
  const skuLabel = (sku: CatalogSkuRow) => `${sku.skuCode} · ${sku.goodsName || sku.skuName || "未命名商品"}`;
  const consumableLabel = (material: ConsumableRow) => `${material.code} · ${material.name}`;

  function applySkuSelection(sku: CatalogSkuRow) {
    setSelectedSkuId(sku.id);
    setSkuEntry(skuLabel(sku));
    const mapping = usageMappings.find((item) => item.skuId === sku.id);
    setSkuConsumableId(mapping?.consumableId ?? null);
    const material = mapping ? usageMaterials.find((item) => item.id === mapping.consumableId) : null;
    setSkuConsumableEntry(material ? consumableLabel(material) : "");
    setSkuConsumableQty(mappedConsumableQty(skuQty, mapping));
    setSkuConsumableAutoQty(Boolean(mapping));
    setSkuConsumableTouched(true);
    // 采购单价以货品维护中的固定成本为准；未维护时留空，交由人工录入。
    setSkuPrice(sku.defaultCost != null && String(sku.defaultCost).trim() !== "" ? String(sku.defaultCost) : "");
  }

  function handleSkuEntry(value: string) {
    const normalized = value.trim().toLowerCase();
    setSkuEntry(value);
    if (!normalized) {
      setSelectedSkuId(null);
      setSkuPrice("");
      resetSkuConsumable();
      return;
    }
    const sku = selectableSkuCatalog.find((item) => [item.skuCode, item.jackyunSkuId, item.barcode, item.skuName, item.goodsName, skuLabel(item)]
      .some((candidate) => candidate?.trim().toLowerCase() === normalized));
    if (sku) applySkuSelection(sku);
    else {
      setSelectedSkuId(null);
      setSkuPrice("");
      resetSkuConsumable();
    }
  }

  function handleConsumableEntry(value: string) {
    const normalized = value.trim().toLowerCase();
    setSkuConsumableEntry(value);
    setSkuConsumableTouched(true);
    if (!normalized) {
      setSkuConsumableId(null);
      setSkuConsumableQty("");
      setSkuConsumableAutoQty(false);
      return;
    }
    const material = usageMaterials.find((item) => [item.code, item.name, consumableLabel(item)]
      .some((candidate) => candidate.trim().toLowerCase() === normalized));
    if (!material) {
      setSkuConsumableId(null);
      setSkuConsumableQty("");
      setSkuConsumableAutoQty(false);
      return;
    }
    setSkuConsumableId(material.id);
    setSkuConsumableEntry(consumableLabel(material));
    const mapping = usageMappings.find((item) => item.skuId === selectedSkuId && item.consumableId === material.id);
    setSkuConsumableQty(mapping ? mappedConsumableQty(skuQty, mapping) : "1");
    setSkuConsumableAutoQty(Boolean(mapping));
  }

  // 入库单卡片内的「添加/修改 SKU」：直接做成 SKU 明细表的一行，与表头列（SKU 明细/数量/单价/总价/耗材/操作）一一对齐
  const skuEditorRow = (
    <tr className="whitespace-nowrap border-t border-indigo-200 bg-indigo-50/60">
      <td className="py-1.5 pr-2">
        {skuCatalogLoading ? (
          <div className="flex h-8 items-center gap-1.5 rounded border border-indigo-200 bg-white px-2 text-[11px] text-slate-400">正在读取 SKU 主档…</div>
        ) : (
          <div className="flex min-w-0 items-center gap-1.5">
            <span className={cx("shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold", editingAllocation ? "bg-amber-100 text-amber-700" : "bg-indigo-100 text-indigo-700")}>{editingAllocation ? "编辑" : "新建"}</span>
            <div className="min-w-0 flex-1">
              <input
                list={skuListId}
                value={skuEntry}
                disabled={skuCatalogLoading}
                onChange={(event) => handleSkuEntry(event.target.value)}
                placeholder={skuCatalogLoading ? "正在读取 SKU 主档…" : "输入 SKU 编码或名称，可快速选择"}
                aria-label="SKU 明细"
                className="h-8 w-full min-w-0 rounded border border-indigo-300 bg-white px-1.5 text-[11px] outline-none focus:border-indigo-500 disabled:bg-slate-50"
              />
              <datalist id={skuListId}>
                {selectableSkuCatalog.map((sku) => <option key={sku.id} value={skuLabel(sku)} />)}
              </datalist>
            </div>
          </div>
        )}
      </td>
      <td className="py-1.5 px-1 text-right">
        <input
          type="number" min="0.0001" step="0.0001" aria-label="数量" value={skuQty}
          onChange={(event) => {
            const value = event.target.value;
            setSkuQty(value);
            if (skuConsumableAutoQty && skuConsumableId != null) {
              setSkuConsumableQty(mappedConsumableQty(value, usageMappings.find((item) => item.skuId === selectedSkuId)));
            }
          }}
          className="h-8 w-full max-w-[96px] rounded border border-slate-200 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-300"
        />
      </td>
      <td className="py-1.5 px-1 text-right">
        <input
          type="number" min="0" step="0.01" aria-label="单价" value={skuPrice}
          onChange={(event) => setSkuPrice(event.target.value)}
          className="h-8 w-full max-w-[96px] rounded border border-slate-200 bg-white px-1.5 text-right text-[11px] tabular-nums outline-none focus:border-indigo-300"
        />
      </td>
      <td className="py-1.5 px-1 text-right tabular-nums font-medium text-slate-700">{fmtMoney(Number(skuQty) * Number(skuPrice))}</td>
      <td className="py-1.5 px-1">
        {skuEditorTargetDoc == null || editorConsumableDisabled ? (
          <span className="text-[11px] text-slate-400">{editorConsumableDisabled ? "本单不使用" : "—"}</span>
        ) : (
          <div className="flex items-center gap-1">
            <input
              list={consumableListId}
              aria-label="耗材"
              value={skuConsumableEntry}
              disabled={usageLoading || usageMaterials.length === 0}
              onChange={(event) => handleConsumableEntry(event.target.value)}
              placeholder={usageLoading ? "正在读取耗材…" : "输入耗材编码或名称"}
              className="h-6 min-w-0 flex-1 rounded border border-indigo-200 bg-white px-1 text-[11px] outline-none focus:border-indigo-400 disabled:bg-slate-50 disabled:text-slate-400"
            />
            <datalist id={consumableListId}>
              {usageMaterials.map((material) => <option key={material.id} value={consumableLabel(material)} />)}
            </datalist>
            <input
              aria-label="耗材用量"
              type="number"
              min="0.0001"
              step="0.0001"
              value={skuConsumableQty}
              disabled={!skuConsumableId}
              onChange={(event) => { setSkuConsumableTouched(true); setSkuConsumableAutoQty(false); setSkuConsumableQty(event.target.value); }}
              placeholder="用量"
              className="h-6 w-12 shrink-0 rounded border border-slate-200 px-1 text-center text-[11px] tabular-nums outline-none focus:border-amber-400 disabled:bg-slate-50"
            />
            <span className="w-6 shrink-0 text-[11px] text-slate-400">{editorConsumable?.unit ?? "—"}</span>
          </div>
        )}
      </td>
      <td className="py-1.5 pl-2">
        <div className="flex justify-end gap-1.5">
          <button
            onClick={() => { setSkuEditorOpen(false); setSkuEditorTargetDoc(null); setEditingAllocation(null); resetSkuConsumable(); }}
            className="text-[11px] text-slate-400 hover:text-slate-600"
          >取消</button>
          <button
            disabled={skuAction !== "" || !selectedSku || !skuQty || skuPrice === "" || (skuConsumableId != null && (!skuConsumableQty || Number(skuConsumableQty) <= 0))}
            onClick={addSkuAllocation}
            className="rounded bg-indigo-600 px-2 py-1 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {skuAction === "add" ? "保存中…" : editingAllocation ? "保存修改" : skuEditorTargetDoc != null ? "保存到本单据" : "保存到采购内容"}
          </button>
        </div>
      </td>
    </tr>
  );

  async function readMutation(res: Response) {
    const body = await res.json().catch(() => ({})) as { detail?: string; id?: number };
    if (!res.ok) throw new Error(body.detail || `操作失败（${res.status}）`);
    return body;
  }

  async function ensureExternalPoId() {
    if (order.externalPoId) return order.externalPoId;
    const res = await authenticatedFetch("/api/v1/purchase/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_order_id: order.orderNo,
        platform: channelOf(order.platform),
        supplier_name: order.supplier || "",
        title: order.title || "",
        ordered_at: order.orderDate,
        order_amount: order.amount === null ? null : String(order.amount),
        paid_amount: (order.paidAmount ?? order.amount) === null ? null : String(order.paidAmount ?? order.amount),
      }),
    });
    const body = await readMutation(res);
    if (!body.id) throw new Error("采购工作流创建成功，但未返回采购单 ID");
    return body.id;
  }

  async function addSkuAllocation() {
    if (!selectedSku || !skuQty || skuPrice === "") return;
    const quantity = Number(skuQty);
    const unitPrice = Number(skuPrice);
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(unitPrice) || unitPrice < 0) {
      setSkuMessage("请填写有效的数量和单价");
      return;
    }
    const consumableQuantity = Number(skuConsumableQty);
    if (skuEditorTargetDoc != null && skuConsumableId != null && (!Number.isFinite(consumableQuantity) || consumableQuantity <= 0)) {
      setSkuMessage("请填写有效的耗材用量");
      return;
    }
    setSkuAction("add");
    setSkuMessage("");
    const targetDocId = skuEditorTargetDoc;
    const wasEditing = editingAllocation != null;
    const consumableTouched = skuConsumableTouched;
    try {
      const poId = await ensureExternalPoId();
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${poId}/allocations${editingAllocation ? `/${editingAllocation}` : ""}`, {
        method: editingAllocation ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sku_id: selectedSku.id,
          sku_code: selectedSku.skuCode,
          goods_name: selectedSku.goodsName || selectedSku.skuName,
          quantity: skuQty,
          unit_price: skuPrice,
          note: targetDocId != null ? `由入库单 #${targetDocId} 明细自动反填` : "",
        }),
      });
      const body = await readMutation(res);
      if (targetDocId != null && body.id && (!wasEditing || consumableTouched)) {
        setPendingConsumableUpdate({
          documentId: targetDocId,
          allocationId: body.id,
          draft: skuConsumableId == null || editorConsumableDisabled
            ? null
            : { consumableId: skuConsumableId, quantity: skuConsumableQty },
        });
      }
      setSelectedSkuId(null);
      setSkuEntry("");
      setSkuPrice("");
      resetSkuConsumable();
      setSkuMessage(wasEditing
        ? "SKU 数量/单价已更新；若该行原为入库单反填，单据明细与金额已同步更正（超分配/待分配会自动重算）"
        : (targetDocId != null ? `SKU 已添加到入库单 #${targetDocId}` : "SKU 已加入当前订单"));
      setEditingAllocation(null);
      if (wasEditing) {
        // 编辑保存：就地面板收起
        setSkuEditorTargetDoc(null);
        setSkuEditorOpen(false);
      } else if (targetDocId == null) {
        setSkuEditorTargetDoc(null);
      }
      // 卡片内新增：面板留在卡片内保持打开，选择已清空，可连续添加
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "SKU 分配失败");
    } finally {
      setSkuAction("");
    }
  }

  async function confirmPurchaseContent() {
    if (!order.externalPoId || !canConfirm) return;
    setSkuAction("confirm");
    setSkuMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/confirm-and-resync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "工作台总确认" }),
      });
      const body = await readMutation(res) as { purchaseStatus?: string; resync?: { ok: boolean | null; attempted: boolean; error?: string; stats?: { stockinOrders?: number; stockinItems?: number } } };
      const r = body.resync;
      const parts = ["采购内容已确认，订单已进入待生成采购单阶段"];
      if (r?.attempted) {
        if (r.ok) parts.push(`已尝试回写本地：入库单 ${r.stats?.stockinOrders ?? 0} 张 / 明细 ${r.stats?.stockinItems ?? 0} 行`);
        else parts.push(`本地回写未生效：${r.error ?? "未知原因"}`);
      }
      setSkuMessage(parts.join(" · "));
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "采购内容确认失败");
    } finally {
      setSkuAction("");
    }
  }

  async function deleteOrder() {
    const recoverable = order.orderId > 0;
    const tip = recoverable
      ? "这是一张 1688 导入订单，将执行软删除：仅从工作台隐藏（row_status=deleted），数据仍可恢复。"
      : "这是一张手工登记的订单（没有 1688 原件），将直接删除记录本体及其分配/费用，不可恢复。";
    if (!window.confirm(`确认删除订单 ${order.orderNo}？\n\n${tip}`)) return;
    setDeleteBusy(true);
    setSkuMessage("");
    try {
      const result = await procurementWorkbenchApi.softDeleteOrder(order.orderId);
      onOrderDeleted(result.recoverable
        ? `订单 ${result.orderNo || order.orderNo} 已从工作台移除（软删除，可恢复）`
        : `订单 ${result.orderNo || order.orderNo} 已删除`);
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "删除订单失败");
    } finally {
      setDeleteBusy(false);
    }
  }

  function openMainEdit() {
    setMainForm({
      supplier: order.supplier ?? "",
      title: order.title ?? "",
      date: order.orderDate ? fmtDate(order.orderDate) : "",
      goods: order.goodsTotal != null ? String(order.goodsTotal) : "",
      freight: order.freight != null ? String(order.freight) : "",
      discount: order.discount != null ? String(order.discount) : "",
      paid: order.paidAmount != null ? String(order.paidAmount) : "",
      orderAmount: order.amount != null ? String(order.amount) : "",
      platform: order.platform ?? "other",
    });
    setMainError("");
    setMainEditOpen(true);
  }

  async function saveMainEdit(event: React.FormEvent) {
    event.preventDefault();
    if (mainSaving) return;
    const isFile = order.fileOrderId != null;
    setMainSaving(true);
    setMainError("");
    try {
      await procurementWorkbenchApi.editOrderMain(order.orderId, isFile ? {
        supplier_name: mainForm.supplier,
        title: mainForm.title,
        goods_total: mainForm.goods,
        freight: mainForm.freight,
        discount: mainForm.discount,
        actual_payment: mainForm.paid,
      } : {
        supplier_name: mainForm.supplier,
        title: mainForm.title,
        ordered_at: mainForm.date || undefined,
        order_amount: mainForm.orderAmount,
        paid_amount: mainForm.paid,
        platform: mainForm.platform,
      });
      setMainEditOpen(false);
      await onOrderChanged();
    } catch (caught) {
      setMainError(caught instanceof Error ? caught.message : "订单主档保存失败");
    } finally {
      setMainSaving(false);
    }
  }

  async function linkJackyunPo() {
    const poId = await ensureExternalPoId();
    const no = poLinkNo.trim();
    if (!no) { setPoMessage("请输入要关联的吉客云采购单号"); return; }
    const alloc = poAllocAmount.trim() === "" ? null : poAllocAmount;
    setPoBusy(true);
    setPoMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${poId}/jackyun-link`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purch_no: no, relation_kind: poRelationKind, alloc_amount: alloc }),
      }));
      setPoEditorOpen(false);
      setPoLinkNo("");
      setPoRelationKind("");
      setPoAllocAmount("");
      setPoMessage("采购单已关联");
      await onOrderChanged();
    } catch (caught) {
      setPoMessage(caught instanceof Error ? caught.message : "关联采购单失败");
    } finally {
      setPoBusy(false);
    }
  }

  async function unlinkJackyunPo(linkId: number) {
    const poId = order.externalPoId;
    if (!poId || !window.confirm("确认解除这张吉客云采购单与当前订单的关联？")) return;
    setPoBusy(true);
    setPoMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${poId}/jackyun-links/${linkId}`, { method: "DELETE" }));
      setPoMessage("已解除采购单关联");
      await onOrderChanged();
    } catch (caught) {
      setPoMessage(caught instanceof Error ? caught.message : "解除关联失败");
    } finally {
      setPoBusy(false);
    }
  }

  async function reopenPurchase() {
    if (!order.externalPoId || !window.confirm("重新编辑后采购内容需再次确认，原始采购单、入库和发票关联保留。是否继续？")) return;
    setEditorBusy(true);
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/reopen`, { method: "POST" }));
      await onOrderChanged();
      setSkuEditorTargetDoc(null);
      setSkuEditorOpen(true);
      setSkuMessage("已重新打开采购内容，可修改 SKU、数量、单价和费用");
    } catch (caught) { setSkuMessage(caught instanceof Error ? caught.message : "重新编辑失败"); }
    finally { setEditorBusy(false); }
  }

  async function removeAllocation(allocationId: number) {
    if (!order.externalPoId) return;
    if (!window.confirm("确认删除这条 SKU 分配？删除后该明细将从入库单中移除，相关耗材台账会同步调整。")) return;
    setEditorBusy(true);
    setSkuMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/allocations/${allocationId}?po_id=${order.externalPoId}`, { method: "DELETE" });
      await readMutation(res);
      setSkuMessage("SKU 分配已删除");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function saveAdjustment() {
    if (!order.externalPoId) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      await readMutation(await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/adjustment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ adjustment_amount: adjValue, note: adjNote }),
      }));
      setAdjEditing(false);
      setAdjValue("");
      setAdjNote("");
      setExpenseMessage(adjValue.trim() === "" ? "1688 微调已清除" : "1688 微调已保存，分配平衡按 实付+微调 重算");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "微调保存失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function addExpense() {
    if (!expenseAmount) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      const poId = await ensureExternalPoId();
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${poId}/expenses`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expense_type: expenseType, amount: expenseAmount }),
      });
      await readMutation(res);
      setExpenseAmount("");
      setExpenseMessage("附加费用已登记");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "费用登记失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function removeExpense(expenseId: number) {
    if (!order.externalPoId) return;
    if (!window.confirm("确认删除这条附加费用？该费用将从本单费用分摊中移除。")) return;
    setEditorBusy(true);
    setExpenseMessage("");
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/expenses/${expenseId}?po_id=${order.externalPoId}`, { method: "DELETE" });
      await readMutation(res);
      setExpenseMessage("附加费用已删除");
      await onOrderChanged();
    } catch (caught) {
      setExpenseMessage(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function advanceStatus() {
    if (!order.externalPoId || !nextStatus) return;
    if ((nextStatus === "inbound" || nextStatus === "done") && (inbound.length === 0 || inbound.some((row) => !row.consumableUsageDecided))) {
      setSkuMessage("请先在已关联的每张吉客云入库单上确认是否添加耗材使用及数量");
      return;
    }
    setEditorBusy(true);
    try {
      const res = await authenticatedFetch(`/api/v1/purchase/orders/${order.externalPoId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      await readMutation(res);
      setSkuMessage("状态已流转到「" + (PO_STATUS[nextStatus] ?? nextStatus) + "」");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "状态流转失败");
    } finally {
      setEditorBusy(false);
    }
  }

  function openInboundPicker(linkId: number | null = null) {
    setReplaceInboundLinkId(linkId);
    setSelectedInboundId(null);
    setInboundQuery("");
    setInboundMessage("");
    setUsageDecision("");
    setUsageMaterialIds([]);
    setUsageQtys({});
    setInboundEditorOpen(true);
  }

  function selectInboundCandidate(candidate: ChainLinkCandidate) {
    setSelectedInboundId(candidate.targetId);
    const totals: Record<number, number> = {};
    for (const item of candidate.details ?? []) {
      if (!item.skuId || item.quantity == null) continue;
      for (const mapping of usageMappings.filter((row) => row.skuId === item.skuId)) {
        totals[mapping.consumableId] = (totals[mapping.consumableId] ?? 0) + item.quantity * Number(mapping.usagePerUnit || 0);
      }
    }
    setUsageMaterialIds(Object.keys(totals).map(Number));
    setUsageQtys(Object.fromEntries(Object.entries(totals).map(([id, value]) => [Number(id), String(value)])));
    setUsageDecision(Object.keys(totals).length > 0 ? "yes" : "");
  }

  async function saveInboundLink() {
    if (!usageDecision) { setInboundMessage("请先明确选择“有耗材使用”或“本次不使用耗材”"); return; }
    const usageItems = usageDecision === "yes"
      ? usageMaterialIds.map((id) => ({ consumable_id: id, quantity: usageQtys[id] || "" })).filter((item) => item.quantity.trim() !== "")
      : [];
    if (usageDecision === "yes" && usageItems.length === 0) { setInboundMessage("请选择耗材并填写使用数量"); return; }
    setEditorBusy(true);
    setInboundMessage("");
    try {
      if (!selectedInboundId) return;
      if (replaceInboundLinkId) {
        await procurementChainApi.replaceLink(replaceInboundLinkId, selectedInboundId, "采购工作台人工更换入库单", usageDecision === "yes", usageItems);
        setInboundMessage("入库单关联已更换");
      } else {
        await procurementChainApi.manualLink(order.orderId, "inbound", selectedInboundId, "采购工作台人工选择入库单", usageDecision === "yes", usageItems);
        setInboundMessage("入库单已人工关联");
      }
      setInboundEditorOpen(false);
      setSelectedInboundId(null);
      setReplaceInboundLinkId(null);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "入库单关联失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function correctInboundAmount(documentId: number) {
    if (!documentId) return;
    setDocAmountBusy(true);
    setInboundMessage("");
    try {
      const result = await procurementChainApi.recalcInboundAmount(documentId);
      setInboundMessage(`已按明细合计更正入库单金额：${fmtMoney(Number(result.after))}`);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "按明细更正失败");
    } finally {
      setDocAmountBusy(false);
    }
  }

async function saveInboundAmount(documentId: number, value: string, note: string) {
    if (!documentId || !value) return;
    setDocAmountBusy(true);
    setInboundMessage("");
    try {
      const result = await procurementChainApi.correctInboundAmount(documentId, value, note);
      setInboundMessage(`入库单金额已更正为 ${fmtMoney(Number(result.amount))}`);
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "更正金额失败");
    } finally {
      setDocAmountBusy(false);
    }
  }

  async function toggleOrderKind() {
    const next = order.orderKind === "consumable" ? "goods" : "consumable";
    const reason = next === "goods"
      ? "确认把该订单改为「正品」？\n\n当前判为耗材的原因：耗材档案 Excel 的「采购订货号」引用了本单号，或供应商名含包装类关键词。人工覆盖后不再受自动判定影响。"
      : "确认把该订单改为「耗材（包材）采购」？人工覆盖后不再受自动判定影响。";
    if (!window.confirm(reason)) return;
    setEditorBusy(true);
    setSkuMessage("");
    try {
      await procurementWorkbenchApi.setOrderKindOverride(order.orderId, next);
      setSkuMessage(next === "goods" ? "已人工改为「正品」" : "已人工改为「耗材」");
      await onOrderChanged();
    } catch (caught) {
      setSkuMessage(caught instanceof Error ? caught.message : "订单类型更新失败");
    } finally {
      setEditorBusy(false);
    }
  }

  async function removeInboundLink(linkId: number) {
    if (!window.confirm(`确认解除这张入库单与当前${CHANNELS[channelOf(order.platform)].label}订单的关联？入库原始数据不会删除。`)) return;
    setEditorBusy(true);
    setInboundMessage("");
    try {
      await procurementChainApi.deleteLink(linkId);
      setInboundMessage("入库单关联已解除，原始入库单仍保留在数据库中");
      await onOrderChanged();
    } catch (caught) {
      setInboundMessage(caught instanceof Error ? caught.message : "解除关联失败");
    } finally {
      setEditorBusy(false);
    }
  }

  return (
    <aside className="min-w-0 xl:sticky xl:top-[calc(var(--wb-header-h,162px)+8px)] xl:self-start">
      <div className="overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-[0_4px_18px_rgba(40,53,85,0.045)]">
        <div className="border-b border-slate-100 px-4 py-3.5">
          <div className="flex items-center justify-between text-[11px]">
            <span className="font-semibold text-slate-700">已选择订单</span>
            <span className="flex items-center gap-2">
              <button
                onClick={openMainEdit}
                title="编辑供应商 / 标题 / 金额等主档信息（修改会同步 1688 源单与采购副本，并写入审计日志）"
                className="rounded-md border border-indigo-200 bg-white px-2 py-1 text-[12px] font-medium text-indigo-600 hover:bg-indigo-50"
              >
                ✎ 编辑
              </button>
              <button
                disabled={deleteBusy}
                onClick={() => void deleteOrder()}
                title={order.orderId > 0 ? "从工作台移除该订单（1688 导入订单为软删除，数据可恢复）" : "删除该手工登记订单（无 1688 原件，不可恢复）"}
                className="rounded-md border border-red-200 bg-white px-2 py-1 text-[12px] font-medium text-red-500 hover:bg-red-50 disabled:opacity-40"
              >
                {deleteBusy ? "删除中…" : "🗑 删除订单"}
              </button>
              <span className="font-medium text-indigo-600">订单详情</span>
            </span>
          </div>
          <div className="mt-3 flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <PlatformBadge value={order.platform} />
                <button
                  onClick={() => void toggleOrderKind()}
                  disabled={editorBusy}
                  className="disabled:opacity-50"
                  title={order.orderKindOverride
                    ? `已人工覆盖为${order.orderKind === "consumable" ? "耗材" : "正品"}；点击切换类型`
                    : "类型为自动判定（耗材档案采购订货号引用 / 供应商关键词）；点击可人工切换 正品/耗材"}
                >
                  <OrderKindTag value={order.orderKind} />
                  <span aria-hidden className="ml-0.5 text-[9px] text-slate-300">✎</span>
                </button>
                <span className="truncate font-mono text-[13px] font-semibold text-slate-800">{order.orderNo}</span>
                <button onClick={() => navigator.clipboard?.writeText(order.orderNo)} className="shrink-0 text-slate-300 hover:text-indigo-500" title="复制订单号"><Icon name="copy" size={12} /></button>
                {closed ? (
                  <span className="shrink-0 rounded bg-slate-700 px-1.5 py-0.5 text-[11px] font-semibold text-white" title={`平台订单状态：${order.orderStatus || "已关闭"}`}>已关闭</span>
                ) : (
                  <>
                    <span
                      className={cx("shrink-0 rounded px-1.5 py-0.5 text-[11px] font-medium", statusClass(statusLabel({ orderStatus: order.orderStatus, stepStates })))}
                      title="最后一步未确认的环节"
                    >
                      {statusLabel({ orderStatus: order.orderStatus, stepStates })}
                    </span>
                    {order.externalPoId && editable && allocations.length > 0 && (
                      <>
                        <button
                          disabled={!canConfirm || skuAction !== ""}
                          onClick={() => void confirmPurchaseContent()}
                          title={confirmBlockReason || "确认采购内容并推进到下一阶段"}
                          className="shrink-0 rounded bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-600 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-40"
                        >{skuAction === "confirm" ? "确认中…" : "确认"}</button>
                        {!canConfirm && confirmBlockReason && (
                          <span
                            className="max-w-[260px] shrink truncate text-[11px] text-amber-600"
                            title={confirmBlockReason}
                          >{confirmBlockReason}</span>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
              <div className="mt-2 text-[12px] text-slate-400">下单时间：{fmtDateTime(order.orderDate)}　来源：{CHANNELS[channelOf(order.platform)].label}</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-slate-500">
                <span className="shrink-0 text-slate-400">供应商：</span>
                {order.supplier ? (
                  <button onClick={() => order.supplier && onOpenSupplier(order.supplier)} className="min-w-0 truncate font-medium text-slate-600 hover:text-indigo-600" title="查看供应商详情">{order.supplier}</button>
                ) : <span>—</span>}
                {detail.supplierHistory && (
                  <>
                    {detail.supplierHistory.orderCount >= 5 && <span className="shrink-0 rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] text-emerald-600">稳定合作</span>}
                    {detail.supplierHistory.orderCount === 1 && <span className="shrink-0 rounded bg-violet-50 px-1.5 py-0.5 text-[11px] text-violet-600">首次采购</span>}
                  </>
                )}
              </div>
              {detail.supplierHistory && (
                <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-slate-400" title="常购商品 / 未开票金额 / 最近一次采购">
                  <span>常购 <span className="font-medium tabular-nums text-slate-600">{detail.supplierHistory.oftenSkus.length}</span></span>
                  <span>未开票 <span className="font-medium tabular-nums text-slate-600">{fmtMoney(detail.supplierHistory.uninvoiced)}</span></span>
                  <span>最近采购 <span className="font-medium tabular-nums text-slate-600">{fmtDate(detail.supplierHistory.lastOrderDate)}</span></span>
                  <button onClick={() => order.supplier && onOpenSupplier(order.supplier)} className="text-indigo-500 hover:text-indigo-600">详情 ›</button>
                </div>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11px] text-slate-400" title="收货仓库 / 付款方式 / 订单状态 / 备注">
                <span>仓库 <span className="font-medium text-slate-600">{inbound.find((row) => row.warehouseName)?.warehouseName ?? "—"}</span></span>
                <span>付款 <span className="font-medium text-slate-600">{paidAmount > 0 ? "已关联" : "未关联"}</span></span>
                <span>状态 <span className="font-medium text-slate-600">{order.orderStatus || "—"}</span></span>
                <span className="min-w-0 truncate" title={order.title || undefined}>备注 <span className="font-medium text-slate-600">{order.title || "—"}</span></span>
              </div>
            </div>
            <div className="shrink-0 text-right">
              <div className="text-[11px] text-slate-400">订单金额</div>
              <div className="mt-1 text-[17px] font-semibold tabular-nums text-slate-900">{fmtMoney(order.amount)}</div>
            </div>
          </div>
        </div>
        <div className="space-y-2 p-3">
          {closed && (
            <div className="rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-[12px] text-slate-500">
              已关闭订单为只读 · 点右上角「🗑 删除订单」可从工作台移除（数据可恢复）
            </div>
          )}
          {skuMessage && (
            <div className={cx(
              "rounded-md border px-2.5 py-1.5 text-[12px]",
              skuMessage.includes("失败") || skuMessage.includes("未生效") || skuMessage.includes("未平衡") || skuMessage.includes("请")
                ? "border-amber-300 bg-amber-50 text-amber-800"
                : skuMessage.includes("已确认") || skuMessage.includes("已移除") || skuMessage.includes("已删除")
                  ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                  : "border-slate-200 bg-white text-slate-600"
            )}>
              {skuMessage}
            </div>
          )}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              {([["jackyun", "吉客云单据"], ["consumable", "耗材入库单"]] as const).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setDocTab(key)}
                  className={cx(
                    "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
                    docTab === key ? "bg-indigo-600 text-white" : "border border-slate-200 bg-white text-slate-500 hover:bg-slate-100"
                  )}
                >
                  {label}{key === "jackyun" && inbound.length > 0 ? ` · ${inbound.length}` : ""}
                </button>
              ))}
            </div>
            <span className="text-[10px] text-slate-400">单据按类型分页展示，两类单据互相独立</span>
          </div>
          {docTab === "jackyun" && (
          <DetailSection title="吉客云单据" badge={inbound.length > 0 ? `已关联 ${inbound.length} 张` : "待关联"}>
            {/* 采购单与入库单同源，通常无需单独关联；保留入口供需要时补录 */}
            <div className="rounded-md border border-indigo-100 bg-indigo-50/20 px-2 py-1.5">
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-1.5">
                  <span className="text-[12px] font-medium text-indigo-800">吉客云采购单</span>
                  {purchaseOrders.length > 0 ? (
                    <span className="shrink-0 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-600">
                      已关联 {purchaseOrders.length} 张
                    </span>
                  ) : order.jackyunPoBypassed ? (
                    <span className="shrink-0 rounded-full bg-teal-50 px-1.5 py-0.5 text-[11px] font-medium text-teal-600" title="吉客云侧未建采购单；金额以导入表格为准、入库已闭环，采购单步骤按口径放行">
                      已按 Excel 口径放行
                    </span>
                  ) : (
                    <span className="truncate text-[11px] text-slate-400">通常无需单独关联</span>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1.5">
                  {purchaseOrders.length > 0 && (
                    <button onClick={() => setPoListOpen((v) => !v)} className="text-[11px] text-indigo-600 hover:text-indigo-700">
                      {poListOpen ? "收起" : "查看"}
                    </button>
                  )}
                  {order.externalPoId && !closed ? (
                    <button disabled={poBusy} onClick={() => setPoEditorOpen((v) => !v)} className="rounded bg-indigo-600 px-2 py-1 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40">
                      {poEditorOpen ? "取消" : "关联采购单"}
                    </button>
                  ) : null}
                </div>
              </div>

              {poListOpen && purchaseOrders.length > 0 && (
                <div className="mt-1.5 space-y-1">
                  {purchaseOrders.map((row, index) => (
                    <div key={(row.purchNo || "po") + index} className="flex items-center justify-between gap-2 rounded bg-white/70 px-2 py-1.5 text-[12px]">
                      <span className="min-w-0 truncate">
                        <span className="font-mono font-medium text-indigo-600">{row.purchNo || "已关联采购单"}</span>
                        {row.relationKind === "merged" && <span className="ml-1.5 shrink-0 rounded bg-amber-100 px-1 py-px text-[11px] text-amber-700" title="多张采购订单共用这张采购单">合并</span>}
                        {row.relationKind === "split" && <span className="ml-1.5 shrink-0 rounded bg-violet-100 px-1 py-px text-[11px] text-violet-700" title="一张采购订单拆成多张采购单，此为其中之一">拆分</span>}
                        {row.allocAmount != null && row.allocAmount > 0 && <span className="ml-1.5 text-[11px] text-slate-500">分摊 {fmtMoney(row.allocAmount)}</span>}
                      </span>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="text-slate-500">{fmtMoney(row.amount)}　{row.status || ""}</span>
                        {row.linkId && order.externalPoId && !closed ? (
                          <button disabled={poBusy} onClick={() => void unlinkJackyunPo(row.linkId!)} className="rounded px-1.5 py-0.5 text-[11px] text-red-500 hover:bg-white disabled:opacity-40">解除</button>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {poClosure?.relevant && (
                <div className={cx("mt-1.5 flex flex-wrap items-center justify-between gap-2 rounded border px-2 py-1.5 text-[11px]",
                  poClosure.closed ? "border-emerald-100 bg-emerald-50/60 text-emerald-700" : "border-amber-200 bg-amber-50/70 text-amber-700")}>
                  <span>
                    采购单金额闭环：关联合计 <span className="font-semibold tabular-nums">{fmtMoney(poClosure.allocTotal)}</span>
                    {poClosure.gap != null && <> · 与实付差额 <span className="font-semibold tabular-nums">{fmtMoney(poClosure.gap)}</span></>}
                  </span>
                  {!poClosure.closed && <span>差额较大：可能存在合并/拆分未完整关联，请标注类型并填写分摊金额</span>}
                </div>
              )}

              {poEditorOpen && (
                <div className="mt-1.5 rounded border border-indigo-100 bg-indigo-50/30 p-2">
                  <div className="text-[11px] font-medium text-slate-700">关联吉客云采购单</div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-slate-200 bg-white px-2 py-1">
                    <span className="text-[11px] text-slate-400">关联类型：</span>
                    {([["", "普通"], ["merged", "合并（多单共一单）"], ["split", "拆分（一单分多单）"]] as const).map(([value, label]) => (
                      <label key={value || "normal"} className="flex items-center gap-1 text-[11px] text-slate-600">
                        <input type="radio" name="po-relation-kind" checked={poRelationKind === value} onChange={() => setPoRelationKind(value)} />
                        {label}
                      </label>
                    ))}
                  </div>
                  <div className="mt-1.5 flex items-center gap-1.5">
                    <input value={poLinkNo} onChange={(event) => setPoLinkNo(event.target.value)} placeholder="吉客云采购单号" className="h-7 flex-1 rounded border border-indigo-300 px-2 text-[12px] outline-none" />
                    <input value={poAllocAmount} onChange={(event) => setPoAllocAmount(event.target.value)} placeholder="分摊金额" className="h-7 w-28 rounded border border-indigo-300 px-2 text-right text-[12px] tabular-nums outline-none" />
                    <button disabled={poBusy} onClick={() => void linkJackyunPo()} className="h-7 rounded bg-indigo-600 px-2.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40">{poBusy ? "关联中…" : "关联"}</button>
                  </div>
                </div>
              )}

              {poMessage && <div className={cx("mt-1.5 text-[11px]", poMessage.includes("失败") || poMessage.includes("尚未") || poMessage.includes("请输入") ? "text-amber-600" : "text-emerald-600")}>{poMessage}</div>}
            </div>

            <div className="mt-2 rounded-lg border border-teal-100 bg-teal-50/20 p-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-1.5">
                <span className="text-[12px] font-semibold text-teal-800">吉客云入库单</span>
                <span className={cx("shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium",
                  inbound.length > 0 ? "bg-emerald-50 text-emerald-600" : "bg-amber-50 text-amber-700")}>
                  {inbound.length > 0 ? `已关联 ${inbound.length} 张` : "待关联"}
                </span>
              </div>
              <button disabled={editorBusy || (!order.fileOrderId && !order.externalPoId) || closed} onClick={() => openInboundPicker()} title={closed ? "订单已关闭，不可再关联入库单" : (!order.fileOrderId && !order.externalPoId) ? "该订单尚未建立采购主档，暂无法关联入库单" : "手工关联吉客云入库单"} className="shrink-0 rounded-md bg-indigo-50 px-2.5 py-1.5 text-[12px] font-medium text-indigo-600 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-40">手工关联入库单</button>
            </div>
        {inbound.length > 0 ? (
          <div className="mt-2 space-y-2">
            {inbound.map((row, index) => (
              <InboundDocCard
                key={(row.linkId ?? row.goodsdocNo ?? "inbound") + "-" + index}
                doc={row}
                allocations={allocationsByDoc.get(row.documentId ?? -1) ?? []}
                closed={closed}
                editable={editable}
                usageMaterials={usageMaterials}
                usageMappings={usageMappings}
                busy={editorBusy || docAmountBusy}
                onChanged={onOrderChanged}
                onRequestReplace={(linkId) => openInboundPicker(linkId)}
                onRemove={(linkId) => void removeInboundLink(linkId)}
                onCorrectAmount={(documentId) => void correctInboundAmount(documentId)}
                onSaveAmount={(documentId, value) => void saveInboundAmount(documentId, value, "吉客云录入金额错误更正")}
                onEditAllocation={(alloc, consumable) => {
                  setEditingAllocation(alloc.id ?? null);
                  setSelectedSkuId(alloc.skuId ?? null);
                  setSkuEntry(`${alloc.skuCode ?? ""}${alloc.goodsName ? ` · ${alloc.goodsName}` : ""}`);
                  setSkuQty(String(alloc.quantity ?? 1));
                  setSkuPrice(String(alloc.unitPrice ?? 0));
                  seedSkuConsumable(alloc.skuId ?? null, String(alloc.quantity ?? 1), consumable);
                  setSkuEditorTargetDoc(row.documentId ?? null);
                  setSkuEditorOpen(true);
                }}
                onAddSku={(documentId) => {
                  setEditingAllocation(null);
                  setSelectedSkuId(null);
                  setSkuEntry("");
                  setSkuQty("1");
                  setSkuPrice("");
                  seedSkuConsumable(null, "1");
                  setSkuEditorTargetDoc(documentId);
                  setSkuEditorOpen(true);
                }}
                onRemoveAllocation={(id) => void removeAllocation(id)}
                onNotify={setSkuMessage}
                pendingConsumableUpdate={pendingConsumableUpdate?.documentId === row.documentId ? pendingConsumableUpdate : null}
                onPendingConsumableUpdateApplied={() => setPendingConsumableUpdate(null)}
                editor={skuEditorOpen && editable && skuEditorTargetDoc != null && skuEditorTargetDoc === row.documentId ? skuEditorRow : null}
              />
            ))}
            {inbound.some((row) => row.linkId && !row.consumableUsageDecided) && (
              <div className="rounded-md border border-amber-200 bg-amber-50/70 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
                有入库单未登记耗材 —— 在对应单据卡片底部「耗材登记」选「有耗材」或「不使用」后点「保存耗材」。确认采购内容前必须全部明确。
              </div>
            )}
          </div>
        ) : <div className="mt-2 rounded-md border border-amber-100 bg-amber-50/60 px-2.5 py-2 text-[12px] text-amber-700">尚未关联入库单，点击「手工关联入库单」从候选中选择。</div>}
{inboundEditorOpen && (
          <div className="mt-2 rounded-lg border border-indigo-100 bg-indigo-50/30 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[12px] font-medium text-slate-700">{replaceInboundLinkId ? "更换当前入库单" : "选择要关联的吉客云入库单"}</span>
              <button onClick={() => setInboundEditorOpen(false)} className="text-[11px] text-slate-400 hover:text-slate-600">取消</button>
            </div>
            <div className="mt-2 flex items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5">
              <Icon name="search" size={13} />
              <input value={inboundQuery} onChange={(event) => setInboundQuery(event.target.value)} placeholder="搜索入库单号、供应商或仓库" className="h-8 min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-slate-400" />
            </div>
            <div className="mt-2 max-h-52 space-y-1 overflow-y-auto">
              {inboundCandidateLoading ? <div className="py-4 text-center text-[12px] text-slate-400">正在读取入库单数据库…</div> : matchingInboundCandidates.length > 0 ? matchingInboundCandidates.map((candidate) => {
                const linkedElsewhere = candidate.linkedOrderNos.filter((no) => no !== order.orderNo);
                return (
                    <button key={candidate.targetId} disabled={candidate.currentlyLinked && !replaceInboundLinkId} onClick={() => selectInboundCandidate(candidate)} className={cx(
                  "flex w-full items-center justify-between gap-2 rounded-md border px-2.5 py-2 text-left disabled:cursor-not-allowed disabled:opacity-50",
                  selectedInboundId === candidate.targetId ? "border-indigo-300 bg-white ring-1 ring-indigo-100" : "border-transparent bg-white/75 hover:border-slate-200"
                )}>
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5"><span className="truncate font-mono text-[12px] font-medium text-slate-700">{candidate.targetNo}</span>{candidate.pendingSuggestion && <span className="shrink-0 rounded bg-amber-50 px-1 py-px text-[8px] text-amber-600">待确认建议</span>}{candidate.currentlyLinked && <span className="shrink-0 rounded bg-emerald-50 px-1 py-px text-[8px] text-emerald-600">本单已关联</span>}{candidate.previouslyRejected && <span className="shrink-0 rounded bg-slate-100 px-1 py-px text-[8px] text-slate-500">曾解除，可重选</span>}</span>
                    <span className="mt-0.5 block truncate text-[11px] text-slate-400">{fmtDateTime(candidate.targetDate)} · {candidate.targetSupplier || "未注供应商"} · {candidate.warehouseName || "未注仓库"}</span>
                    <span className="mt-0.5 block truncate text-[11px] text-indigo-500">{candidate.reason}{linkedElsewhere.length > 0 ? ` · 已关联其他订单 ${linkedElsewhere.slice(0, 2).join("、")}` : ""}</span>
                  </span>
                  <span className="shrink-0 text-right"><span className="block text-[12px] font-medium tabular-nums text-slate-700">{fmtMoney(candidate.targetAmount)}</span><span className="mt-0.5 block text-[8px] text-slate-400">参考 {(candidate.score * 100).toFixed(0)}%</span></span>
                </button>
              );
            }) : <div className="py-4 text-center text-[12px] text-slate-400">没有符合搜索条件的入库单</div>}
            </div>
            {selectedInboundId && (
              <div className="mt-2 rounded-md border border-amber-200 bg-amber-50/60 p-2.5">
                <div className="text-[12px] font-semibold text-slate-800">关联前必答：本次入库产生了耗材出库吗？</div>
                <div className="mt-1.5 flex gap-2">
                  <button type="button" onClick={() => setUsageDecision("yes")} className={cx("flex-1 rounded-md border px-2 py-1.5 text-[12px] transition-colors",
                    usageDecision === "yes" ? "border-amber-400 bg-amber-100 font-semibold text-amber-800" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>有耗材使用</button>
                  <button type="button" onClick={() => { setUsageDecision("no"); setUsageMaterialIds([]); setUsageQtys({}); }} className={cx("flex-1 rounded-md border px-2 py-1.5 text-[12px] transition-colors",
                    usageDecision === "no" ? "border-slate-500 bg-slate-200 font-semibold text-slate-700" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>本次不使用耗材</button>
                </div>
                {usageDecision === "yes" && <>
                  <div className="mt-2 text-[12px] font-medium text-slate-700">用了哪些耗材？<span className="ml-1 font-normal text-[8.5px] text-slate-400">数量必填，保存后计入耗材台账</span></div>
                  <SearchableSelect
                    ariaLabel="添加耗材"
                    placeholder="＋ 添加耗材（已匹配的会自动带出）"
                    className="mt-1.5 w-full [&>button]:border-amber-300"
                    value=""
                    onChange={(next) => {
                      const id = Number(next);
                      if (id && !usageMaterialIds.includes(id)) { setUsageMaterialIds([...usageMaterialIds, id]); setUsageQtys({ ...usageQtys, [id]: "" }); }
                    }}
                    options={usageMaterials.filter((row) => !usageMaterialIds.includes(row.id)).map((row) => ({
                      value: String(row.id), label: `${row.code} · ${row.name}`, keywords: `${row.code} ${row.name}`,
                    }))}
                  />
                  <div className="mt-2 space-y-1.5">
                    {usageMaterialIds.map((id) => {
                      const material = usageMaterials.find((row) => row.id === id);
                      if (!material) return null;
                      return (
                        <div key={id} className="flex items-center gap-2 rounded-md bg-white px-2.5 py-2 ring-1 ring-slate-100">
                          <span className="min-w-0 flex-1 truncate text-[12px] text-slate-700"><span className="font-mono text-[9px] text-slate-400">{material.code}</span> {material.name}</span>
                          <input required value={usageQtys[id] ?? ""} onChange={(event) => setUsageQtys({ ...usageQtys, [id]: event.target.value })} placeholder="数量" className="h-7 w-16 rounded border border-slate-200 px-2 text-center text-[12px] tabular-nums outline-none focus:border-amber-400" />
                          <span className="w-8 shrink-0 text-[11px] text-slate-400">{material.unit}</span>
                          <button type="button" onClick={() => { setUsageMaterialIds(usageMaterialIds.filter((value) => value !== id)); const next = { ...usageQtys }; delete next[id]; setUsageQtys(next); }} className="shrink-0 rounded px-1.5 py-0.5 text-[11px] text-red-400 hover:bg-red-50 hover:text-red-600">移除</button>
                        </div>
                      );
                    })}
                    {usageMaterialIds.length === 0 && <div className="rounded-md border border-dashed border-amber-300 bg-white/60 px-2 py-2.5 text-center text-[12px] text-amber-700">从上方「＋ 添加耗材」下拉选择耗材，并填写使用数量</div>}
                  </div>
                </>}
              </div>
            )}
            <div className="mt-2 flex items-center justify-between gap-2 border-t border-indigo-100 pt-2">
              <span className="truncate text-[11px] text-slate-500">
                {selectedInbound ? `已选 ${selectedInbound.targetNo} · ${fmtMoney(selectedInbound.targetAmount)}` : "请选择一张入库单"}
              </span>
              <button disabled={editorBusy || !selectedInboundId || !usageDecision} onClick={() => void saveInboundLink()} className="shrink-0 rounded-md bg-indigo-600 px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40">{editorBusy ? "保存中…" : replaceInboundLinkId ? "确认更换" : "确认关联"}</button>
            </div>
          </div>
        )}
        {inboundMessage && <div className={cx("mt-2 text-[11px]", inboundMessage.includes("失败") || inboundMessage.includes("暂不能") ? "text-red-500" : "text-emerald-600")}>{inboundMessage}</div>}
            </div>
          {(manualAllocations.length > 0 || (skuEditorOpen && skuEditorTargetDoc == null)) && (
            <div className="mt-2 rounded-lg border border-dashed border-slate-200 bg-slate-50/40 px-2.5 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-slate-600">未关联入库单的明细（手工补录）</span>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-400">{manualAllocations.length} 行 · {fmtMoney(manualTotal)}</span>
                  {editable && <button
                    onClick={() => {
                      setEditingAllocation(null);
                      setSelectedSkuId(null);
                      setSkuEntry("");
                      setSkuQty("1");
                      setSkuPrice("");
                      resetSkuConsumable();
                      setSkuEditorTargetDoc(null);
                      setSkuEditorOpen(true);
                    }}
                    className="rounded bg-indigo-50 px-1.5 py-0.5 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100"
                  >＋ 新建</button>}
                </div>
              </div>
              <table className="mt-1.5 w-full table-fixed border-collapse text-[12px]">
                <colgroup>
                  <col style={{ width: "38%" }} />
                  <col style={{ width: "9%" }} />
                  <col style={{ width: "11%" }} />
                  <col style={{ width: "12%" }} />
                  <col style={{ width: "20%" }} />
                  <col style={{ width: "10%" }} />
                </colgroup>
                <thead>
                  <tr className="text-[11px] text-slate-400">
                    <th className="border-b border-slate-200 py-1 pr-2 text-left font-medium">SKU 明细</th>
                    <th className="border-b border-slate-200 py-1 px-1 text-right font-medium">数量</th>
                    <th className="border-b border-slate-200 py-1 px-1 text-right font-medium">单价</th>
                    <th className="border-b border-slate-200 py-1 px-1 text-right font-medium">总价</th>
                    <th className="border-b border-slate-200 py-1 px-1 text-left font-medium">耗材</th>
                    <th className="border-b border-slate-200 py-1 pl-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {manualAllocations.map((row, index) => (
                    <tr key={(row.id ?? row.skuCode ?? "manual") + "-" + index} className="whitespace-nowrap border-b border-slate-100 last:border-0">
                      <td className="py-1.5 pr-2">
                        <div className="flex min-w-0 items-center gap-1.5">
                          <span className="shrink-0 font-mono text-[11px] text-indigo-500">{row.skuCode || "未关联SKU"}</span>
                          <span className="min-w-0 truncate text-[12px] font-medium text-slate-700" title={`${row.skuCode || ""} ${row.goodsName || "未命名商品"}`}>{row.goodsName || "未命名商品"}</span>
                        </div>
                      </td>
                      <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{row.quantity ?? "—"}</td>
                      <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{fmtMoney(row.unitPrice)}</td>
                      <td className="py-1.5 px-1 text-right tabular-nums font-medium text-slate-700">{fmtMoney(row.amount)}</td>
                      <td className="py-1.5 px-1 text-[11px] text-slate-400">—</td>
                      <td className="py-1.5 pl-2">
                        {editable && row.id ? (
                          <div className="flex gap-2">
                            <button disabled={editorBusy} onClick={() => { setEditingAllocation(row.id!); setSelectedSkuId(row.skuId ?? null); setSkuEntry(`${row.skuCode ?? ""}${row.goodsName ? ` · ${row.goodsName}` : ""}`); setSkuQty(String(row.quantity ?? 1)); setSkuPrice(String(row.unitPrice ?? 0)); resetSkuConsumable(); setSkuEditorTargetDoc(null); setSkuEditorOpen(true); }} className="text-[11px] text-indigo-600 disabled:opacity-40">修改</button>
                            <button disabled={editorBusy} onClick={() => void removeAllocation(row.id as number)} title="删除这条 SKU 明细" className="text-[11px] text-red-400 hover:text-red-600 disabled:opacity-40">删除</button>
                          </div>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                  {skuEditorOpen && skuEditorTargetDoc == null ? skuEditorRow : null}
                </tbody>
              </table>
            </div>
          )}
        {allocations.length === 0 && (
          <div className="mt-2 flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <span className={cx("text-[12px] tabular-nums", remainingAmount !== null && Math.abs(remainingAmount) < 0.005 ? "text-emerald-600" : remainingAmount !== null && remainingAmount < 0 ? "text-red-600" : "text-amber-600")}>
              {remainingAmount !== null && remainingAmount < -0.005 ? "超分配" : remainingAmount !== null && remainingAmount > 0.005 ? "待分配" : "金额已平衡"} {fmtMoney(remainingAmount)}
            </span>
            {editable ? (
              <button onClick={() => { setEditingAllocation(null); setSelectedSkuId(null); setSkuEntry(""); setSkuQty("1"); setSkuPrice(""); resetSkuConsumable(); setSkuEditorTargetDoc(null); setSkuEditorOpen((open) => !open); }} className="rounded-md bg-indigo-50 px-2.5 py-1.5 text-[12px] font-medium text-indigo-600 hover:bg-indigo-100">
                {skuEditorOpen ? "收起分配" : "新建 SKU 明细"}
              </button>
            ) : <button disabled={editorBusy || closed} onClick={() => void reopenPurchase()} className="text-[12px] text-indigo-600 disabled:opacity-40 disabled:cursor-not-allowed">重新编辑采购内容</button>}
          </div>
        )}
          </DetailSection>
          )}
          {docTab === "consumable" && (
            <OrderConsumableSection order={order} materials={usageMaterials} />
          )}
          <DetailSection title="费用分摊" badge="原单总额 / 实际分配">
          <div className="grid grid-cols-8 items-end gap-1.5">
            <DetailField label="商品金额" value={fmtMoney(order.goodsTotal ?? order.amount)} strong />
            <DetailField label="运费" value={fmtMoney(order.freight ?? 0)} strong />
            <DetailField label="优惠/抵扣" value={fmtMoney(order.discount ?? 0)} strong />
            <DetailField label="其它费用" value={fmtMoney(extraAmount)} strong />
            <DetailField label="应付款" value={fmtMoney(payable)} strong />
            <DetailField label="已付款" value={fmtMoney(paidAmount)} strong />
            <DetailField label="未付款" value={fmtMoney(unpaidAmount)} strong />
            {adjEditing ? (
              <div className="min-w-0 space-y-1">
                <input autoFocus onFocus={(e) => e.target.select()} value={adjValue}
                  onChange={(event) => setAdjValue(event.target.value)} placeholder="如 -3.50"
                  className="h-7 w-full rounded border border-indigo-300 px-1 text-right text-[12px] tabular-nums outline-none" />
                <input value={adjNote} onChange={(event) => setAdjNote(event.target.value)} placeholder="原因（红包等）"
                  className="h-7 w-full rounded border border-slate-200 px-1 text-[11px] outline-none" />
                <div className="flex justify-end gap-1.5">
                  <button disabled={editorBusy} onClick={() => void saveAdjustment()} className="text-[11px] font-medium text-indigo-600 disabled:opacity-40">保存</button>
                  <button onClick={() => { setAdjEditing(false); setAdjValue(""); setAdjNote(""); }} className="text-[11px] text-slate-400 hover:text-slate-600">取消</button>
                </div>
              </div>
            ) : (
              <button type="button" disabled={!order.externalPoId} title="1688 红包等导致开票金额（准确）与订单实付有零头差时使用；分配平衡按 实付+微调 计算"
                onClick={() => { setAdjValue(order.adjustmentAmount != null ? String(order.adjustmentAmount) : ""); setAdjNote(order.adjustmentNote ?? ""); setAdjEditing(true); }}
                className="min-w-0 text-left disabled:cursor-not-allowed">
                <div className="text-[11px] text-slate-400">1688微调 ✎</div>
                <div className={cx("mt-0.5 truncate text-[12px] font-semibold tabular-nums",
                  order.adjustmentAmount != null ? "text-amber-700" : "text-slate-400")}>
                  {order.adjustmentAmount != null ? `${order.adjustmentAmount > 0 ? "+" : ""}${fmtMoney(order.adjustmentAmount)}` : "未调整"}
                </div>
              </button>
            )}
          </div>
        {expenses.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {expenses.map((row, index) => (
              <span key={(row.id ?? "exp") + "-" + index} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[12px] text-slate-600">
                {EXPENSE_LABEL[row.expenseType ?? ""] ?? row.expenseType ?? "费用"} {fmtMoney(row.amount)}
                {editable && row.id ? (
                  <button disabled={editorBusy} onClick={() => void removeExpense(row.id as number)} title="删除这条费用" className="text-red-400 hover:text-red-600 disabled:opacity-40">×</button>
                ) : null}
              </span>
            ))}
          </div>
        )}
        {editable ? (
          <div className="mt-2 flex items-end gap-1.5 border-t border-slate-100 pt-2">
            <select value={expenseType} onChange={(event) => setExpenseType(event.target.value)} className="h-7 rounded-md border border-slate-200 bg-white px-1.5 text-[12px] text-slate-700 outline-none focus:border-indigo-300">
              {Object.entries(EXPENSE_LABEL).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
            <input value={expenseAmount} onChange={(event) => setExpenseAmount(event.target.value)} placeholder="金额" className="h-7 w-20 rounded-md border border-slate-200 bg-white px-2 text-[12px] text-slate-700 outline-none focus:border-indigo-300" />
            <button disabled={editorBusy || !expenseAmount} onClick={() => void addExpense()} className="h-7 rounded-md bg-indigo-50 px-2.5 text-[12px] font-medium text-indigo-600 hover:bg-indigo-100 disabled:opacity-40">登记费用</button>
            {expenseMessage && <span className={cx("text-[11px]", expenseMessage.includes("失败") ? "text-red-500" : "text-emerald-600")}>{expenseMessage}</span>}
          </div>
        ) : null}
          </DetailSection>
          <RelatedRecordsPanel detail={detail} ensurePo={ensureExternalPoId} onChanged={onOrderChanged} />
          {nextStatus && !closed ? (
        <div className="flex items-center justify-between gap-2 rounded-lg border border-slate-200/80 bg-white px-3 py-2">
          <span className="text-[12px] text-slate-500">当前状态：<span className="font-medium text-slate-700">{PO_STATUS[order.purchaseStatus ?? ""] ?? order.purchaseStatus}</span></span>
          <button disabled={editorBusy || closed} onClick={() => void advanceStatus()} className="rounded-md bg-white px-2.5 py-1.5 text-[12px] font-medium text-indigo-600 ring-1 ring-slate-200 hover:bg-slate-50 disabled:opacity-40">
            流转到「{PO_STATUS[nextStatus] ?? nextStatus}」
          </button>
        </div>
          ) : null}
        </div>
      </div>
      {mainEditOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/35 p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) setMainEditOpen(false); }} role="dialog" aria-modal="true" aria-label="编辑订单主档">
          <form onSubmit={saveMainEdit} className="max-h-[90vh] w-full max-w-xl overflow-y-auto rounded-2xl bg-white p-5 shadow-2xl">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-base font-semibold text-slate-800">编辑订单主档</h3>
                <p className="mt-1 text-xs text-slate-400">{order.orderNo} · 修改会同步 1688 源单与采购副本，并写入审计日志</p>
              </div>
              <button type="button" onClick={() => setMainEditOpen(false)} aria-label="关闭编辑订单" className="text-xl leading-none text-slate-300 hover:text-slate-500">×</button>
            </div>
            {mainError && <div className="mt-3 rounded-lg bg-red-50 p-2.5 text-xs text-red-600">{mainError}</div>}
            <div className="mt-4 grid grid-cols-2 gap-3 text-xs">
              <label className="col-span-2">供应商<input required value={mainForm.supplier} onChange={(event) => setMainForm({ ...mainForm, supplier: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
              <label className="col-span-2">标题 / 备注<input value={mainForm.title} onChange={(event) => setMainForm({ ...mainForm, title: event.target.value })} placeholder="可空" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
              {order.fileOrderId != null ? (<>
                <label>商品金额（元）<input required type="number" min="0" step="0.01" value={mainForm.goods} onChange={(event) => setMainForm({ ...mainForm, goods: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>运费（元）<input required type="number" min="0" step="0.01" value={mainForm.freight} onChange={(event) => setMainForm({ ...mainForm, freight: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>优惠 / 调整（元）<input type="number" step="0.01" value={mainForm.discount} onChange={(event) => setMainForm({ ...mainForm, discount: event.target.value })} title="按 1688 导入口径：正数从应付金额中扣减" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>实付金额（元）<input required type="number" min="0" step="0.01" value={mainForm.paid} onChange={(event) => setMainForm({ ...mainForm, paid: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
              </>) : (<>
                <label>下单日期<input type="date" value={mainForm.date} onChange={(event) => setMainForm({ ...mainForm, date: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>采购渠道<input value={mainForm.platform} onChange={(event) => setMainForm({ ...mainForm, platform: event.target.value })} placeholder="1688 / pdd / taobao / other" className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>订单金额（元）<input required type="number" min="0" step="0.01" value={mainForm.orderAmount} onChange={(event) => setMainForm({ ...mainForm, orderAmount: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
                <label>实付金额（元）<input required type="number" min="0" step="0.01" value={mainForm.paid} onChange={(event) => setMainForm({ ...mainForm, paid: event.target.value })} className="mt-1.5 h-9 w-full rounded-lg border border-slate-200 px-3 text-xs text-slate-700 outline-none focus:border-indigo-400" /></label>
              </>)}
            </div>
            <p className="mt-3 text-[11px] leading-5 text-slate-400">1688 订单的副本金额按「商品 + 运费 − 优惠」自动同步；SKU 分配平衡目标 = 实付 + 1688微调。金额请按 1688 后台或发票实际数字填写。</p>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" onClick={() => setMainEditOpen(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-xs text-slate-600">取消</button>
              <button disabled={mainSaving} className="rounded-lg bg-indigo-600 px-4 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50">{mainSaving ? "保存中…" : "保存修改"}</button>
            </div>
          </form>
        </div>
      )}
    </aside>
  );
}

/** 耗材订单专属：直接在本平台登记耗材入库单（HC 单）并收货进耗材库台账。 */
const CONSUMABLE_PO_STATUS: Record<string, { label: string; cls: string }> = {
  ordered: { label: "待收货", cls: "bg-amber-50 text-amber-600" },
  partial: { label: "部分收货", cls: "bg-amber-50 text-amber-600" },
  received: { label: "已收齐", cls: "bg-emerald-50 text-emerald-600" },
  cancelled: { label: "已取消", cls: "bg-slate-100 text-slate-500" },
};

function OrderConsumableSection({ order, materials }: { order: WorkbenchOrderRow; materials: ConsumableRow[] }) {
  const sourceOrderId = order.fileOrderId;
  const [purchases, setPurchases] = useState<ConsumablePurchaseRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState<{ orderedOn: string; lines: Array<{ consumable_id: string; quantity: string; unit_cost: string; total_amount: string }> }>({
    orderedOn: inputDate(new Date()),
    lines: [{ consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }],
  });
  const [receivingId, setReceivingId] = useState<number | null>(null);
  const [receiveDate, setReceiveDate] = useState(inputDate(new Date()));
  const [receiveQtys, setReceiveQtys] = useState<Record<number, string>>({});
  // 单价自动均摊：单价 = 订单实付 ÷ 总数量，让合计与实付精确一致；手动改单价会自动关闭。
  const [autoUnitCost, setAutoUnitCost] = useState(true);
  // 行内编辑（采购量 / 单价 / 总金额）；整单删除 / 恢复 cancelled → ordered 的就地反馈。
  const [editingLine, setEditingLine] = useState<{ purchaseId: number; line: ConsumablePurchaseItem } | null>(null);
  const [editQty, setEditQty] = useState("");
  const [editCost, setEditCost] = useState("");
  const [editTotal, setEditTotal] = useState("");

  const load = useCallback(async () => {
    if (sourceOrderId == null && !order.orderNo) { setPurchases([]); setLoading(false); return; }
    setLoading(true);
    // 统一按订单号圈定：1688 单看 source 原件单号，工作流独有单看 reference_no，
    // 避免 source_order_id 为空时漏出其他订单的耗材入库单。
    try { setPurchases(await consumablesApi.purchases("", undefined, order.orderNo)); setError(""); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "耗材采购单加载失败"); }
    finally { setLoading(false); }
  }, [sourceOrderId, order.orderNo]);
  useEffect(() => { void load(); }, [load]);

  // 总进度：跨本单全部耗材入库单汇总（已取消的不计）。
  const activePurchases = purchases.filter((row) => row.status !== "cancelled");
  const progress = activePurchases.reduce(
    (acc, row) => {
      for (const line of row.items) {
        acc.qty += Number(line.quantity) || 0;
        acc.received += Number(line.receivedQty) || 0;
        acc.amount += Number(line.quantity) * Number(line.unitCost) || 0;
        acc.receivedAmount += (Number(line.receivedQty) || 0) * Number(line.unitCost) || 0;
      }
      return acc;
    },
    { qty: 0, received: 0, amount: 0, receivedAmount: 0 },
  );
  const progressPct = progress.qty > 0 ? Math.min(100, Math.round((progress.received / progress.qty) * 100)) : 0;

  // 建单表单的自动单价：实付 ÷ 总数量（10 位小数），合计与订单实付严格一致（发票口径）。
  const orderTotal = Number(order.paidAmount ?? order.amount ?? 0);
  const formTotalQty = form.lines.reduce((sum, line) => sum + Number(line.quantity || 0), 0);
  const autoUnit = formTotalQty > 0 && orderTotal > 0 ? Number((orderTotal / formTotalQty).toFixed(10)) : 0;
  function applyAutoUnitCost(lines: Array<{ consumable_id: string; quantity: string; unit_cost: string; total_amount: string }>) {
    const totalQty = lines.reduce((sum, line) => sum + Number(line.quantity || 0), 0);
    if (!autoUnitCost || totalQty <= 0 || orderTotal <= 0) return lines;
    const unit = String(Number((orderTotal / totalQty).toFixed(10)));
    return lines.map((line) => {
      const q = Number(line.quantity || 0);
      const cost = line.quantity ? unit : line.unit_cost;
      return { ...line, unit_cost: cost, total_amount: q > 0 ? (q * Number(cost)).toFixed(4) : line.total_amount };
    });
  }
  // 每行三 input（数量/总金额/单价）互相推算，跟 inline 编辑保持一致。
  function setLineField(index: number, patch: Partial<{ consumable_id: string; quantity: string; unit_cost: string; total_amount: string }>) {
    setForm({ ...form, lines: form.lines.map((item, i) => i === index ? { ...item, ...patch } : item) });
  }
  function onCreateQtyChange(index: number, value: string) {
    const q = Number(value);
    const cost = Number(form.lines[index].unit_cost);
    const total = q > 0 && Number.isFinite(cost) ? (q * cost).toFixed(4) : "";
    setLineField(index, { quantity: value, total_amount: total });
  }
  function onCreateTotalChange(index: number, value: string) {
    const t = Number(value);
    const q = Number(form.lines[index].quantity);
    // 单价 10 位小数：让合计与订单实付严格对齐（发票口径）。
    const cost = q > 0 && Number.isFinite(t) ? (t / q).toFixed(10) : form.lines[index].unit_cost;
    setLineField(index, { total_amount: value, unit_cost: String(cost) });
  }
  function onCreateCostChange(index: number, value: string) {
    if (autoUnitCost) setAutoUnitCost(false);
    const c = Number(value);
    const q = Number(form.lines[index].quantity);
    const total = q > 0 && Number.isFinite(c) ? (c * q).toFixed(4) : "";
    setLineField(index, { unit_cost: value, total_amount: total });
  }

  async function createPurchase(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const items = form.lines
      .filter((line) => line.consumable_id && line.quantity)
      .map((line) => ({ consumable_id: Number(line.consumable_id), quantity: line.quantity, unit_cost: line.unit_cost || "0" }));
    if (!items.length) { setError("请至少选择一种耗材并填写数量"); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      await consumablesApi.createPurchase({
        request_key: newRequestKey(),
        supplier_name: order.supplier || "未记录供应商",
        ordered_on: form.orderedOn || inputDate(new Date()),
        source_order_id: sourceOrderId,
        reference_no: order.orderNo,
        note: "采购工作台耗材订单登记",
        items,
      });
      setCreating(false);
      setForm({ orderedOn: inputDate(new Date()), lines: [{ consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }] });
      setMessage("耗材入库单已建立；到货后在本区块「登记收货」，实收数量会计入本平台耗材库存台账。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "耗材采购单创建失败");
    } finally {
      setBusy(false);
    }
  }

  function startReceive(row: ConsumablePurchaseRow) {
    setReceivingId(row.id);
    setReceiveDate(inputDate(new Date()));
    setReceiveQtys(Object.fromEntries(row.items.map((line) => [line.id, ""])));
    setError(""); setMessage("");
  }

  async function saveReceive(row: ConsumablePurchaseRow) {
    if (busy) return;
    const items = row.items
      .filter((line) => (receiveQtys[line.id] ?? "").trim() !== "" && Number(receiveQtys[line.id]) !== 0)
      .map((line) => ({ item_id: line.id, quantity: receiveQtys[line.id] }));
    if (!items.length) { setError("请填写本次实收数量，未到货的耗材留空"); return; }
    setBusy(true); setError("");
    try {
      await consumablesApi.receivePurchase(row.id, {
        request_key: newRequestKey(),
        received_on: receiveDate || inputDate(new Date()),
        note: `来源${CHANNELS[channelOf(order.platform)].label}订单 ${order.orderNo}`,
        items,
      });
      setReceivingId(null);
      setMessage("收货已登记，实收数量已计入耗材库存。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "收货登记失败");
    } finally {
      setBusy(false);
    }
  }

  // ===== 行内编辑（数量/单价/总金额）、整单删除、cancelled 恢复 =====
  function startEditLine(purchaseId: number, line: ConsumablePurchaseItem) {
    setEditingLine({ purchaseId, line });
    setEditQty(line.quantity);
    setEditCost(line.unitCost);
    // 总金额 = 数量 × 单价（4 位小数），与后端 amount 字段对齐。
    const t = (Number(line.quantity) * Number(line.unitCost)).toFixed(4);
    setEditTotal(t);
    setError(""); setMessage("");
  }
  function cancelEditLine() { setEditingLine(null); setEditQty(""); setEditCost(""); setEditTotal(""); }

  // 三个 input 互相推算：改任一，其余自动跟着算。
  // - 改数量 → 总金额 = 数量 × 单价
  // - 改总金额 → 单价 = 总金额 ÷ 数量
  // - 改单价 → 总金额 = 单价 × 数量
  // 存盘仍以「数量 + 单价」提交（与后端 PATCH items 协议一致）。
  function onEditQtyChange(value: string) {
    setEditQty(value);
    const q = Number(value);
    if (q > 0) setEditTotal((Number(editCost) * q).toFixed(4));
  }
  function onEditTotalChange(value: string) {
    setEditTotal(value);
    const t = Number(value);
    const q = Number(editQty);
    // 单价 10 位小数：让合计与订单实付严格对齐（发票口径），如 800 ÷ 1050 = 0.7619047619。
    if (q > 0) setEditCost((t / q).toFixed(10));
  }
  function onEditCostChange(value: string) {
    setEditCost(value);
    const c = Number(value);
    const q = Number(editQty);
    if (q > 0) setEditTotal((c * q).toFixed(4));
  }

  async function saveEditLine(row: ConsumablePurchaseRow) {
    if (busy || !editingLine) return;
    const qty = Number(editQty);
    const cost = Number(editCost);
    if (!Number.isFinite(qty) || qty <= 0) { setError("采购数量必须大于 0"); return; }
    if (!Number.isFinite(cost) || cost < 0) { setError("单价不能为负"); return; }
    // 整张单整体 PATCH 上去——后端按 consumable_id 全量对齐（增量更新会失同步）。
    const items = row.items.map((it) => it.id === editingLine.line.id
      ? { consumable_id: it.consumableId, quantity: String(qty), unit_cost: String(cost) }
      : { consumable_id: it.consumableId, quantity: it.quantity, unit_cost: it.unitCost },
    );
    setBusy(true); setError("");
    try {
      await consumablesApi.updatePurchase(row.id, { items });
      setEditingLine(null);
      setEditQty(""); setEditCost("");
      setMessage(`已更新 ${editingLine.line.name}（${editingLine.line.code}）的采购量/单价`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "耗材明细保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function doDeletePurchase(row: ConsumablePurchaseRow) {
    if (busy) return;
    const hasReceived = row.items.some((it) => Number(it.receivedQty) > 0);
    const tip = hasReceived
      ? `耗材入库单 ${row.number} 已有收货记录：删除时会自动生成负数冲销流水，把已入库存的 ${row.items.reduce((s, it) => s + Number(it.receivedQty || 0), 0)} 件从对应仓回冲（原收货流水保留可查）。确定删除整单？`
      : `耗材入库单 ${row.number} 将被物理删除（无收货记录，可安全删除）。`;
    if (!window.confirm(`确认删除？\n\n${tip}`)) return;
    setBusy(true); setError("");
    try {
      await consumablesApi.deletePurchase(row.id);
      setMessage(`已删除耗材入库单 ${row.number}${hasReceived ? "（库存已自动冲销）" : ""}`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    } finally {
      setBusy(false);
    }
  }

  async function doReopenPurchase(row: ConsumablePurchaseRow) {
    if (busy) return;
    if (!window.confirm(`把已取消的耗材入库单 ${row.number} 恢复为「待收货」？\n\n将恢复 ordered 状态，不影响耗材档案。`)) return;
    setBusy(true); setError("");
    try {
      await consumablesApi.reopenPurchase(row.id);
      setMessage(`已恢复耗材入库单 ${row.number} → 待收货`);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  const activeMaterials = materials.filter((material) => material.status === "active");
  const hasActivePurchase = purchases.some((row) => row.status !== "cancelled");
  const inputCls = "h-8 w-full rounded-md border border-slate-200 bg-white px-2 text-[11px] text-slate-700 outline-none focus:border-indigo-400";

  return (
    <DetailSection
      title="耗材入库单"
      badge="本平台耗材库"
    >
      <p className="mb-3 text-[11px] leading-5 text-slate-400">
        该订单是耗材（包材）采购：作为耗材入库单登记，到货后登记收货，实收数量直接计入本平台耗材库存台账，与正品货品走不同的库存体系。
      </p>
      {activePurchases.length > 0 && (
        <div className="mb-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
            <span className="font-semibold text-slate-700">收货总进度</span>
            <span className="tabular-nums text-slate-500">
              已收 <strong className={progressPct >= 100 ? "text-emerald-600" : "text-slate-700"}>{progress.received.toLocaleString("zh-CN")}</strong>
              {" / "}{progress.qty.toLocaleString("zh-CN")}
              <span className="ml-2 font-semibold text-indigo-600">{progressPct}%</span>
            </span>
          </div>
          <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-slate-200">
            <div
              className={cx("h-full rounded-full transition-all", progressPct >= 100 ? "bg-emerald-500" : "bg-indigo-500")}
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-400">
            <span>{activePurchases.length} 张入库单 · 已收货金额 ¥{progress.receivedAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
            <span>入库单合计 ¥{progress.amount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
        </div>
      )}
      {message && <div className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-[11px] text-emerald-700">{message}</div>}
      {error && <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-[11px] text-red-600">{error}</div>}
      {loading ? (
        <div className="py-4 text-center text-[11px] text-slate-400">正在加载耗材采购单…</div>
      ) : (
        <div className="space-y-3">
          {purchases.map((row) => {
            const status = CONSUMABLE_PO_STATUS[row.status] ?? { label: row.status, cls: "bg-slate-100 text-slate-500" };
            const receiving = receivingId === row.id && (row.status === "ordered" || row.status === "partial");
            return (
              <div key={row.id} className="rounded-lg border border-slate-200 bg-white">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-[11px] font-medium text-indigo-600">{row.number}</span>
                    <span className={cx("rounded px-1.5 py-0.5 text-[10px] font-medium", status.cls)}>{status.label}</span>
                    <span className="text-[10px] text-slate-400">{row.orderedOn} · ¥{Number(row.amount).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {row.status === "cancelled" && (
                      <button disabled={busy} onClick={() => void doReopenPurchase(row)} title="把已取消的耗材入库单恢复为「待收货」" className="rounded-md border border-emerald-200 bg-white px-2 py-1 text-[11px] font-medium text-emerald-600 hover:bg-emerald-50 disabled:opacity-40">
                        ↺ 恢复
                      </button>
                    )}
                    <button disabled={busy} onClick={() => void doDeletePurchase(row)} title="删除这张耗材入库单（已收货的会自动冲销库存流水）" className="rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] font-medium text-red-500 hover:bg-red-50 disabled:opacity-40">
                      🗑 删除
                    </button>
                    {!receiving && (row.status === "ordered" || row.status === "partial") && (
                      <button onClick={() => startReceive(row)} className="rounded-md bg-indigo-50 px-2.5 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100">登记收货</button>
                    )}
                  </div>
                </div>
                <table className="w-full text-left text-[11px]">
                  <thead className="text-slate-400">
                    <tr className="border-b border-slate-100">
                      <th className="px-3 py-1.5 font-medium">耗材</th>
                      <th className="px-2 py-1.5 text-right font-medium">采购量</th>
                      <th className="px-2 py-1.5 text-right font-medium">已收</th>
                      <th className="px-2 py-1.5 text-right font-medium">待收</th>
                      <th className="px-2 py-1.5 text-right font-medium">单价</th>
                      <th className="px-2 py-1.5 text-right font-medium">总金额</th>
                      {receiving && <th className="px-3 py-1.5 text-right font-medium">本次实收</th>}
                      <th className="px-2 py-1.5 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {row.items.map((line) => {
                      const remaining = Number(line.quantity) - Number(line.receivedQty);
                      const lineAmount = Number(line.quantity) * Number(line.unitCost);
                      const isEditing = editingLine?.line.id === line.id;
                      return (
                        <Fragment key={line.id}>
                          <tr>
                            <td className="px-3 py-1.5"><span className="text-slate-700">{line.name}</span> <span className="ml-1 font-mono text-[9px] text-slate-400">{line.code}</span></td>
                            <td className="px-2 py-1.5 text-right tabular-nums">{Number(line.quantity).toLocaleString("zh-CN")} {line.unit}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">{Number(line.receivedQty).toLocaleString("zh-CN")}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums font-medium">{remaining.toLocaleString("zh-CN")}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums text-slate-500">¥{Number(line.unitCost).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}</td>
                            <td className="px-2 py-1.5 text-right tabular-nums font-medium text-slate-700">¥{lineAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                            {receiving && (
                              <td className="px-3 py-1.5 text-right">
                                <input
                                  aria-label={`${line.name} 本次实收`}
                                  type="number"
                                  min="0"
                                  max={remaining}
                                  step="0.0001"
                                  disabled={remaining === 0 || busy}
                                  value={receiveQtys[line.id] ?? ""}
                                  onChange={(event) => setReceiveQtys({ ...receiveQtys, [line.id]: event.target.value })}
                                  placeholder="未到货留空"
                                  className={cx(inputCls, "ml-auto w-24 text-right")}
                                />
                              </td>
                            )}
                            <td className="px-2 py-1.5 text-right">
                                <button
                                  type="button"
                                  disabled={busy}
                                  onClick={() => isEditing ? cancelEditLine() : startEditLine(row.id, line)}
                                  title={isEditing ? "取消编辑" : "编辑这条耗材的采购量 / 单价 / 总金额（已收货的单：数量不能低于已收数）"}
                                  className={cx(
                                    "rounded border px-1.5 py-0.5 text-[10px] font-medium disabled:opacity-40",
                                    isEditing
                                      ? "border-slate-300 bg-white text-slate-500"
                                      : "border-indigo-200 bg-white text-indigo-600 hover:bg-indigo-50",
                                  )}
                                >
                                  {isEditing ? "取消" : "✎"}
                                </button>
                              </td>
                          </tr>
                          {isEditing && (
                            <tr className="bg-amber-50/60">
                              <td colSpan={receiving ? 8 : 7} className="px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-700">
                                  <span className="font-medium text-slate-800">编辑 {line.name}</span>
                                  <span className="font-mono text-[10px] text-slate-400">{line.code}</span>
                                  <span className="ml-2 text-slate-500">采购量</span>
                                  <input
                                    type="number" min="0.0001" step="0.0001"
                                    aria-label={`${line.name} 新采购量`}
                                    value={editQty} onChange={(e) => onEditQtyChange(e.target.value)}
                                    className={cx(inputCls, "w-24 text-right")}
                                    disabled={busy}
                                  />
                                  <span className="text-slate-500">单价</span>
                                  <input
                                    type="number" min="0" step="any"
                                    aria-label={`${line.name} 新单价`}
                                    value={editCost} onChange={(e) => onEditCostChange(e.target.value)}
                                    className={cx(inputCls, "w-24 text-right")}
                                    disabled={busy}
                                  />
                                  <span className="text-slate-500">总金额</span>
                                  <input
                                    type="number" min="0" step="any"
                                    aria-label={`${line.name} 新总金额`}
                                    value={editTotal} onChange={(e) => onEditTotalChange(e.target.value)}
                                    className={cx(inputCls, "w-28 text-right font-medium text-indigo-700")}
                                    disabled={busy}
                                  />
                                  <button type="button" disabled={busy} onClick={() => void saveEditLine(row)} className="rounded-md bg-indigo-600 px-2.5 py-1 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50">保存</button>
                                  <button type="button" disabled={busy} onClick={cancelEditLine} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-50 disabled:opacity-50">取消</button>
                                  <span className="text-[10px] text-amber-700">已收货的单：数量不能低于已收数；单价改动会同步收货成本</span>
                                </div>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                  {(() => {
                    const totalQty = row.items.reduce((s, it) => s + Number(it.quantity), 0);
                    const totalAmount = row.items.reduce((s, it) => s + Number(it.quantity) * Number(it.unitCost), 0);
                    if (totalAmount === 0 && totalQty === 0) return null;
                    return (
                      <tfoot>
                        <tr className="border-t border-slate-200 bg-slate-50/70 text-[11px]">
                          <td className="px-3 py-1.5 font-semibold text-slate-600">合计</td>
                          <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">{totalQty.toLocaleString("zh-CN")}</td>
                          <td colSpan={2} className="px-2 py-1.5"></td>
                          <td className="px-2 py-1.5 text-right text-[10px] text-slate-400">×{row.items.length} 项</td>
                          <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-800">¥{totalAmount.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                          {receiving && <td className="px-2 py-1.5"></td>}
                          <td className="px-2 py-1.5"></td>
                        </tr>
                      </tfoot>
                    );
                  })()}
                </table>
                {receiving && (
                  <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-3 py-2">
                    <label className="text-[10px] text-slate-500">收货日期
                      <input type="date" value={receiveDate} onChange={(event) => setReceiveDate(event.target.value)} className={cx(inputCls, "ml-1 inline-block w-32")} />
                    </label>
                    <button disabled={busy} onClick={() => setReceivingId(null)} className="rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-500">取消</button>
                    <button disabled={busy} onClick={() => void saveReceive(row)} className="rounded-md bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50">{busy ? "入库中…" : "确认收货入库"}</button>
                  </div>
                )}
              </div>
            );
          })}

          {!hasActivePurchase && !creating && (
            <button onClick={() => { setCreating(true); setError(""); setMessage(""); }} disabled={!activeMaterials.length} className="rounded-md bg-indigo-600 px-3 py-1.5 text-[11px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50" title={activeMaterials.length ? "" : "请先在「商品与库存 → 耗材」建立耗材档案"}>
              + 登记耗材入库单
            </button>
          )}

          {creating && (
            <form onSubmit={createPurchase} className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-semibold text-slate-700">新建耗材入库单（关联{CHANNELS[channelOf(order.platform)].label}订单 {order.orderNo}）</span>
                <button type="button" onClick={() => setCreating(false)} className="text-[11px] text-slate-400 hover:text-slate-600">收起</button>
              </div>
              <div className="mt-2 flex items-center gap-2 text-[10px] text-slate-500">
                <label>采购日期 <input type="date" required value={form.orderedOn} onChange={(event) => setForm({ ...form, orderedOn: event.target.value })} className={cx(inputCls, "ml-1 inline-block w-32")} /></label>
              </div>
              <div className="mt-2 space-y-2">
                {form.lines.map((line, index) => (
                  <div key={index} className="grid grid-cols-[minmax(0,1fr)_84px_92px_84px_24px] items-center gap-2">
                    <SearchableSelect
                      ariaLabel={`第${index + 1}行耗材`}
                      placeholder="选择耗材"
                      className="w-full"
                      value={line.consumable_id}
                      onChange={(next) => {
                        const material = activeMaterials.find((row) => row.id === Number(next));
                        const newCost = material?.purchaseUnitCost ?? line.unit_cost;
                        const q = Number(line.quantity || 0);
                        const newTotal = q > 0 && Number(newCost) > 0 ? (q * Number(newCost)).toFixed(4) : line.total_amount;
                        setLineField(index, { consumable_id: next, unit_cost: newCost, total_amount: newTotal });
                      }}
                      options={activeMaterials.map((material) => ({
                        value: String(material.id),
                        label: `${material.code} · ${material.name}（${material.unit}）`,
                        keywords: `${material.code} ${material.name}`,
                      }))}
                    />
                    <input required type="number" min="0.0001" step="0.0001" aria-label={`第${index + 1}行数量`} placeholder="数量" value={line.quantity} onChange={(event) => onCreateQtyChange(index, event.target.value)} className={cx(inputCls, "text-right")} />
                    <input type="number" min="0" step="any" aria-label={`第${index + 1}行单价`} placeholder="单价" value={line.unit_cost} onChange={(event) => onCreateCostChange(index, event.target.value)} className={cx(inputCls, "text-right")} />
                    <input type="number" min="0" step="any" aria-label={`第${index + 1}行总金额`} placeholder="总金额" value={line.total_amount} onChange={(event) => onCreateTotalChange(index, event.target.value)} className={cx(inputCls, "text-right font-medium text-indigo-700")} />
                    <button type="button" disabled={form.lines.length === 1} aria-label={`移除第${index + 1}行`} onClick={() => setForm({ ...form, lines: form.lines.filter((_, i) => i !== index) })} className="text-slate-300 hover:text-slate-500 disabled:opacity-30">×</button>
                  </div>
                ))}
              </div>
              <div className="mt-2 flex items-center justify-between">
                <button type="button" onClick={() => setForm({ ...form, lines: applyAutoUnitCost([...form.lines, { consumable_id: "", quantity: "", unit_cost: "", total_amount: "" }]) })} className="text-[11px] font-medium text-indigo-600">+ 添加耗材</button>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-slate-500">合计 <strong className="text-slate-700">¥{form.lines.reduce((sum, line) => sum + Number(line.quantity || 0) * Number(line.unit_cost || 0), 0).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</strong></span>
                  <button disabled={busy} className="rounded-md bg-indigo-600 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50">{busy ? "保存中…" : "建立采购单"}</button>
                </div>
              </div>
              <label className="mt-2 flex items-center gap-1.5 text-[10px] text-slate-500">
                <input type="checkbox" checked={autoUnitCost} onChange={(event) => { setAutoUnitCost(event.target.checked); if (event.target.checked) setForm({ ...form, lines: applyAutoUnitCost(form.lines) }); }} />
                单价按订单实付自动均摊
                {autoUnitCost && formTotalQty > 0 && orderTotal > 0 && (
                  <span className="text-slate-400">＝ 实付 ¥{orderTotal.toLocaleString("zh-CN", { minimumFractionDigits: 2 })} ÷ 总数量 {formTotalQty.toLocaleString("zh-CN")} = ¥{autoUnit}（手动改单价即转为手动模式）</span>
                )}
              </label>
            </form>
          )}

          {!purchases.length && !creating && !loading && (
            <p className="text-[10px] text-slate-400">还没有关联本订单的耗材入库单。上方按钮建档后，收货即入「商品与库存 → 耗材」的库存台账。</p>
          )}
        </div>
      )}
    </DetailSection>
  );
}

function InboundDocCard({ doc, allocations, closed, editable, usageMaterials, usageMappings, busy,
  onChanged, onRequestReplace, onRemove, onCorrectAmount, onSaveAmount,
  onEditAllocation, onRemoveAllocation, onAddSku, onNotify, pendingConsumableUpdate,
  onPendingConsumableUpdateApplied, editor }: {
  doc: InboundRow;
  allocations: AllocationRow[];
  closed: boolean;
  editable: boolean;
  usageMaterials: ConsumableRow[];
  usageMappings: ConsumableMappingRow[];
  busy: boolean;
  onChanged: () => Promise<void>;
  onRequestReplace: (linkId: number) => void;
  onRemove: (linkId: number) => void;
  onCorrectAmount: (documentId: number) => void;
  onSaveAmount: (documentId: number, value: string) => void;
  onEditAllocation: (row: AllocationRow, consumable: EditorConsumableDraft | null) => void;
  onRemoveAllocation: (id: number) => void;
  onAddSku: (documentId: number) => void;
  onNotify: (message: string) => void;
  pendingConsumableUpdate?: PendingConsumableUpdate | null;
  onPendingConsumableUpdateApplied?: () => void;
  editor?: ReactNode;
}) {
  const [amountEditing, setAmountEditing] = useState(false);
  const [amountValue, setAmountValue] = useState("");
  const [usageEnabled, setUsageEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  // 每行耗材的本地草稿：key -> { consumableId, quantity } | null（null = 该行无默认绑定，需手工选）
  const [rowCons, setRowCons] = useState<Record<string, { consumableId: number; quantity: string } | null>>({});

  const linkId = doc.linkId ?? null;
  const decided = Boolean(doc.consumableUsageDecided);
  const items = doc.consumableUsageItems ?? [];
  const mismatch = doc.amount != null && doc.itemAmount != null && Math.abs(doc.amount - doc.itemAmount) > 0.01;
  const skuTotal = allocations.reduce((sum, row) => sum + (row.amount ?? 0), 0);

  const rowKey = (row: AllocationRow, i: number) => (row.id != null ? `a${row.id}` : `c${(row.skuCode ?? "")}-${i}`);
  const round3 = (n: number) => String(Number(n.toFixed(3)));
  const defaultFor = (row: AllocationRow, mapping?: ConsumableMappingRow) =>
    mapping ? { consumableId: mapping.consumableId, quantity: round3(Number(row.quantity ?? 0) * Number(mapping.usagePerUnit || 0)) } : null;
  // 选了某个耗材时，按“该货品→该耗材”的用量比例给出默认数量；无映射则默认 1
  const defaultQtyFor = (row: AllocationRow, consumableId: number) => {
    const m = usageMappings.find((row2) => row2.consumableId === consumableId);
    return round3(m ? Number(row.quantity ?? 0) * Number(m.usagePerUnit || 0) : 1);
  };

  // 卡片挂载（或换单据）时预填每行耗材草稿：
  // 1) 已登记过（decided && enabled）：该耗材仅被一行映射命中时，直接回显实际登记数量；
  // 2) 未登记：按 SKU→耗材映射预填默认（数量 = usagePerUnit × 数量）；无映射留给手工选。
  // 用户编辑会写入 rowCons 不被覆盖。
  useEffect(() => {
    const seeded: Record<string, { consumableId: number; quantity: string } | null> = {};
    const registered = doc.consumableUsageDecided && doc.consumableUsageEnabled ? (doc.consumableUsageItems ?? []) : [];
    const hitCount = new Map<number, number>();
    allocations.forEach((row) => {
      const m = usageMappings.find((mm) => mm.skuId === row.skuId);
      if (m) hitCount.set(m.consumableId, (hitCount.get(m.consumableId) ?? 0) + 1);
    });
    allocations.forEach((row, i) => {
      const k = rowKey(row, i);
      const m = usageMappings.find((mm) => mm.skuId === row.skuId);
      let draft = defaultFor(row, m);
      if (m && hitCount.get(m.consumableId) === 1) {
        const item = registered.find((it) => it.consumableId === m.consumableId);
        if (item) draft = { consumableId: m.consumableId, quantity: round3(Number(item.quantity)) };
      }
      seeded[k] = draft;
    });
    setRowCons(seeded);
    setUsageEnabled(decided ? Boolean(doc.consumableUsageEnabled) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc.documentId]);

  useEffect(() => {
    if (!pendingConsumableUpdate) return;
    const index = allocations.findIndex((row) => row.id === pendingConsumableUpdate.allocationId);
    if (index < 0) return;
    const key = rowKey(allocations[index], index);
    setRowCons((previous) => ({ ...previous, [key]: pendingConsumableUpdate.draft }));
    onPendingConsumableUpdateApplied?.();
    // The parent clears this one-shot update after the allocation is present in the refreshed detail.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allocations, pendingConsumableUpdate]);

  async function saveConsumables() {
    if (!linkId) return;
    if (usageEnabled === null) { onNotify("请先选择「有耗材使用」或「本次不使用耗材」"); return; }
    setSaving(true);
    try {
      if (usageEnabled) {
        // 把每行耗材按 consumableId 聚合（一个货品对应一个耗材，多行可合并）
        const agg = new Map<number, number>();
        allocations.forEach((row, i) => {
          const k = rowKey(row, i);
          const c = rowCons[k] ?? defaultFor(row, usageMappings.find((m) => m.skuId === row.skuId));
          if (c && c.consumableId) {
            const q = Number(c.quantity);
            if (Number.isFinite(q) && q > 0) agg.set(c.consumableId, (agg.get(c.consumableId) ?? 0) + q);
          }
        });
        const payload = Array.from(agg, ([consumable_id, quantity]) => ({ consumable_id, quantity: round3(quantity) }));
        if (payload.length === 0) { onNotify("请为至少一行选择耗材并填写数量"); setSaving(false); return; }
        await procurementChainApi.setInboundConsumableUsage(linkId, true, payload, "采购工作台登记耗材使用");
      } else {
        await procurementChainApi.setInboundConsumableUsage(linkId, false, [], "采购工作台登记耗材使用");
      }
      await onChanged();
      onNotify(usageEnabled ? "耗材使用已登记" : "已标记本次入库不使用耗材");
    } catch (caught) {
      onNotify(caught instanceof Error ? caught.message : "保存耗材失败");
    } finally {
      setSaving(false);
    }
  }

  const usageTag = !decided
    ? { text: "耗材：待登记", className: "font-medium text-amber-600" }
    : items.length > 0
      ? { text: "耗材：有", className: "text-emerald-600" }
      : { text: "耗材：无", className: "text-slate-400" };

  return (
    <div className={cx("rounded-lg border bg-white px-2.5 py-2", decided ? "border-slate-200" : "border-amber-300")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[12px] font-semibold text-slate-800">{doc.goodsdocNo || "已关联入库单"}</div>
          <div className="mt-0.5 truncate text-[11px] text-slate-400">{fmtDateTime(doc.date)} · {doc.warehouseName || "未注仓库"} · <span className={usageTag.className} title="由明细中登记的耗材使用自动标识">{usageTag.text}</span></div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {amountEditing ? (
              <>
                <input autoFocus onFocus={(e) => e.target.select()} value={amountValue}
                  onChange={(e) => setAmountValue(e.target.value)} placeholder={String(doc.amount ?? "")}
                  className="h-7 w-24 rounded border border-indigo-300 px-1.5 text-right text-[12px] tabular-nums outline-none" />
                <button disabled={busy || saving || !amountValue}
                  onClick={() => { onSaveAmount(doc.documentId!, amountValue); setAmountEditing(false); setAmountValue(""); }}
                  className="rounded bg-indigo-600 px-2 py-0.5 text-[11px] font-medium text-white disabled:opacity-40">保存</button>
                <button onClick={() => { setAmountEditing(false); setAmountValue(""); }}
                  className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:text-slate-600">取消</button>
              </>
            ) : (
              <>
                <span className="text-[12px] font-semibold tabular-nums text-slate-700">{fmtMoney(doc.amount)}</span>
                {doc.documentId ? (
                  <button disabled={busy || closed}
                    onClick={() => { setAmountEditing(true); setAmountValue(String(doc.amount ?? "")); }}
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-500 hover:bg-indigo-50 hover:text-indigo-600 disabled:opacity-40"
                    title="更正录错的入库单金额">✎ 改金额</button>
                ) : null}
                {mismatch && (
                  <>
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[11px] text-amber-700 ring-1 ring-amber-200">
                      单据头 {fmtMoney(doc.amount)} ≠ 明细合计 {fmtMoney(doc.itemAmount)}（{doc.itemCount} 行）
                    </span>
                    <button disabled={busy || closed} onClick={() => doc.documentId && onCorrectAmount(doc.documentId)}
                      className="rounded bg-amber-500 px-1.5 py-0.5 text-[11px] font-medium text-white hover:bg-amber-600 disabled:opacity-40"
                      title="以明细金额合计重算单据金额">按明细更正</button>
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          {linkId ? (
            <div className="flex items-center gap-1">
              <button disabled={busy || closed} onClick={() => onRequestReplace(linkId)}
                className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-slate-50 hover:text-indigo-600 disabled:opacity-40">更换</button>
              <button disabled={busy || closed} onClick={() => onRemove(linkId)}
                className="rounded px-1.5 py-0.5 text-[11px] text-slate-400 hover:bg-slate-50 hover:text-red-500 disabled:opacity-40">解除</button>
            </div>
          ) : <span className="text-[11px] text-slate-400">历史关联</span>}
        </div>
      </div>

      <div className="mt-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-medium text-slate-500">
            SKU 明细 {allocations.length > 0 ? `(${allocations.length} 行 · ${fmtMoney(skuTotal)})` : ""}
          </span>
          {editable && <button disabled={busy || closed} onClick={() => onAddSku(doc.documentId!)}
            className="rounded bg-indigo-50 px-2 py-0.5 text-[11px] font-medium text-indigo-600 hover:bg-indigo-100 disabled:opacity-40">＋ 新建</button>}
        </div>
        <div className="mt-1 overflow-x-auto">
        <table className="w-full table-fixed border-collapse text-[12px]">
          <colgroup>
            <col style={{ width: "38%" }} />
            <col style={{ width: "9%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "12%" }} />
            <col style={{ width: "20%" }} />
            <col style={{ width: "10%" }} />
          </colgroup>
          <thead>
            <tr className="text-[11px] text-slate-400">
              <th className="border-b border-slate-100 py-1 pr-2 text-left font-medium">SKU 明细</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">数量</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">单价</th>
              <th className="border-b border-slate-100 py-1 px-1 text-right font-medium">总价</th>
              <th className="border-b border-slate-100 py-1 px-1 text-left font-medium">耗材</th>
              <th className="border-b border-slate-100 py-1 pl-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {allocations.length > 0 ? allocations.map((row, i) => {
              const k = rowKey(row, i);
              const mapping = usageMappings.find((m) => m.skuId === row.skuId);
              const current = rowCons[k] ?? defaultFor(row, mapping);
              const material = current ? usageMaterials.find((m) => m.id === current.consumableId) : null;
              const editableCell = editable && !closed && usageEnabled !== false;
              return (
                <tr key={k} className="whitespace-nowrap border-b border-slate-50 last:border-0">
                  <td className="py-1.5 pr-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span className="shrink-0 font-mono text-[11px] text-indigo-500">{row.skuCode || "未关联SKU"}</span>
                      <span className="min-w-0 truncate text-[12px] font-medium text-slate-700" title={`${row.skuCode || ""} ${row.goodsName || "未命名商品"}`}>{row.goodsName || "未命名商品"}</span>
                    </div>
                  </td>
                  <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{row.quantity ?? "—"}</td>
                  <td className="py-1.5 px-1 text-right tabular-nums text-slate-500">{fmtMoney(row.unitPrice)}</td>
                  <td className="py-1.5 px-1 text-right tabular-nums font-medium text-slate-700">{fmtMoney(row.amount)}</td>
                  <td className="py-1.5 px-1">
                    {usageEnabled === false ? (
                      <span className="text-[11px] text-slate-400">本单不使用</span>
                    ) : (
                      <div className="flex items-center gap-1">
                        <SearchableSelect
                          compact
                          className="flex-1"
                          disabled={!editableCell}
                          ariaLabel="选择耗材"
                          placeholder={`＋ 选择耗材${mapping ? "" : "（手工）"}`}
                          value={current?.consumableId ? String(current.consumableId) : ""}
                          onChange={(next) => {
                            const id = Number(next);
                            if (!id) { setRowCons((p) => ({ ...p, [k]: null })); return; }
                            setRowCons((p) => ({ ...p, [k]: { consumableId: id, quantity: defaultQtyFor(row, id) } }));
                          }}
                          options={usageMaterials.map((m) => ({
                            value: String(m.id), label: `${m.code} · ${m.name}`, keywords: `${m.code} ${m.name}`,
                          }))}
                        />
                        <input value={current?.quantity ?? ""} disabled={!editableCell || !current?.consumableId}
                          onChange={(e) => setRowCons((p) => ({ ...p, [k]: { consumableId: current?.consumableId ?? mapping?.consumableId ?? 0, quantity: e.target.value } }))}
                          placeholder="数量" className="h-6 w-11 shrink-0 rounded border border-slate-200 px-1 text-center text-[11px] tabular-nums outline-none focus:border-amber-400 disabled:bg-slate-50" />
                        <span className="w-6 shrink-0 text-[11px] text-slate-400">{material?.unit}</span>
                      </div>
                    )}
                  </td>
                  <td className="py-1.5 pl-2 text-right">
                    {editable && row.id ? (
                      <div className="flex justify-end gap-1.5">
                        <button disabled={busy} onClick={() => onEditAllocation(row, current)} className="text-[11px] text-indigo-600 disabled:opacity-40">修改</button>
                        <button disabled={busy} onClick={() => onRemoveAllocation(row.id as number)} className="text-[11px] text-red-400 hover:text-red-600 disabled:opacity-40">删除</button>
                      </div>
                    ) : null}
                  </td>
                </tr>
              );
            }            ) : (
              <tr><td colSpan={6} className="py-2 text-center text-[11px] text-slate-400">本张单据没有反填 SKU 明细</td></tr>
            )}
            {editor}
          </tbody>
        </table>
        </div>
      </div>

      {/* 未登记时才显示登记表单；已登记的状态见右上角徽标 */}
      {!decided && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded-md bg-amber-50/70 px-2 py-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-slate-600">耗材登记</span>
            <div className="flex gap-1">
              <button type="button" disabled={busy || saving} onClick={() => setUsageEnabled(true)}
                className={cx("rounded border px-1.5 py-0.5 text-[11px]",
                  usageEnabled === true ? "border-amber-400 bg-amber-100 font-semibold text-amber-800" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>有耗材</button>
              <button type="button" disabled={busy || saving} onClick={() => setUsageEnabled(false)}
                className={cx("rounded border px-1.5 py-0.5 text-[11px]",
                  usageEnabled === false ? "border-slate-500 bg-slate-200 font-semibold text-slate-700" : "border-slate-200 bg-white text-slate-500 hover:border-slate-300")}>不使用</button>
            </div>
          </div>
          {linkId && !closed ? (
            <button disabled={saving || busy || usageEnabled === null} onClick={() => void saveConsumables()}
              className="rounded bg-amber-500 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-amber-600 disabled:opacity-40">
              {saving ? "保存中…" : "保存耗材"}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function DetailSection({ title, badge, children }: { title: string; badge?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200/80 bg-white">
      <div className="flex items-center justify-between px-3 py-2.5">
        <span className="text-[12px] font-semibold text-slate-700">{title}</span>
        <span className="text-[11px] text-slate-400">{badge}</span>
      </div>
      <div className="border-t border-slate-100 px-3 py-2.5">{children}</div>
    </section>
  );
}

function DetailField({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return <div className="min-w-0"><div className="text-[8px] text-slate-400">{label}</div><div className={cx("mt-0.5 truncate text-[12px] text-slate-600", strong && "font-semibold tabular-nums text-slate-800")} title={value}>{value}</div></div>;
}

function StatusSection({ title, status, records, href }: { title: string; status: string; records: string[]; href: string }) {
  return (
    <div className="rounded-lg border border-slate-200/80 bg-white px-3 py-2.5">
      <div className="flex items-center justify-between"><span className="text-[12px] font-semibold text-slate-700">{title}</span><Link href={href} className={cx("text-[9.5px]", records.length ? "text-emerald-600" : "text-slate-400")}>{status}</Link></div>
      {records.length > 0 && <div className="mt-1.5 truncate font-mono text-[11px] text-slate-400">{records.slice(0, 2).join("　")}</div>}
    </div>
  );
}

function SupplierList({ suppliers, loading, query, selectedName, onQueryChange, onSelect }: {
  suppliers: WorkbenchSupplierSummary[]; loading: boolean; query: string; selectedName: string | null;
  onQueryChange: (value: string) => void; onSelect: (name: string) => void;
}) {
  return (
    <section className="min-w-0 overflow-x-auto rounded-xl border border-slate-200/90 bg-white shadow-[0_3px_14px_rgba(40,53,85,0.035)]">
      <div className="flex min-w-[700px] items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <div><h2 className="text-[13px] font-semibold text-slate-800">供应商管理</h2><p className="mt-0.5 text-[12px] text-slate-400">采购频次、金额、入库与发票状态汇总</p></div>
        <div className="flex w-56 items-center gap-2 rounded-lg bg-slate-50 px-3"><Icon name="search" size={14} /><input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="搜索供应商" className="h-8 min-w-0 flex-1 bg-transparent text-[11px] outline-none placeholder:text-slate-400" /></div>
      </div>
      <div className="grid min-w-[700px] grid-cols-[minmax(220px,1fr)_90px_130px_110px_110px] gap-3 border-b border-slate-100 bg-slate-50/70 px-4 py-2 text-[12px] font-medium text-slate-400">
        <span>供应商</span><span className="text-right">采购次数</span><span className="text-right">累计采购</span><span className="text-right">未入库</span><span className="text-right">最近采购</span>
      </div>
      {loading ? <Loading text="正在加载供应商…" /> : suppliers.length === 0 ? <Empty text="暂无供应商数据" /> : <div className="min-w-[700px] divide-y divide-slate-100">{suppliers.map((supplier) => (
        <button key={supplier.supplierName} onClick={() => onSelect(supplier.supplierName)} className={cx(
          "grid w-full grid-cols-[minmax(220px,1fr)_90px_130px_110px_110px] items-center gap-3 px-4 py-3 text-left transition-colors",
          selectedName === supplier.supplierName ? "bg-indigo-50/60 ring-1 ring-inset ring-indigo-400" : "hover:bg-slate-50"
        )}>
          <span className="truncate text-[12px] font-medium text-slate-700">{supplier.supplierName}</span>
          <span className="text-right text-[11px] tabular-nums text-slate-500">{supplier.orderCount}次</span>
          <span className="text-right text-[11px] font-semibold tabular-nums text-slate-700">{fmtMoney(supplier.totalPurchase)}</span>
          <span className="text-right text-[11px] tabular-nums text-orange-500">{fmtMoney(supplier.uninbound)}</span>
          <span className="text-right text-[12px] text-slate-400">{fmtDate(supplier.lastOrderDate)}</span>
        </button>
      ))}</div>}
    </section>
  );
}

/** 发票状态标签：与发票对账视图（view=tax）口径一致 */
const INVOICE_MATCH_BADGE: Record<string, { text: string; cls: string }> = {
  matched: { text: "已配平", cls: "bg-emerald-50 text-emerald-600" },
  short: { text: "订单不足", cls: "bg-amber-50 text-amber-600" },
};

/** 订单匹配状态：remaining = 该单未被发票覆盖的余量（后端 FIFO 配平结果） */
function orderMatchStatus(order: { orderAmount: number; remaining: number }): { text: string; cls: string; sub: string } {
  if (order.orderAmount <= 0.005) return { text: "金额未同步", cls: "bg-slate-100 text-slate-400", sub: "金额为 0 未参与配平" };
  if (order.remaining <= 0.05) return { text: "已配平", cls: "bg-emerald-50 text-emerald-600", sub: "发票已足额覆盖" };
  const matched = order.orderAmount - order.remaining;
  if (matched > 0.05) return { text: "部分匹配", cls: "bg-sky-50 text-sky-600", sub: "已配 " + fmtMoney(matched) + " · 余 " + fmtMoney(order.remaining) };
  return { text: "待开票", cls: "bg-amber-50 text-amber-600", sub: "无发票覆盖" };
}

function SupplierDetailPanel({ detail, loading, onRenamed }: {
  detail: WorkbenchSupplierDetail | null;
  loading: boolean;
  onRenamed?: (newName: string) => void | Promise<void>;
}) {
  const supplierName = detail?.supplierName ?? null;
  const [invoiceData, setInvoiceData] = useState<InvoiceReconciliation | null>(null);
  const [invoiceLoading, setInvoiceLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");

  // 供应商改名/归一：统一该供应商全部订单的写法，同名供应商自动合并
  async function saveRename() {
    if (!detail) return;
    const newName = nameDraft.trim();
    if (!newName || newName === detail.supplierName) {
      setEditingName(false);
      setRenameError("");
      return;
    }
    setRenaming(true);
    setRenameError("");
    try {
      await procurementWorkbenchApi.renameSupplier(detail.supplierName, newName);
      setEditingName(false);
      await onRenamed?.(newName);
    } catch (caught) {
      setRenameError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setRenaming(false);
    }
  }

  // 发票匹配清单：按当前供应商拉取配平结果（手工关联优先 + FIFO 自动）；手工微调后 onReload 刷新
  const loadInvoiceMatch = useCallback(() => {
    if (!supplierName) {
      setInvoiceData(null);
      return;
    }
    let cancelled = false;
    setInvoiceLoading(true);
    procurementWorkbenchApi.invoiceReconciliation(supplierName)
      .then((data) => { if (!cancelled) setInvoiceData(data); })
      .catch(() => { if (!cancelled) setInvoiceData(null); })
      .finally(() => { if (!cancelled) setInvoiceLoading(false); });
    return () => { cancelled = true; };
  }, [supplierName]);

  useEffect(() => loadInvoiceMatch(), [loadInvoiceMatch]);

  const invoiceEntry = useMemo(() => {
    const list = invoiceData?.suppliers ?? [];
    if (list.length === 0) return null;
    return list.find((s) => s.supplier === supplierName) ?? list[0];
  }, [invoiceData, supplierName]);

  if (loading && !detail) return <aside className="rounded-xl border border-slate-200 bg-white"><Loading text="正在加载供应商画像…" /></aside>;
  if (!detail) return <aside className="flex min-h-[480px] items-center justify-center rounded-xl border border-slate-200 bg-white text-[12px] text-slate-400">选择供应商查看详情</aside>;
  return (
    <aside className="xl:sticky xl:top-[calc(var(--wb-header-h,162px)+8px)] xl:self-start">
      <div className="rounded-xl border border-slate-200/90 bg-white p-4 shadow-[0_4px_18px_rgba(40,53,85,0.04)]">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] text-slate-400">供应商画像</div>
            {editingName ? (
              <input
                autoFocus
                value={nameDraft}
                onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void saveRename();
                  if (e.key === "Escape") { setEditingName(false); setRenameError(""); }
                }}
                className="mt-1 w-full rounded-md border border-indigo-200 px-2 py-1 text-[14px] font-semibold text-slate-800 outline-none focus:border-indigo-400"
              />
            ) : (
              <h2 className="mt-1 text-[15px] font-semibold text-slate-800">{detail.supplierName}</h2>
            )}
          </div>
          {editingName ? (
            <span className="flex shrink-0 gap-1.5 pt-4">
              <button disabled={renaming} onClick={() => void saveRename()} className="rounded-md bg-indigo-500 px-2.5 py-1 text-[11px] text-white hover:bg-indigo-600 disabled:opacity-50">{renaming ? "保存中…" : "保存"}</button>
              <button onClick={() => { setEditingName(false); setRenameError(""); }} className="rounded-md border border-slate-200 px-2.5 py-1 text-[11px] text-slate-500 hover:bg-slate-50">取消</button>
            </span>
          ) : (
            <button onClick={() => { setNameDraft(detail.supplierName); setEditingName(true); }} className="mt-3 shrink-0 text-[11px] text-indigo-500 hover:underline">改名</button>
          )}
        </div>
        {editingName && (
          <p className="mt-1 text-[10px] leading-4 text-slate-400">
            该供应商全部订单将统一改为新名称；若与现有供应商同名会自动合并（发票卖方名是税务事实不受影响）。回车保存，Esc 取消。
          </p>
        )}
        {renameError && <p className="mt-1 text-[11px] text-red-500">{renameError}</p>}
        <div className="mt-4 grid grid-cols-2 gap-2">
          <SupplierMetric label="采购次数" value={detail.orderCount + "次"} tone="indigo" />
          <SupplierMetric label="累计采购" value={fmtMoney(detail.totalPurchase)} tone="indigo" />
          <SupplierMetric label="未开票金额" value={fmtMoney(detail.uninvoiced)} tone="amber" />
          <SupplierMetric label="未入库金额" value={fmtMoney(detail.uninbound)} tone="amber" />
        </div>
        <div className="mt-3 rounded-lg border border-slate-100 p-3">
          <div className="text-[12px] font-semibold text-slate-700">常购SKU</div>
          <div className="mt-2 space-y-1.5">{detail.oftenSkus.length > 0 ? detail.oftenSkus.slice(0, 6).map((sku) => (
        <div key={sku.skuCode} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-2 text-[12px]"><span className="truncate text-slate-600">{sku.goodsName || "未命名商品"}</span><span className="shrink-0 font-mono text-indigo-500">{sku.skuCode} · {sku.count}次</span></div>
          )) : <div className="py-4 text-center text-[12px] text-slate-400">暂无常购SKU</div>}</div>
        </div>
        <div className="mt-3 rounded-lg border border-slate-100 p-3">
          <div className="text-[12px] font-semibold text-slate-700">最近采购订单</div>
          <div className="mt-2 space-y-1.5">{detail.recentOrders.length > 0 ? detail.recentOrders.slice(0, 6).map((order) => (
        <div key={order.orderId} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-2 text-[12px]"><span className="truncate font-mono text-indigo-500">{order.orderNo}</span><span className="shrink-0 text-slate-400">{fmtDate(order.orderDate)}　{fmtMoney(order.amount)}</span></div>
          )) : <div className="py-4 text-center text-[12px] text-slate-400">暂无历史订单</div>}</div>
        </div>
        <SupplierInvoiceMatchSection
          entry={invoiceEntry}
          loading={invoiceLoading && !invoiceEntry}
          hasData={invoiceData != null}
          onReload={loadInvoiceMatch}
        />
      </div>
    </aside>
  );
}

function SupplierInvoiceMatchSection({ entry, loading, hasData, onReload }: {
  entry: InvoiceReconciliation["suppliers"][number] | null;
  loading: boolean;
  hasData: boolean;
  onReload: () => void;
}) {
  const [adjustingId, setAdjustingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  // 手工微调：把订单挂到发票（manual 关联落库）后整票以手工为准，不再自动 FIFO
  async function addMatch(invoiceId: number, poId: number) {
    setBusy(true);
    try {
      await procurementWorkbenchApi.createInvoiceMatch(invoiceId, poId);
      setAdjustingId(null);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  async function removeLink(linkId: number) {
    setBusy(true);
    try {
      await procurementWorkbenchApi.deleteInvoiceMatch(linkId);
      onReload();
    } finally {
      setBusy(false);
    }
  }

  const matchCandidates = (entry?.orders ?? []).filter((o) => o.remaining > 0.05);

  return (
    <div className="mt-3 rounded-lg border border-slate-100 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[12px] font-semibold text-slate-700">发票匹配清单</div>
        {entry && (
          <NextLink href={`/purchase/workbench?view=tax`} className="text-[11px] text-indigo-500 hover:underline">全量对账 →</NextLink>
        )}
      </div>
      <p className="mt-1 text-[10px] leading-4 text-slate-400">进项发票与该供应商的采购订单自动配平（按下单时间先进先出）；看到不对的点「调整匹配」手动指定，手工关联优先并落库。</p>
      {loading ? (
        <div className="flex items-center gap-2 py-4 text-[12px] text-slate-400"><span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" />正在匹配发票…</div>
      ) : !entry ? (
        <div className="py-4 text-center text-[12px] text-slate-400">
          {hasData ? "该供应商暂无已开票的进项发票" : "暂无发票数据"}
          <div className="mt-1 text-[11px] text-slate-300">可到「税务发票」页导入发票清单</div>
        </div>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-3 gap-1.5 text-center">
            <div className="rounded-md bg-slate-50 px-1 py-1.5"><div className="text-[10px] text-slate-400">发票合计</div><div className="text-[12px] font-semibold tabular-nums text-slate-700">{entry.invoiceCount}张 · {fmtMoney(entry.invoiceTotal)}</div></div>
            <div className="rounded-md bg-emerald-50/70 px-1 py-1.5"><div className="text-[10px] text-slate-400">已配平</div><div className="text-[12px] font-semibold tabular-nums text-emerald-600">{fmtMoney(entry.matchedTotal)}</div></div>
            <div className="rounded-md bg-amber-50/70 px-1 py-1.5"><div className="text-[10px] text-slate-400">待开票订单</div><div className="text-[12px] font-semibold tabular-nums text-amber-600">{entry.remainingOrders}单 · {fmtMoney(entry.remainingOrderTotal)}</div></div>
          </div>
          {entry.months.length === 0 ? (
            <div className="py-3 text-center text-[12px] text-slate-400">该供应商暂无进项发票</div>
          ) : entry.months.map((month) => (
            <div key={month.month} className="mt-2">
              <div className="text-[11px] font-medium text-slate-500">{month.month === "未知月份" ? month.month : month.month + " 开票"}</div>
              <div className="mt-1 space-y-1.5">
                {month.invoices.map((inv) => {
                  const badge = INVOICE_MATCH_BADGE[inv.status] ?? { text: inv.status, cls: "bg-slate-100 text-slate-500" };
                  return (
                    <div key={inv.invoiceId} className="rounded-md bg-slate-50 px-2.5 py-2 text-[12px]">
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate font-mono text-indigo-600">{inv.invoiceNo || "无发票号"}</span>
                        <span className="flex shrink-0 items-center gap-1">
                          {inv.manualLinked && <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] text-indigo-500">手动</span>}
                          <span className={"rounded px-1.5 py-0.5 text-[10px] " + badge.cls}>{badge.text}</span>
                        </span>
                      </div>
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        开票 {fmtDate(inv.issueDate)} · 票面 {fmtMoney(inv.amount)}
                        {inv.coveredTotal > 0 && <> · 已配订单 {fmtMoney(inv.coveredTotal)}</>}
                        {inv.status === "short" && <> · 差额 {fmtMoney(inv.diff)}</>}
                      </div>
                      {inv.covered.length === 0 ? (
                        <div className="mt-1 text-[11px] text-amber-600">未匹配到采购订单（订单池为空或金额已耗尽）</div>
                      ) : inv.covered.map((order) => (
                        <div key={(order.linkId ?? "a") + "-" + order.orderId} className="mt-1 flex items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px]">
                          <span className="flex min-w-0 items-center gap-1">
                            <span className={"shrink-0 rounded px-1 py-px text-[9px] " + (order.source === "manual" ? "bg-indigo-50 text-indigo-500" : "bg-slate-100 text-slate-400")}>{order.source === "manual" ? "手动" : "自动"}</span>
                            <span className="truncate font-mono text-slate-500">{order.orderNo || "无单号"}</span>
                          </span>
                          <span className="flex shrink-0 items-center gap-1.5 text-slate-400">
                            {fmtDate(order.date)} · 消耗 {fmtMoney(order.consumed)}{order.partial && <span className="text-amber-500">（部分）</span>}
                            {order.source === "manual" && order.linkId != null && (
                              <button disabled={busy} onClick={() => removeLink(order.linkId!)} className="text-red-400 hover:text-red-500">移除</button>
                            )}
                          </span>
                        </div>
                      ))}
                      <div className="mt-1 flex items-center justify-between gap-2">
                        <span className="text-[10px] text-slate-300">{inv.manualLinked ? "已按手工关联配平；全部移除后恢复自动匹配" : "自动匹配结果不对？可手动指定订单"}</span>
                        <button disabled={busy} onClick={() => setAdjustingId(adjustingId === inv.invoiceId ? null : inv.invoiceId)} className="shrink-0 text-[11px] text-indigo-500 hover:underline">
                          {adjustingId === inv.invoiceId ? "收起" : "调整匹配"}
                        </button>
                      </div>
                      {adjustingId === inv.invoiceId && (
                        <div className="mt-1 rounded-md border border-indigo-100 bg-indigo-50/40 p-2">
                          <div className="text-[10px] text-slate-500">点选要关联到本票的订单（只列有余量的；可多次添加，挂满票面金额即配平）</div>
                          <div className="mt-1 max-h-40 space-y-1 overflow-y-auto">
                            {matchCandidates.length === 0 ? (
                              <div className="py-2 text-center text-[11px] text-slate-400">该供应商没有可关联的订单余量</div>
                            ) : matchCandidates.map((o) => (
                              <button key={o.orderId} disabled={busy} onClick={() => addMatch(inv.invoiceId, o.orderId)} className="flex w-full items-center justify-between gap-2 rounded bg-white px-2 py-1 text-[11px] hover:bg-indigo-50 disabled:opacity-50">
                                <span className="truncate font-mono text-indigo-600">{o.orderNo || "无单号"}</span>
                                <span className="shrink-0 text-slate-400">{fmtDate(o.date)} · 余 {fmtMoney(o.remaining)}</span>
                              </button>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          {entry.orders.length > 0 && (
            <div className="mt-2">
              <div className="flex items-center justify-between gap-2">
                <div className="text-[11px] font-medium text-slate-500">全部采购订单匹配状态</div>
                <div className="text-[10px] text-slate-400">共 {entry.orders.length} 单 · 按下单时间排序</div>
              </div>
              <div className="mt-1 max-h-[280px] space-y-1 overflow-y-auto pr-0.5">
                {entry.orders.map((order) => {
                  const st = orderMatchStatus(order);
                  return (
                    <div key={order.orderId} className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-[12px]">
                      <div className="min-w-0">
                        <div className="truncate font-mono text-indigo-500">{order.orderNo || "无单号"}</div>
                        <div className="text-[10px] text-slate-400">{fmtDate(order.date)}{order.platform ? " · " + order.platform : ""}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <div className="text-right">
                          <div className="text-[12px] font-semibold tabular-nums text-slate-700">{fmtMoney(order.orderAmount)}</div>
                          <div className="text-[10px] text-slate-400">{st.sub}</div>
                        </div>
                        <span className={"shrink-0 rounded px-1.5 py-0.5 text-[10px] " + st.cls}>{st.text}</span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SupplierMetric({ label, value, tone }: { label: string; value: string; tone: "indigo" | "amber" }) {
  return <div className={cx("rounded-lg px-3 py-2.5", tone === "indigo" ? "bg-indigo-50/60" : "bg-amber-50/60")}><div className="text-[11px] text-slate-400">{label}</div><div className={cx("mt-1 text-[12px] font-semibold tabular-nums", tone === "indigo" ? "text-indigo-700" : "text-amber-700")}>{value}</div></div>;
}

function Loading({ text }: { text: string }) {
  return <div className="flex min-h-[160px] items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-[11px] text-slate-400"><span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-indigo-500" />{text}</div>;
}

function Empty({ text }: { text: string }) {
  return <div className="flex min-h-[160px] items-center justify-center rounded-xl border border-slate-200 bg-white text-[11px] text-slate-400">{text}</div>;
}

function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  const paths: Record<IconName, string> = {
    dashboard: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    sales: "M4 19V9m5 10V5m6 14v-7m5 7V3",
    box: "M4 7l8-4 8 4-8 4-8-4zm0 0v10l8 4 8-4V7M12 11v10",
    purchase: "M3 4h2l2 12h11l2-8H7m2 12h.01M18 20h.01",
    orders: "M6 3h12v18H6zM9 8h6M9 12h6M9 16h4",
    calendar: "M5 4h14a2 2 0 0 1 2 2v14H3V6a2 2 0 0 1 2-2zm2-2v4m10-4v4M3 9h18",
    users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m7-10a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm9 4a4 4 0 0 1 4 4v2m-6-10a4 4 0 0 0 0-8",
    wallet: "M3 6h16a2 2 0 0 1 2 2v11H3zM3 6V4h14v2m0 7h4",
    reconcile: "M5 7h14M5 12h9M5 17h6m6-2 2 2 3-4",
    receipt: "M6 3h12v18l-3-2-3 2-3-2-3 2V3zm3 5h6m-6 4h6m-6 4h4",
    cloud: "M7 18h11a4 4 0 0 0 .5-8A6 6 0 0 0 7.2 8.2 5 5 0 0 0 7 18zm5-8v7m-3-3 3 3 3-3",
    chart: "M4 20V10m5 10V4m6 16v-7m5 7H2",
    settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zm0-13v2m0 15v2m9.5-9.5h-2m-15 0h-2m16.2-6.2-1.4 1.4M6.7 17.3l-1.4 1.4m13.4 0-1.4-1.4M6.7 6.7 5.3 5.3",
    import: "M12 3v12m-4-4 4 4 4-4M5 21h14",
    download: "M12 3v12m-4-4 4 4 4-4M5 21h14",
    plus: "M12 5v14M5 12h14",
    sync: "M20 7h-5V2M4 17h5v5M20 7a8 8 0 0 0-13-3M4 17a8 8 0 0 0 13 3",
    magic: "m5 19 14-14M15 5l4 4M5 4v3M3.5 5.5h3M18 16v4M16 18h4",
    refresh: "M20 6v5h-5M4 18v-5h5M19 11a7 7 0 0 0-12-4M5 13a7 7 0 0 0 12 4",
    filter: "M4 5h16l-6 7v6l-4 2v-8z",
    search: "m21 21-4.5-4.5M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0z",
    copy: "M9 9h11v11H9zM5 15V5h10",
    chevron: "m8 10 4 4 4-4",
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className="shrink-0"><path d={paths[name]} stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}

function ChainPanel({ overview, orders, total, pending, filter, loading, busy, prelinkBusy, xrefBusy, onFilterChange, onConfirm, onReject, onPrelink, onManualLink, onReload, onNotice, onStageView }: {
  overview: ChainOverview | null;
  orders: ChainOrderRow[];
  total: number;
  pending: PendingLink[];
  filter: ChainFilter;
  loading: boolean;
  busy: boolean;
  prelinkBusy: boolean;
  xrefBusy: boolean;
  onFilterChange: (value: ChainFilter) => void;
  onConfirm: (kind: string, linkId: number) => Promise<void>;
  onReject: (kind: string, linkId: number) => Promise<void>;
  onPrelink: (auto: boolean) => Promise<void>;
  onManualLink: (orderId: number, targetId: number) => Promise<void>;
  onReload?: () => Promise<void>;
  onNotice?: (text: string) => void;
  onStageView: (view: ViewMode) => void;
}) {
  const filtered = useMemo(() => {
    if (filter === "gap") return orders.filter((row) => row.doneCount < (row.stageTotal || 7));
    if (filter === "full") return orders.filter((row) => row.doneCount >= (row.stageTotal || 7));
    return orders;
  }, [filter, orders]);

  // 逐单候选展开：orderId -> 已加载候选
  const [candByOrder, setCandByOrder] = useState<Record<number, ChainLinkCandidate[]>>({});
  const [candLoadingId, setCandLoadingId] = useState<number | null>(null);
  const [expandedDetails, setExpandedDetails] = useState<Record<string, boolean>>({});
  async function toggleCandidates(row: ChainOrderRow) {
    const orderId = canonicalChainOrderId(row);
    if (candByOrder[orderId]) {
      setCandByOrder((prev) => { const next = { ...prev }; delete next[orderId]; return next; });
      return;
    }
    setCandLoadingId(orderId);
    try {
      const result = await procurementChainApi.candidates(orderId, "inbound", "", 8);
      setCandByOrder((prev) => ({ ...prev, [orderId]: result.items }));
    } catch {
      setCandByOrder((prev) => ({ ...prev, [orderId]: [] }));
    } finally {
      setCandLoadingId(null);
    }
  }
  async function linkCandidate(orderId: number, targetId: number) {
    await onManualLink(orderId, targetId);
    setCandByOrder((prev) => { const next = { ...prev }; delete next[orderId]; return next; });
  }

  // 对照表面板
  const [xrefOpen, setXrefOpen] = useState(false);
  const [xrefContent, setXrefContent] = useState("");
  const [xrefPreview, setXrefPreview] = useState<XrefPreview | null>(null);
  const [xrefApplyResult, setXrefApplyResult] = useState<XrefApplyResult | null>(null);
  const [xrefFileMeta, setXrefFileMeta] = useState<{ modifiedAt: number | null; size: number } | null>(null);
  async function openXrefPanel() {
    if (!xrefOpen) {
      try {
        const meta = await procurementChainApi.xrefFile();
        setXrefContent(meta.content || "");
        setXrefFileMeta({ modifiedAt: meta.modifiedAt, size: meta.size });
      } catch {
        setXrefContent("");
      }
    }
    setXrefOpen((v) => !v);
    setXrefPreview(null);
    setXrefApplyResult(null);
  }
  async function runXrefPreview() {
    if (!xrefContent.trim()) return;
    try {
      const result = await procurementChainApi.xrefPreview(xrefContent);
      setXrefPreview(result);
      setXrefApplyResult(null);
      onNotice?.(`预览完成：将创建 ${result.stats.to_create} 条，缺 ${result.stats.missing_order} 单 / ${result.stats.missing_rk} RK`);
    } catch (caught) {
      onNotice?.("对照表预览失败：" + String(caught));
    }
  }
  async function runXrefApply() {
    if (!xrefContent.trim()) return;
    try {
      const result = await procurementChainApi.xrefApply(xrefContent, true);
      setXrefApplyResult(result);
      setXrefPreview(null);
      onNotice?.(`对照表已应用：新建 ${result.created} 条链` + (result.missing_order.length || result.missing_rk.length
        ? `；缺 ${result.missing_order.length} 单 / ${result.missing_rk.length} RK` : ""));
      await onReload?.();
    } catch (caught) {
      onNotice?.("对照表应用失败：" + String(caught));
    }
  }

  return (
    <div className="mt-5 space-y-4">
      <section className="rounded-xl border border-slate-200/90 bg-white p-3.5 shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
        <div className="flex flex-wrap items-stretch gap-1.5">
          {(overview?.stages ?? []).map((stage) => {
        const done = stage.count > 0;
        const action = STAGE_ACTION[stage.key];
        const body = (
          <>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className={cx("inline-block h-1.5 w-1.5 shrink-0 rounded-full", DIM_DOT[stage.dimension] ?? "bg-slate-300")} />
                <span className={cx("text-[11px]", done ? "text-indigo-400" : "text-slate-400")}>
                  {CIRCLED[stage.no - 1] ?? stage.no} {stage.short}
                </span>
              </div>
              <div className={cx("mt-0.5 text-lg font-semibold leading-6", done ? "text-indigo-700" : "text-slate-400")}>
                {stage.count}
                <span className="ml-1 text-[11px] font-normal text-slate-400">{stage.pct}%</span>
              </div>
              <div className="truncate text-[12px] text-slate-400" title={stage.label}>{stage.label}</div>
            </div>
            <span className={cx("h-2 w-2 shrink-0 rounded-full", done ? "bg-indigo-500" : "bg-slate-200")} />
          </>
        );
        const shell = cx(
          "group flex min-w-[112px] flex-1 items-center justify-between rounded-lg px-3 py-2.5 transition-colors",
          done ? "bg-indigo-50/80 hover:bg-indigo-50" : "bg-slate-50 hover:bg-slate-100"
        );
        return action?.view ? (
          <button key={stage.key} type="button" onClick={() => onStageView(action.view as ViewMode)} className={shell}>{body}</button>
        ) : (
          <Link key={stage.key} href={action?.href ?? "#"} className={shell}>{body}</Link>
        );
          })}
          <div className="flex min-w-[104px] flex-1 items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
        <div>
          <div className="text-[11px] text-amber-500">待确认</div>
          <div className="mt-0.5 text-lg font-semibold leading-6 text-amber-600">{overview?.pending ?? 0}</div>
        </div>
        {Number(overview?.pending ?? 0) > 0 && (
          <span className="animate-pulse rounded-full bg-amber-400 px-1.5 py-0.5 text-[12px] font-medium text-white">需处理</span>
        )}
          </div>
        </div>
      </section>

      <QuickTriage
        orders={orders}
        pending={pending}
        busy={busy}
        onConfirm={onConfirm}
        onManualInbound={onManualLink}
        onReload={onReload}
        onNotice={onNotice}
      />

      <section className="rounded-xl border border-amber-200 bg-amber-50/40 p-3.5">
        <div className="mb-2.5 flex items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-amber-400" />
          <h2 className="text-[12px] font-semibold text-amber-800">关联待确认</h2>
          <span className="text-[11px] text-amber-500">{pending.length} 条建议</span>
        </div>
        {pending.length === 0 ? (
          <div className="rounded-lg border border-white bg-white/70 px-3 py-3 text-center text-[11px] text-amber-600/70">暂无待确认的关联建议；顶部「自动匹配并确认」会继续处理新数据</div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2">
        {pending.map((item) => (
          <div key={item.kind + "-" + item.linkId} className="flex items-center justify-between gap-3 rounded-lg border border-amber-100 bg-white px-3 py-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="truncate font-mono text-[11px] font-medium text-slate-800">{item.orderNo}</span>
                <span className="text-slate-300">→</span>
                <span className="truncate font-mono text-[11px] font-medium text-indigo-600">{item.targetNo}</span>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[12px] text-slate-400">
                <span className="rounded bg-slate-50 px-1 py-px text-slate-500">
                  {item.targetType === "inbound" ? "入库" : item.targetType === "settlement" ? "付款" : "发票"}
                </span>
                {item.confidence !== null && <span>置信度 {(item.confidence * 100).toFixed(0)}%</span>}
                {item.targetAmount !== null && <span>{fmtMoney(item.targetAmount)}</span>}
                {item.note && <span className="truncate">{item.note}</span>}
              </div>
            </div>
            <div className="flex shrink-0 gap-1.5">
              <button disabled={busy} onClick={() => void onConfirm(item.kind, item.linkId)} className="rounded-md bg-indigo-600 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-indigo-700 disabled:opacity-50">确认</button>
              <button disabled={busy} onClick={() => void onReject(item.kind, item.linkId)} className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] text-slate-500 hover:bg-slate-50 disabled:opacity-50">拒绝</button>
            </div>
          </div>
        ))}
          </div>
        )}
      </section>

      <section className="overflow-hidden rounded-xl border border-slate-200/90 bg-white shadow-[0_3px_12px_rgba(40,53,85,0.04)]">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-2.5">
          <div className="flex items-center gap-2">
        <span className="h-3.5 w-1 rounded-full bg-indigo-600" />
        <h2 className="text-[12px] font-semibold text-slate-800">订单链路</h2>
        <span className="text-[11px] text-slate-400">共 {total} 条 · 每单 7 环节完成度</span>
          </div>
          <div className="flex items-center gap-1.5">
        <button
          type="button"
          disabled={prelinkBusy}
          onClick={() => void onPrelink(true)}
          title="多因子打分（SKU 重合+供应商+时间窗）：达到阈值的候选自动关联并确认；没有耗材来源信息时明确记为不使用耗材，不扣库存。一笔订单可拆批关联多张入库单。"
          className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors disabled:opacity-50",
            "bg-indigo-600 text-white hover:bg-indigo-700"
          )}
        >
          {prelinkBusy ? "自动处理中…" : "⚡ 智能预关联并确认"}
        </button>
        <button
          type="button"
          onClick={openXrefPanel}
          className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
            xrefOpen
              ? "bg-violet-600 text-white"
              : "border border-violet-200 bg-violet-50 text-violet-600 hover:bg-violet-100"
          )}
          title="维护 1688 ↔ 入库单 手动交叉对照表：粘贴/编辑后点「解析预览」查看将创建/缺失明细，确认无误后点「应用」批量建链（同时落盘到 backend/data/1688_rk_xref.tsv）"
        >
          {xrefOpen ? "收起对照表" : "📋 对照表"}
        </button>
        {([["all", "全部"], ["gap", "有缺口"], ["full", "已完成"]] as Array<[ChainFilter, string]>).map(([key, label]) => (
          <button key={key} onClick={() => onFilterChange(key)} className={cx(
            "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors",
            filter === key ? "bg-indigo-600 text-white" : "bg-slate-50 text-slate-500 hover:bg-slate-100"
          )}>{label}</button>
        ))}
          </div>
        </div>
        {xrefOpen && (
          <section className="mt-3 rounded-xl border border-violet-200 bg-violet-50/30 p-3.5">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="h-3.5 w-1 rounded-full bg-violet-500" />
          <h3 className="text-[12px] font-semibold text-violet-800">1688↔入库单 对照表</h3>
          <span className="text-[12px] text-violet-500">
            粘贴或编辑 TSV（19 列：年度 订货日期 订单号 条形码 … 备注）后点「解析预览」查看将创建/缺失明细，确认后点「应用」批量建链并落盘。
          </span>
          {xrefFileMeta?.modifiedAt != null && (
            <span className="text-[12px] text-slate-400">
              当前文件 {Math.round((xrefFileMeta.size || 0) / 1024)} KB · 修改 {fmtDateTime(new Date(xrefFileMeta.modifiedAt * 1000).toISOString())}
            </span>
          )}
        </div>
        <textarea
          value={xrefContent}
          onChange={(e) => setXrefContent(e.target.value)}
          placeholder={"年度\t订货日期\t订单号\t条形码\t…\n2025年\t2025/7/23\t4648732165543821020\t2020240528007\t…"}
          className="h-48 w-full resize-y rounded-md border border-violet-100 bg-white px-2 py-1.5 font-mono text-[12px] text-slate-800 focus:border-violet-400 focus:outline-none"
          spellCheck={false}
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={xrefBusy || !xrefContent.trim()}
            onClick={() => void runXrefPreview()}
            className="rounded-md border border-violet-200 bg-white px-2.5 py-1 text-[12px] font-medium text-violet-700 hover:bg-violet-50 disabled:opacity-50"
          >解析预览</button>
          <button
            type="button"
            disabled={xrefBusy || !xrefContent.trim()}
            onClick={() => void runXrefApply()}
            className="rounded-md bg-violet-600 px-2.5 py-1 text-[12px] font-medium text-white hover:bg-violet-700 disabled:opacity-50"
          >应用（写入并落盘）</button>
          <button
            type="button"
            onClick={async () => {
              const meta = await procurementChainApi.xrefFile();
              setXrefContent(meta.content || "");
              setXrefFileMeta({ modifiedAt: meta.modifiedAt, size: meta.size });
              setXrefPreview(null);
              setXrefApplyResult(null);
              onNotice?.("已重新加载文件内容");
            }}
            className="rounded-md border border-slate-200 bg-white px-2.5 py-1 text-[12px] text-slate-500 hover:bg-slate-50"
          >从文件重新加载</button>
          <span className="text-[12px] text-slate-400">已写入字节：{xrefContent.length.toLocaleString()}</span>
        </div>
        {xrefPreview && (
          <div className="mt-2 rounded-md border border-slate-100 bg-white p-2 text-[12px] text-slate-700">
            <div className="flex flex-wrap gap-3">
              <span>总行 {xrefPreview.totalXrefRows} / 唯一对 {xrefPreview.totalPairs}</span>
              <span className="text-emerald-600">将创建 {xrefPreview.stats.to_create}</span>
              <span className="text-slate-500">已存在 {xrefPreview.stats.already_linked}</span>
              <span className="text-amber-600">缺订单 {xrefPreview.stats.missing_order} / 缺RK {xrefPreview.stats.missing_rk}</span>
              <span className="text-slate-500">组合装 {xrefPreview.stats.combo_skipped}</span>
              {xrefPreview.noiseLinkCount > 0 && <span className="text-rose-600">噪声链 {xrefPreview.noiseLinkCount}</span>}
            </div>
            {xrefPreview.missingOrder.length > 0 && (
              <div className="mt-1 text-rose-600">缺 1688 订单：{xrefPreview.missingOrder.join("、") || "—"}</div>
            )}
            {xrefPreview.missingRk.length > 0 && (
              <div className="mt-1 text-rose-600">缺 RK 入库单：{xrefPreview.missingRk.join("、") || "—"}</div>
            )}
            {xrefPreview.toCreate.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer text-slate-500">查看前 {xrefPreview.toCreate.length} 条将创建</summary>
                <div className="mt-1 max-h-32 overflow-y-auto font-mono text-[12px] text-slate-600">
                  {xrefPreview.toCreate.map((c, i) => (
                    <div key={i}>{c.order_no} → {c.rk_no}　SKUs {c.barcodes.join("+")}　qty盒={c.qty_boxes}　{c.note_extra && `备注：${c.note_extra.slice(0, 30)}`}</div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
        {xrefApplyResult && (
          <div className="mt-2 rounded-md border border-emerald-100 bg-emerald-50/50 p-2 text-[12px] text-emerald-800">
            <div className="flex flex-wrap gap-3">
              <span>本次新建 {xrefApplyResult.created} 条链</span>
              <span className="text-slate-500">对照表行 {xrefApplyResult.total_xref_rows} / 对 {xrefApplyResult.total_pairs}</span>
              <span className="text-slate-500">已存在 {xrefApplyResult.already_linked_count}</span>
              <span className="text-slate-500">组合装 {xrefApplyResult.combo_skipped_count}</span>
              {(xrefApplyResult.missing_order.length || xrefApplyResult.missing_rk.length) > 0 && (
                <span className="text-amber-700">缺 {xrefApplyResult.missing_order.length} 单 / {xrefApplyResult.missing_rk.length} RK（需先同步入库/订单）</span>
              )}
              {xrefApplyResult.noise_links.length > 0 && <span className="text-rose-700">噪声链 {xrefApplyResult.noise_links.length} 条（建议人工拒绝）</span>}
            </div>
            {xrefApplyResult.noise_links.length > 0 && (
              <div className="mt-1">id 列表：{xrefApplyResult.noise_links.map((n) => `${n.id}`).join("、")}</div>
            )}
          </div>
        )}
          </section>
        )}
        {loading ? <Loading text="正在加载采购链路…" /> : filtered.length === 0 ? <Empty text="没有符合条件的订单链路" /> : (
          <div className="divide-y divide-slate-100">
        {filtered.map((row) => {
          const chainOrderId = canonicalChainOrderId(row);
          const stageTotal = row.stageTotal || 7;
          const pct = Math.round((row.doneCount / stageTotal) * 100);
          return (
            <div key={row.orderId} className="px-4 py-3 hover:bg-slate-50/60">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-[11.5px] font-semibold text-slate-800">{row.orderNo}</span>
                    {row.pendingCount > 0 && (
                      <span className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[12px] font-medium text-amber-600">{row.pendingCount} 条待确认</span>
                    )}
                    {row.source === "workflow" && (
                      <span className="shrink-0 rounded bg-violet-50 px-1.5 py-0.5 text-[12px] font-medium text-violet-600">手工</span>
                    )}
                  </div>
                  <div className="mt-0.5 truncate text-[12px] text-slate-400">
                    {row.supplier || "—"}　·　{fmtDate(row.orderDate)}　·　{row.title || "无标题"}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-4">
                  <div className="text-right">
                    <div className="text-[13px] font-semibold tabular-nums text-slate-900">{fmtMoney(row.amount)}</div>
                    <div className="text-[12px] text-slate-400">完成度 {row.doneCount}/{stageTotal}</div>
                  </div>
                  <div className="w-24">
                    <div className="h-1.5 overflow-hidden rounded-full bg-slate-100">
                      <div className={cx("h-full rounded-full", row.doneCount >= stageTotal ? "bg-emerald-500" : "bg-indigo-500")} style={{ width: pct + "%" }} />
                    </div>
                  </div>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1">
                {row.stages.map((stage) => (
                  <span key={stage.key} title={stage.label} className={cx(
                    "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px]",
                    stage.done ? "bg-emerald-50 text-emerald-600" : "bg-slate-50 text-slate-400"
                  )}>
                    <span className={cx("inline-block h-1.5 w-1.5 rounded-full", stage.done ? "bg-emerald-500" : DIM_DOT[stage.dimension] ?? "bg-slate-300")} />
                    {CIRCLED[stage.no - 1] ?? stage.no} {stage.short}
                  </span>
                ))}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={busy || candLoadingId === chainOrderId}
                  onClick={() => void toggleCandidates(row)}
                  className="rounded-md border border-indigo-100 bg-indigo-50/60 px-2 py-0.5 text-[12px] font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-50"
                >
                  {candLoadingId === chainOrderId ? "加载中…" : candByOrder[chainOrderId] ? "收起候选" : "候选入库单"}
                </button>
                {(row.inbound ?? []).length > 0 && (
                  <span className="text-[12px] text-slate-400">
                    已关联 {row.inbound.length} 张：{row.inbound.map((i) => i.goodsdocNo).join("、")}
                  </span>
                )}
              </div>
              {candByOrder[chainOrderId] && (
                <div className="mt-2 rounded-lg border border-slate-100 bg-slate-50/60 p-2">
                  {candByOrder[chainOrderId].length === 0 ? (
                    <div className="px-1 py-1 text-[12px] text-slate-400">没有可用的入库单候选（可能是全新单据，请先同步或文件导入入库单）</div>
                  ) : (
                    <div className="divide-y divide-slate-100">
                      {candByOrder[chainOrderId].map((c) => (
                        <div key={c.targetId} className="flex items-start justify-between gap-3 py-1.5">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span className="font-mono text-[12px] font-semibold text-slate-800">{c.targetNo}</span>
                              {c.currentlyLinked && <span className="rounded bg-emerald-50 px-1 text-[11px] text-emerald-600">已关联</span>}
                              {c.pendingSuggestion && <span className="rounded bg-amber-50 px-1 text-[11px] text-amber-600">待确认建议</span>}
                              {c.skuOverlapRatio !== null && c.skuOverlapRatio > 0 && (
                                <span className="rounded bg-indigo-50 px-1 text-[11px] text-indigo-600">SKU 重合 {c.matchedSkuCount}/{c.requiredSkuCount}</span>
                              )}
                              <span className={cx("rounded px-1 text-[11px]", c.score >= 0.5 ? "bg-emerald-50 text-emerald-600" : "bg-slate-100 text-slate-400")}>
                                匹配 {(c.score * 100).toFixed(0)}%
                              </span>
                            </div>
                            <div className="mt-0.5 text-[12px] text-slate-400">
                              {c.targetSupplier || "无供应商"} · {fmtDate(c.targetDate)} · {c.reason}
                            </div>
                            {c.details.length > 0 && (() => {
                              const open = expandedDetails[`${chainOrderId}:${c.targetId}`];
                              return (
                                <div className="mt-1">
                                  <button
                                    type="button"
                                    onClick={() => setExpandedDetails((m) => ({ ...m, [`${chainOrderId}:${c.targetId}`]: !open }))}
                                    className="inline-flex items-center gap-1 rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-slate-50"
                                  >
                                    📦 明细 ({c.details.length}) {open ? "▴" : "▾"}
                                  </button>
                                  {open && (
                                    <div className="mt-1 overflow-x-auto rounded border border-slate-100 bg-white">
                                      <table className="w-full text-[11px]">
                                        <thead className="bg-slate-50 text-slate-500">
                                          <tr>
                                            <th className="px-1.5 py-0.5 text-left font-medium">货品</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">规格</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">数量</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">含税单价</th>
                                            <th className="px-1.5 py-0.5 text-right font-medium">含税金额</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">条码</th>
                                            <th className="px-1.5 py-0.5 text-left font-medium">SKU匹配</th>
                                          </tr>
                                        </thead>
                                        <tbody className="divide-y divide-slate-50">
                                          {c.details.map((d, i) => (
                                            <tr key={i} className="hover:bg-slate-50/50">
                                              <td className="px-1.5 py-0.5 text-slate-700">{d.goodsName || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-slate-500">{d.spec || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.applyQuantity ?? d.quantity ?? 0}{d.unitName || ""}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.unitPriceTax != null ? d.unitPriceTax.toFixed(2) : "—"}</td>
                                              <td className="px-1.5 py-0.5 text-right font-mono text-slate-700">{d.amountTax != null ? d.amountTax.toFixed(2) : "—"}</td>
                                              <td className="px-1.5 py-0.5 font-mono text-[11px] text-slate-500">{d.barcode || "—"}</td>
                                              <td className="px-1.5 py-0.5 text-[11px]">
                                                {d.skuId ? <span className="rounded bg-indigo-50 px-1 text-indigo-600">#{d.skuId}·{d.matchStatus || "auto"}</span> : <span className="text-slate-300">未匹配</span>}
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  )}
                                </div>
                              );
                            })()}
                          </div>
                          <button
                            type="button"
                            disabled={busy || c.currentlyLinked}
                            onClick={() => void linkCandidate(chainOrderId, c.targetId)}
                            className="shrink-0 rounded-md bg-indigo-600 px-2 py-0.5 text-[12px] font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                          >
                            {c.currentlyLinked ? "已关联" : "关联"}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
          </div>
        )}
      </section>
    </div>
  );
}

// ---------- SKU 匹配工作台 ----------

const MATCH_STATUS_META: Record<string, { label: string; cls: string }> = {
  price_ok: { label: "金额校验通过", cls: "bg-emerald-50 text-emerald-600" },
  auto: { label: "自动命中", cls: "bg-blue-50 text-blue-600" },
  manual: { label: "人工指定", cls: "bg-indigo-50 text-indigo-600" },
  price_mismatch: { label: "金额异常", cls: "bg-red-50 text-red-600" },
  missing: { label: "档案缺失", cls: "bg-amber-50 text-amber-700" },
  unmatched: { label: "未匹配", cls: "bg-slate-100 text-slate-500" },
};

function MatchStatusChip({ status }: { status: string }) {
  const meta = MATCH_STATUS_META[status] ?? MATCH_STATUS_META.unmatched;
  return <span className={cx("rounded px-1.5 py-0.5 text-[12px] font-medium", meta.cls)}>{meta.label}</span>;
}

function MatchingView({ summary, pending, busy, loading, onRunAuto, onRefresh, onNotice, onOpenOrder }: {
  summary: InboundMatchSummary | null;
  pending: PendingAllocation[];
  busy: boolean;
  loading: boolean;
  onRunAuto: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
  onOpenOrder: (orderId: number) => void;
}) {
  const counts = summary?.counts ?? {};
  return (
    <div className="mt-5 space-y-4">
      <InboundMatchCard summary={summary} counts={counts} busy={busy} loading={loading} onRunAuto={onRunAuto} onRefresh={onRefresh} onNotice={onNotice} />
      <PendingAllocationCard pending={pending} loading={loading} onRefresh={onRefresh} onNotice={onNotice} onOpenOrder={onOpenOrder} />
    </div>
  );
}

function InboundMatchCard({ summary, counts, busy, loading, onRunAuto, onRefresh, onNotice }: {
  summary: InboundMatchSummary | null;
  counts: Record<string, number>;
  busy: boolean;
  loading: boolean;
  onRunAuto: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
}) {
  const [skus, setSkus] = useState<CatalogSkuRow[]>([]);
  const [savingItem, setSavingItem] = useState<number | null>(null);
  useEffect(() => {
    skuMatchingApi.skus().then(setSkus).catch(() => setSkus([]));
  }, []);
  const anomalies = summary?.anomalies ?? [];
  const manualMatches = summary?.manualMatches ?? [];

  async function assign(itemId: number, skuId: number) {
    if (!skuId) return;
    setSavingItem(itemId);
    try {
      await skuMatchingApi.manualInbound(itemId, skuId);
      onNotice("人工 SKU 匹配已保存，后续自动扫描不会覆盖");
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "人工指定失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function unassign(itemId: number) {
    if (!window.confirm("确认解除这条入库明细的 SKU 匹配？\n\n该明细会保留为人工待处理，自动扫描不会重新绑定；你可以随后选择正确的 SKU。")) return;
    setSavingItem(itemId);
    try {
      await skuMatchingApi.clearManualInbound(itemId);
      onNotice("SKU 匹配已解除，明细保留为人工待处理");
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "解除匹配失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function acceptCost(itemId: number) {
    if (!window.confirm("确认更新吉客云 SKU 主档成本？\n\n系统优先按已关联的 1688 实付金额比例分摊；未关联时才使用入库含税单价。该变更会影响后续利润计算，并记录审计日志。")) return;
    setSavingItem(itemId);
    try {
      const res = await skuMatchingApi.acceptCost(itemId);
      onNotice(`已确认成本：${res.oldCost ?? "无"} → ${res.newCost}`);
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "确认成本失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  async function batchAcceptCost(documentId: number, docNo: string, count: number) {
    if (!window.confirm(`确认按入库单 ${docNo} 批量确认成本？\n\n将处理该单 ${count} 条金额异常明细：已关联 1688 订单时按实付比例分摊，未关联时按入库含税单价更新 SKU 主档成本。该变更会影响后续利润计算。`)) return;
    setSavingItem(-documentId);
    try {
      const res = await skuMatchingApi.batchAcceptCost(documentId);
      onNotice(
        `按单批量确认完成：接受 ${res.accepted} 条` +
        (res.failed.length ? `，跳过 ${res.failed.length} 条（请核对异常说明）` : "")
      );
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "批量确认失败，请重试");
    } finally {
      setSavingItem(null);
    }
  }

  return (
    <section className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-[14px] font-semibold text-slate-900">吉客云入库明细 ↔ SKU</h2>
          <p className="mt-0.5 text-[11px] text-slate-400">SKU 条码/货号命中主档后校验金额；系统识别异常，人工可随时改绑，且人工结果不被自动覆盖</p>
        </div>
        <div className="flex items-center gap-2">
          <HeaderButton icon="sync" busy={busy} onClick={() => void onRunAuto()}>扫描未人工锁定项</HeaderButton>
          <HeaderButton icon="refresh" onClick={() => void onRefresh()}>刷新</HeaderButton>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {[
          { label: "明细总数", value: summary?.total ?? 0, cls: "bg-slate-100 text-slate-600" },
          { label: "金额通过", value: counts.price_ok ?? 0, cls: "bg-emerald-50 text-emerald-600" },
          { label: "自动命中", value: counts.auto ?? 0, cls: "bg-blue-50 text-blue-600" },
          { label: "人工锁定", value: counts.manual ?? 0, cls: "bg-indigo-50 text-indigo-600" },
          { label: "金额异常", value: counts.price_mismatch ?? 0, cls: "bg-red-50 text-red-600" },
          { label: "档案缺失", value: counts.missing ?? 0, cls: "bg-amber-50 text-amber-700" },
        ].map((chip) => (
          <span key={chip.label} className={cx("rounded-lg px-2.5 py-1.5 text-[11px] font-medium", chip.cls)}>
        {chip.label} <span className="ml-1 text-[13px] font-semibold">{chip.value}</span>
          </span>
        ))}
      </div>
      {anomalies.length > 0 && (
        <div className="mt-3 rounded-lg border border-red-100 bg-red-50/50 p-3">
          <div className="mb-2 text-[11px] font-medium text-red-600">以下 {anomalies.length} 条明细需要人工确认</div>
          <div className="space-y-2.5">
        {Array.from(
          anomalies.reduce((map, item) => {
            const group = map.get(item.documentId);
            if (group) group.items.push(item);
            else map.set(item.documentId, { docNo: item.goodsdocNo, items: [item] });
            return map;
          }, new Map<number, { docNo: string; items: InboundMatchSummary["anomalies"] }>())
        ).map(([documentId, group]) => {
          const mismatchCount = group.items.filter((i) => i.status === "price_mismatch" && i.matchedSkuId != null).length;
          return (
            <div key={documentId} className="rounded-lg border border-slate-100 bg-white/70 p-2">
              <div className="mb-1.5 flex flex-wrap items-center gap-2 px-1">
                <span className="text-[11px] font-medium text-slate-700">{group.docNo}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[12px] text-slate-500">{group.items.length} 条异常</span>
                {mismatchCount > 1 && (
                  <button
                    onClick={() => void batchAcceptCost(documentId, group.docNo, mismatchCount)}
                    disabled={savingItem !== null}
                    className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[12px] font-medium text-emerald-600 hover:bg-emerald-100 disabled:opacity-50"
                    title={`一次确认该单 ${mismatchCount} 条异常明细的档案成本；历史价格偏差过大时会被拦截`}
                  >
                    按单批量确认成本（{mismatchCount}）
                  </button>
                )}
              </div>
              <InboundMatchRows items={group.items} skus={skus} savingItem={savingItem} onAssign={assign} onAcceptCost={acceptCost} />
            </div>
          );
        })}
          </div>
        </div>
      )}
      {!loading && anomalies.length === 0 && (
        <div className="mt-3 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[11px] text-emerald-700">当前没有待处理的入库明细异常。</div>
      )}
      {manualMatches.length > 0 && (
        <details className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50/30">
          <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium text-indigo-700">人工已匹配 {manualMatches.length} 条（展开可再次更换）</summary>
          <div className="border-t border-indigo-100 p-3">
        <InboundMatchRows items={manualMatches} skus={skus} savingItem={savingItem} onAssign={assign} onUnassign={unassign} />
          </div>
        </details>
      )}
      {loading && <div className="mt-2 text-[11px] text-slate-400">加载中…</div>}
    </section>
  );
}

function InboundMatchRows({ items, skus, savingItem, onAssign, onUnassign, onAcceptCost }: {
  items: InboundMatchSummary["anomalies"];
  skus: CatalogSkuRow[];
  savingItem: number | null;
  onAssign: (itemId: number, skuId: number) => Promise<void>;
  onUnassign?: (itemId: number) => Promise<void>;
  onAcceptCost?: (itemId: number) => Promise<void>;
}) {
  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.itemId} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-100 bg-white px-3 py-2 text-[11px]">
          <MatchStatusChip status={item.status} />
          <span className="font-medium text-slate-700">{item.goodsdocNo}</span>
          <span className="font-mono text-[12px] text-slate-400">{item.goodsNo || "无货号"}</span>
          <span className="text-slate-600">{item.goodsName || "未命名商品"}</span>
          <span className="text-slate-400">数量 {item.quantity ?? "-"}</span>
          <span className="text-slate-400">金额 {fmtMoney(item.amountTax)}</span>
          <span className="min-w-[160px] flex-1 truncate text-amber-600" title={item.note}>{item.note || "人工已确认"}</span>
          <SearchableSelect
        ariaLabel={`为 ${item.goodsdocNo} 选择 SKU`}
        disabled={savingItem !== null}
        className="max-w-[280px]"
        placeholder="选择吉客云 SKU…"
        value={item.status === "manual" && item.matchedSkuId ? String(item.matchedSkuId) : ""}
        onChange={(next) => void onAssign(item.itemId, Number(next))}
        options={skus.map((sku) => ({
          value: String(sku.id), label: `${sku.skuCode} · ${sku.skuName || sku.goodsName}`, keywords: `${sku.skuCode} ${sku.skuName ?? ""} ${sku.goodsName ?? ""}`,
        }))}
      />
          {onAcceptCost && item.status === "price_mismatch" && item.matchedSkuId != null && (
        <button
          onClick={() => void onAcceptCost(item.itemId)}
          disabled={savingItem !== null}
          className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[12px] font-medium text-emerald-600 hover:bg-emerald-100 disabled:opacity-50"
          title="确认更新档案成本：优先按关联 1688 实付比例分摊，否则使用入库含税单价"
        >
          确认成本
        </button>
          )}
          {onUnassign && item.status === "manual" && item.matchedSkuId != null && (
        <button
          onClick={() => void onUnassign(item.itemId)}
          disabled={savingItem !== null}
          className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[12px] font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          title="解除当前 SKU，保留为人工待处理且不参与自动扫描"
        >
          解除匹配
        </button>
          )}
        </div>
      ))}
    </div>
  );
}

function PendingAllocationCard({ pending, loading, onRefresh, onNotice, onOpenOrder }: {
  pending: PendingAllocation[];
  loading: boolean;
  onRefresh: () => Promise<void>;
  onNotice: (text: string) => void;
  onOpenOrder: (orderId: number) => void;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const [candidates, setCandidates] = useState<Record<number, SkuCandidate[]>>({});
  const [draft, setDraft] = useState<{ skuId: number; quantity: string; unitPrice: string } | null>(null);
  const [savingPo, setSavingPo] = useState<number | null>(null);

  const toggle = async (poId: number) => {
    if (expanded === poId) {
      setExpanded(null);
      return;
    }
    setExpanded(poId);
    setDraft(null);
    if (!candidates[poId]) {
      try {
        const res = await skuMatchingApi.candidates(poId);
        setCandidates((c) => ({ ...c, [poId]: res.candidates }));
      } catch {
        setCandidates((c) => ({ ...c, [poId]: [] }));
      }
    }
  };

  const confirm = async (po: PendingAllocation) => {
    if (!draft) return;
    const quantity = Number(draft.quantity);
    const unitPrice = Number(draft.unitPrice);
    if (!po.editable) {
      onNotice("该订单已确认采购内容，如需改绑请先在订单详情核对状态");
      return;
    }
    if (!Number.isFinite(quantity) || quantity <= 0 || !Number.isFinite(unitPrice) || unitPrice < 0) {
      onNotice("请填写有效的数量和单价");
      return;
    }
    setSavingPo(po.poId);
    try {
      await skuMatchingApi.addAllocation(po.poId, {
        sku_id: draft.skuId,
        quantity: draft.quantity,
        unit_price: draft.unitPrice,
      });
      onNotice(`订单 ${po.externalOrderId} SKU 分配已保存`);
      setDraft(null);
      setExpanded(null);
      await onRefresh();
    } catch (caught) {
      onNotice(caught instanceof Error ? caught.message : "保存失败，请检查数量/单价后重试");
    } finally {
      setSavingPo(null);
    }
  };

  return (
    <section className="rounded-xl border border-slate-200/80 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-[14px] font-semibold text-slate-900">1688 订单 SKU 配置</h2>
          <p className="mt-0.5 text-[11px] text-slate-400">1688 源数据无商品明细，需人工指定 SKU；候选按同供应商历史入库货品推荐，金额不平衡会提示</p>
        </div>
        <span className="rounded-lg bg-slate-100 px-2.5 py-1.5 text-[11px] font-medium text-slate-600">待配置 {pending.length}</span>
      </div>
      <div className="mt-3 space-y-2">
        {loading ? <div className="text-[11px] text-slate-400">加载中…</div> : pending.length === 0 ? (
          <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2 text-[12px] text-emerald-600">全部订单已配置且金额平衡 ✓</div>
        ) : pending.map((po) => (
          <div key={po.poId} className="rounded-lg border border-slate-100">
        <div className="flex items-center gap-2 pr-3 hover:bg-slate-50">
          <button onClick={() => void toggle(po.poId)} className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left text-[11px]">
            <span className="font-medium text-slate-700">{po.supplierName || "(未知供应商)"}</span>
            <span className="font-mono text-slate-400">{po.externalOrderId}</span>
            <span className="text-slate-400">采购时间 {fmtDateTime(po.orderedAt)}</span>
            <span className="text-slate-400">实付 {fmtMoney(po.paidAmount)}</span>
            {po.allocationCount > 0 && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">已配 {po.allocationCount} 条</span>}
            {!po.balance.balanced && po.allocationCount > 0 && (
              <span className="rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600" title={po.balance.abnormalNote}>金额不平衡</span>
            )}
            {!po.editable && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">当前状态已锁定</span>}
            <span className="ml-auto text-slate-300">{expanded === po.poId ? "收起 ▲" : "配置 ▼"}</span>
          </button>
          <button onClick={() => onOpenOrder(po.fileOrderId ?? -po.poId)} className="shrink-0 rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] font-medium text-indigo-600 hover:bg-indigo-50">进入订单调整</button>
        </div>
        {expanded === po.poId && (
          <div className="border-t border-slate-100 px-3 py-2.5">
            {(candidates[po.poId] ?? []).length > 0 && (
              <div className="mb-2">
                <div className="mb-1 text-[10px] font-medium text-slate-400">候选（同供应商历史优先，其余来自吉客云 SKU 主档）</div>
                <div className="flex flex-wrap gap-1.5">
                  {(candidates[po.poId] ?? []).map((c) => (
                    <button
                      key={c.skuId}
                      onClick={() => setDraft({
                        skuId: c.skuId,
                        quantity: "1",
                        unitPrice: c.defaultCost != null && c.defaultCost > 0 ? String(c.defaultCost) : po.paidAmount != null && po.paidAmount > 0 ? String(po.paidAmount) : "0",
                      })}
                      className={cx(
                        "rounded-md border px-2 py-1 text-left text-[10.5px]",
                        draft?.skuId === c.skuId ? "border-indigo-300 bg-indigo-50 text-indigo-600" : "border-slate-200 text-slate-600 hover:border-indigo-200 hover:bg-indigo-50/50"
                      )}
                      title={c.reason}
                    >
                      <span className="font-mono">{c.skuCode}</span> · {c.skuName || "未命名 SKU"}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {draft && (
              <div className="flex flex-wrap items-center gap-2 text-[11px]">
                <span className="text-slate-500">数量</span>
                <input value={draft.quantity} onChange={(e) => setDraft({ ...draft, quantity: e.target.value })} className="w-16 rounded border border-slate-200 px-1.5 py-1" />
                <span className="text-slate-500">单价 ¥</span>
                <input value={draft.unitPrice} onChange={(e) => setDraft({ ...draft, unitPrice: e.target.value })} className="w-24 rounded border border-slate-200 px-1.5 py-1" />
                <span className="text-slate-400">新增 {fmtMoney(Number(draft.quantity || 0) * Number(draft.unitPrice || 0))}</span>
                {po.paidAmount != null && Math.abs(po.balance.allocated + Number(draft.quantity || 0) * Number(draft.unitPrice || 0) - po.paidAmount) > Math.max(po.paidAmount * 0.02, 0.5) && (
                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700">新增后累计 {fmtMoney(po.balance.allocated + Number(draft.quantity || 0) * Number(draft.unitPrice || 0))}，与实付 {fmtMoney(po.paidAmount)} 仍不一致</span>
                )}
                {po.editable ? (
                  <HeaderButton icon="plus" busy={savingPo === po.poId} onClick={() => void confirm(po)}>保存分配</HeaderButton>
                ) : (
                  <span className="rounded-md bg-slate-100 px-2.5 py-1.5 text-[10px] text-slate-500">已确认状态不可直接改动</span>
                )}
              </div>
            )}
            {!draft && (candidates[po.poId] ?? []).length === 0 && (
              <div className="text-[11px] text-slate-400">暂无候选，可在采购订单详情中手动添加分配</div>
            )}
          </div>
        )}
          </div>
        ))}
      </div>
    </section>
  );
}
