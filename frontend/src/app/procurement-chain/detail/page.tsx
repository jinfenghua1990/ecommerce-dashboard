"use client";

import { useState, useEffect, useCallback, Suspense, type ReactNode } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  procurementChainApi,
  ChainOrderDetail,
  ChainSuggestion,
  ChainInvoice,
} from "@/lib/api";

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return "¥" + v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return "—";
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 维度标记 */
const DIM = {
  "1688": { label: "1688 维度", bar: "bg-indigo-500", badge: "bg-indigo-100 text-indigo-700", rail: "bg-indigo-500" },
  purchase: { label: "采购中心", bar: "bg-violet-500", badge: "bg-violet-100 text-violet-700", rail: "bg-violet-500" },
  jackyun: { label: "吉客云维度", bar: "bg-teal-500", badge: "bg-teal-100 text-teal-700", rail: "bg-teal-500" },
  tax: { label: "税务维度", bar: "bg-amber-500", badge: "bg-amber-100 text-amber-700", rail: "bg-amber-500" },
} as const;

type SuggestionListProps = {
  items: ChainSuggestion[];
  busy: boolean;
  onConfirm: (s: ChainSuggestion) => void;
  onReject: (s: ChainSuggestion) => void;
};

function SuggestionList({ items, busy, onConfirm, onReject }: SuggestionListProps) {
  if (items.length === 0) return null;
  return (
    <div className="mt-2 space-y-1.5 rounded-lg border border-dashed border-amber-300 bg-amber-50/50 p-2">
      <div className="text-[11px] font-medium text-amber-700">待确认关联建议（{items.length}）</div>
      {items.map((s) => (
        <div key={s.linkId} className="flex items-center justify-between gap-2 rounded-md bg-white px-2.5 py-1.5">
          <div className="min-w-0 text-xs">
            <span className="font-medium text-gray-800">{s.targetNo}</span>
            {s.targetAmount !== null && (
              <span className="ml-2 tabular-nums text-gray-500">{fmtMoney(s.targetAmount)}</span>
            )}
            <div className="mt-0.5 text-[11px] text-gray-400">
              {s.confidence !== null && <span>置信度 {(s.confidence * 100).toFixed(0)}%　</span>}
              {s.note || "自动匹配"}
            </div>
          </div>
          <div className="flex shrink-0 gap-1.5">
            <button
              onClick={() => onConfirm(s)}
              disabled={busy}
              className="rounded-md bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              确认关联
            </button>
            <button
              onClick={() => onReject(s)}
              disabled={busy}
              className="rounded-md border border-gray-200 px-2.5 py-1 text-xs text-gray-500 hover:bg-gray-50 disabled:opacity-50"
            >
              拒绝
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

/** 环节卡片骨架：左侧轨道序号 + 卡片（维度色条 + 标题 + 状态标签 + 内容） */
function StageCard({
  no,
  title,
  dim,
  status,
  statusTone,
  children,
}: {
  no: string;
  title: string;
  dim: keyof typeof DIM;
  status: string;
  statusTone: "done" | "todo" | "warn";
  children: ReactNode;
}) {
  const d = DIM[dim];
  const toneCls =
    statusTone === "done"
      ? "bg-emerald-50 text-emerald-600"
      : statusTone === "warn"
      ? "bg-amber-50 text-amber-600"
      : "bg-gray-100 text-gray-500";
  return (
    <div className="relative flex gap-4 pb-5 last:pb-0">
      {/* 左侧轨道：序号节点 + 连接线 */}
      <div className="flex flex-col items-center">
        <span
          className={`z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-white ${d.rail}`}
        >
          {no}
        </span>
        <span className="absolute top-7 bottom-0 left-1/2 w-px -translate-x-1/2 bg-gray-200" />
      </div>

      {/* 卡片 */}
      <div className="min-w-0 flex-1 overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="flex items-center justify-between gap-2 border-b border-gray-100 py-2 pl-3.5 pr-3">
          <div className="flex items-center gap-2">
            <span className={`h-3.5 w-1 rounded-full ${d.bar}`} />
            <h3 className="text-sm font-medium text-gray-800">{title}</h3>
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${d.badge}`}>{d.label}</span>
          </div>
          <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${toneCls}`}>{status}</span>
        </div>
        <div className="p-3">{children}</div>
      </div>
    </div>
  );
}

function EmptyStage({ text, href, act }: { text: string; href: string; act: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-3">
      <div className="text-xs text-gray-400">{text}</div>
      <Link
        href={href}
        className="shrink-0 rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100"
      >
        {act} →
      </Link>
    </div>
  );
}

function DetailContent() {
  const params = useSearchParams();
  const orderId = Number(params.get("id"));
  const [detail, setDetail] = useState<ChainOrderDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!Number.isFinite(orderId) || orderId <= 0) {
      setError("缺少订单参数");
      setLoading(false);
      return;
    }
    try {
      const d = await procurementChainApi.orderDetail(orderId);
      setDetail(d);
    } catch {
      setError("订单不存在或加载失败");
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    load();
  }, [load]);

  async function confirmSuggestion(s: ChainSuggestion) {
    setBusy(true);
    try {
      if (s.kind === "invoice") await procurementChainApi.confirmInvoiceLink(s.linkId);
      else await procurementChainApi.confirmLink(s.linkId);
      setNotice(`已确认关联：${s.targetNo}`);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function rejectSuggestion(s: ChainSuggestion) {
    setBusy(true);
    try {
      if (s.kind === "invoice") await procurementChainApi.deleteInvoiceLink(s.linkId);
      else await procurementChainApi.deleteLink(s.linkId);
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function toggleVerify(inv: ChainInvoice) {
    setBusy(true);
    try {
      const now = new Date();
      const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
      await procurementChainApi.verifyInvoice(inv.invoiceId, !inv.verified, inv.verified ? inv.verifiedMonth : month);
      await load();
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-400">
        <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-indigo-500" />
        加载中…
      </div>
    );
  }

  if (error || !detail) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-gray-50">
        <p className="text-sm text-gray-400">{error || "订单不存在"}</p>
        <Link
          href="/procurement-chain"
          className="rounded-lg border border-gray-200 bg-white px-4 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-50"
        >
          ← 返回订单列表
        </Link>
      </div>
    );
  }

  // 7 环节完成度：优先用后端口径，缺字段时本地兜底
  const stageTotal = detail.stageTotal ?? 7;
  const n =
    typeof detail.doneCount === "number"
      ? detail.doneCount
      : 1 +
        (detail.purchaseContentComplete && detail.allocations.length > 0 ? 1 : 0) +
        (detail.purchaseOrders.length > 0 ? 1 : 0) +
        (detail.inbound.length > 0 ? 1 : 0) +
        (detail.invoice.length > 0 ? 1 : 0) +
        (detail.settlement.some((s) => s.paid) ? 1 : 0) +
        (detail.verified ? 1 : 0);
  const full = n >= stageTotal;

  const sugInbound = detail.suggestions.filter((s) => s.targetType === "inbound");
  const sugSettle = detail.suggestions.filter((s) => s.targetType === "settlement");
  const sugInvoice = detail.suggestions.filter((s) => s.targetType === "invoice");
  const paidSettle = detail.settlement.filter((s) => s.paid);
  const unpaidSettle = detail.settlement.filter((s) => !s.paid);

  return (
    <div className="flex min-h-screen flex-col bg-gray-50">
      {/* 顶栏 */}
      <div className="sticky top-0 z-10 border-b border-gray-200 bg-white/95 px-6 py-3 backdrop-blur">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <Link
              href="/procurement-chain"
              className="rounded-lg border border-gray-200 bg-white px-2.5 py-1 text-xs font-medium text-gray-500 transition-colors hover:bg-gray-50"
            >
              ← 列表
            </Link>
            <span className="h-4 w-1 rounded-full bg-indigo-600" />
            <h1 className="text-[15px] font-semibold tracking-tight text-gray-900">采购链路详情</h1>
            <span className="ml-1 font-mono text-xs text-gray-500">{detail.orderNo}</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <span className={`text-sm font-semibold tabular-nums ${full ? "text-emerald-600" : "text-amber-600"}`}>
                {n}/{stageTotal}
              </span>
              <div className="h-1.5 w-20 overflow-hidden rounded-full bg-gray-100">
                <div
                  className={`h-full rounded-full ${full ? "bg-emerald-500" : "bg-amber-400"}`}
                  style={{ width: `${(n / stageTotal) * 100}%` }}
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="mx-auto w-full max-w-[1080px] flex-1 space-y-3 px-6 py-4">
        {notice && (
          <div className="flex items-center justify-between rounded-lg border border-indigo-100 bg-indigo-50/70 px-3.5 py-2 text-xs text-indigo-700">
            <span>{notice}</span>
            <button onClick={() => setNotice("")} className="text-indigo-400 hover:text-indigo-600">✕</button>
          </div>
        )}

        {/* ① 采购订单（1688 维度） */}
        <StageCard no="①" title="采购订单" dim="1688" status={detail.orderStatus || "已成交"} statusTone="done">
          <div className="grid grid-cols-2 gap-x-6 gap-y-2.5 text-sm md:grid-cols-4">
            <div>
              <div className="text-[11px] text-gray-400">订单号</div>
              <div className="mt-0.5 font-mono text-[13px] font-medium text-gray-800">{detail.orderNo}</div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">下单时间</div>
              <div className="mt-0.5 text-[13px] text-gray-700">{fmtDate(detail.orderDate)}</div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">供应商</div>
              <div className="mt-0.5 text-[13px] text-gray-700" title={detail.supplier}>
                <span className="line-clamp-1">{detail.supplier || "—"}</span>
              </div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">采购金额</div>
              <div className="mt-0.5 text-[15px] font-semibold tabular-nums text-gray-900">{fmtMoney(detail.amount)}</div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">买家</div>
              <div className="mt-0.5 text-[13px] text-gray-700">{detail.buyer || "—"}</div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">商品标题</div>
              <div className="mt-0.5 text-[13px] text-gray-700" title={detail.title}>
                <span className="line-clamp-1">{detail.title || "—"}</span>
              </div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">数据来源</div>
              <div className="mt-0.5 text-[13px] text-gray-700">
                {detail.source === "file" ? "1688 订单导出文件" : "采购工作流"}
              </div>
            </div>
            <div>
              <div className="text-[11px] text-gray-400">订单状态</div>
              <div className="mt-0.5 text-[13px] text-indigo-600">{detail.orderStatus || "已成交"}</div>
            </div>
          </div>
        </StageCard>

        {/* ② 实际采购内容 / SKU（采购中心维度） */}
        <StageCard
          no="②"
          title="实际采购内容 / SKU"
          dim="purchase"
          status={
            detail.allocations.length > 0
              ? detail.purchaseContentComplete
                ? `已细化 ${detail.allocations.length} 个 SKU`
                : "细化中"
              : "待细化"
          }
          statusTone={detail.purchaseContentComplete && detail.allocations.length > 0 ? "done" : "todo"}
        >
          {detail.allocations.length > 0 ? (
            <div className="space-y-1.5">
              {detail.allocations.map((a) => (
                <div key={a.id} className="flex items-center justify-between gap-3 rounded-lg bg-violet-50/60 px-3 py-2">
                  <div className="min-w-0 text-sm">
                    <span className="font-medium text-gray-800">{a.goodsName || a.skuCode || "—"}</span>
                    {a.skuCode && (
                      <span className="ml-2 rounded bg-white px-1.5 py-px font-mono text-[11px] text-violet-600">
                        {a.skuCode}
                      </span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-3 text-sm">
                    <span className="tabular-nums text-gray-500">
                      {a.quantity ?? "—"} × {fmtMoney(a.unitPrice)}
                    </span>
                    <span className="font-semibold tabular-nums text-gray-800">{fmtMoney(a.amount)}</span>
                  </div>
                </div>
              ))}
              {detail.unallocatedAmount !== null && Math.abs(detail.unallocatedAmount) > 0.009 && (
                <div className="flex items-center justify-between rounded-lg bg-amber-50 px-3 py-1.5 text-[11px] text-amber-700">
                  <span>
                    {detail.unallocatedAmount > 0 ? "付款金额中尚未分配到 SKU" : "SKU 分配金额超出付款金额"}
                  </span>
                  <span className="font-semibold tabular-nums">{fmtMoney(Math.abs(detail.unallocatedAmount))}</span>
                </div>
              )}
            </div>
          ) : (
            <EmptyStage
              text="尚未细化实际采购内容；在采购中心把该 1688 订单拆成具体 SKU 后计入②"
              href="/purchase"
              act="去采购中心细化"
            />
          )}
        </StageCard>

        {/* ③ 吉客云采购单（吉客云维度） */}
        <StageCard
          no="③"
          title="吉客云采购单"
          dim="jackyun"
          status={detail.purchaseOrders.length > 0 ? `已生成 ${detail.purchaseOrders.length} 张` : "未生成"}
          statusTone={detail.purchaseOrders.length > 0 ? "done" : "todo"}
        >
          {detail.purchaseOrders.length > 0 ? (
            <div className="space-y-1.5">
              {detail.purchaseOrders.map((po) => (
                <div key={po.id} className="flex items-center justify-between gap-3 rounded-lg bg-teal-50/60 px-3 py-2">
                  <div className="min-w-0 text-sm">
                    <span className="font-medium text-gray-800">{po.purchNo}</span>
                    {po.supplierName && (
                      <span className="ml-2 truncate text-[11px] text-gray-400">{po.supplierName}</span>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2.5">
                    <span className="text-sm font-semibold tabular-nums text-gray-700">{fmtMoney(po.amount)}</span>
                    {po.status && (
                      <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-teal-600">{po.status}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStage
              text="尚未关联吉客云采购单；导入吉客云采购单后可自动匹配（关联后计入③）"
              href="/jackyun-import"
              act="导入吉客云采购单"
            />
          )}
        </StageCard>

        {/* ④ 入库单（吉客云维度） */}
        <StageCard
          no="④"
          title="吉客云入库"
          dim="jackyun"
          status={detail.inbound.length > 0 ? `已入库 ${detail.inbound.length} 单` : "未入库"}
          statusTone={detail.inbound.length > 0 ? "done" : sugInbound.length > 0 ? "warn" : "todo"}
        >
          {detail.inbound.length > 0 ? (
            <div className="space-y-1.5">
              {detail.inbound.map((doc) => (
                <div key={doc.goodsdocNo} className="flex items-center justify-between gap-3 rounded-lg bg-teal-50/60 px-3 py-2">
                  <div className="text-sm">
                    <span className="font-medium text-gray-800">{doc.goodsdocNo}</span>
                    <span className="ml-2 text-[11px] text-gray-400">{fmtDate(doc.date)}</span>
                  </div>
                  {doc.warehouseName && (
                    <span className="rounded-full bg-white px-2 py-0.5 text-[11px] text-teal-600">{doc.warehouseName}</span>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <EmptyStage
              text="尚未关联入库单；导入吉客云入库报表后可自动匹配"
              href="/jackyun-import"
              act="上传入库报表"
            />
          )}
          <SuggestionList items={sugInbound} busy={busy} onConfirm={confirmSuggestion} onReject={rejectSuggestion} />
        </StageCard>

        {/* ⑤ 发票（税务维度） */}
        <StageCard
          no="⑤"
          title="供应商发票"
          dim="tax"
          status={detail.invoice.length > 0 ? `已收票 ${detail.invoice.length} 张` : "未收票"}
          statusTone={detail.invoice.length > 0 ? "done" : sugInvoice.length > 0 ? "warn" : "todo"}
        >
          {detail.invoice.length > 0 ? (
            <div className="space-y-1.5">
              {detail.invoice.map((inv) => (
                <div key={inv.invoiceId} className="rounded-lg bg-amber-50/60 px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="text-sm">
                      <span className="font-medium text-gray-800">{inv.invoiceNo}</span>
                      <span className="ml-2 text-[11px] text-gray-400">{fmtDate(inv.issueDate)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold tabular-nums text-gray-700">{fmtMoney(inv.amount)}</span>
                      <button
                        onClick={() => toggleVerify(inv)}
                        disabled={busy}
                        title={inv.verified ? "点击取消认证" : "点击标记已认证（本月）"}
                        className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                          inv.verified
                            ? "bg-emerald-600 text-white hover:bg-emerald-700"
                            : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                        }`}
                      >
                        {inv.verified ? `已认证 ${inv.verifiedMonth}` : "标记已认证"}
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStage
              text="尚未关联发票；导入发票清单后可自动匹配（勾选抵扣后计入⑦）"
              href="/tax-invoices"
              act="上传发票清单"
            />
          )}
          <SuggestionList items={sugInvoice} busy={busy} onConfirm={confirmSuggestion} onReject={rejectSuggestion} />
        </StageCard>

        {/* ⑥ 结算 / 付款（吉客云维度） */}
        <StageCard
          no="⑥"
          title="结算 / 付款"
          dim="jackyun"
          status={
            paidSettle.length > 0
              ? `已付款 ${paidSettle.length} 笔`
              : unpaidSettle.length > 0
              ? "有结算单未付款"
              : "未付款"
          }
          statusTone={paidSettle.length > 0 ? "done" : detail.settlement.length > 0 || sugSettle.length > 0 ? "warn" : "todo"}
        >
          {detail.settlement.length > 0 ? (
            <div className="space-y-1.5">
              {detail.settlement.map((st) => (
                <div
                  key={st.settlementNo}
                  className={`flex items-center justify-between gap-3 rounded-lg px-3 py-2 ${
                    st.paid ? "bg-emerald-50/70" : "bg-amber-50/70"
                  }`}
                >
                  <div className="text-sm">
                    <span className="font-medium text-gray-800">{st.settlementNo}</span>
                    <span className="ml-2 text-[11px] text-gray-400">{fmtDate(st.date)}</span>
                  </div>
                  <div className="flex items-center gap-2.5">
                    <span className="text-sm font-semibold tabular-nums text-gray-700">
                      {fmtMoney(st.amount ?? st.paidAmount)}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                        st.paid ? "bg-emerald-100 text-emerald-600" : "bg-amber-100 text-amber-600"
                      }`}
                    >
                      {st.paid ? "已付款" : "未付款"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyStage
              text="尚未关联结算单；导入吉客云结算报表后可自动匹配"
              href="/jackyun-import"
              act="上传结算报表"
            />
          )}
          <SuggestionList items={sugSettle} busy={busy} onConfirm={confirmSuggestion} onReject={rejectSuggestion} />
        </StageCard>

        {/* ⑦ 税务认证（税务维度） */}
        <StageCard
          no="⑦"
          title="税务认证（勾选抵扣）"
          dim="tax"
          status={detail.verified ? `已认证 ${detail.invoice[0]?.verifiedMonth || ""}`.trim() : "未认证"}
          statusTone={detail.verified ? "done" : detail.invoice.length > 0 ? "warn" : "todo"}
        >
          {detail.invoice.length === 0 ? (
            <EmptyStage
              text="先在⑤关联供应商发票，收票后在此确认勾选认证月份"
              href="/tax-invoices"
              act="查看发票清单"
            />
          ) : (
            <div className="space-y-1.5">
              {detail.invoice.map((inv) => (
                <div
                  key={inv.invoiceId}
                  className="flex items-center justify-between gap-3 rounded-lg px-3 py-2"
                  style={{ backgroundColor: inv.verified ? "rgba(16,185,129,0.08)" : "rgba(249,250,251,1)" }}
                >
                  <div className="text-sm text-gray-700">
                    <span className="font-medium">{inv.invoiceNo}</span>
                    <span className="ml-2 text-[11px] text-gray-400">{fmtMoney(inv.amount)}</span>
                  </div>
                  <button
                    onClick={() => toggleVerify(inv)}
                    disabled={busy}
                    className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                      inv.verified
                        ? "bg-emerald-600 text-white hover:bg-emerald-700"
                        : "bg-gray-900 text-white hover:bg-gray-700"
                    }`}
                  >
                    {inv.verified ? `已认证 ${inv.verifiedMonth}（点击取消）` : "勾选认证（本月）"}
                  </button>
                </div>
              ))}
              <p className="pt-1 text-[11px] text-gray-400">
                认证月份默认取当前月，取消认证可重新勾选；已认证发票将计入完成度⑦。
              </p>
            </div>
          )}
        </StageCard>
      </div>
    </div>
  );
}

export default function ChainDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center text-sm text-gray-400">
          <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-indigo-500" />
          加载中…
        </div>
      }
    >
      <DetailContent />
    </Suspense>
  );
}
