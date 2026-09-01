"use client";

import { useEffect, useState } from "react";
import StatusBadge from "@/components/status-badge";
import { changePassword, getOverview, IntegrationStatus, openingApi, OpeningData, testJackyun } from "@/lib/api";

type TestState = { loading: boolean; result?: string; tools?: string[] };

const KIND_LABEL: Record<string, string> = {
  platform_receivable: "平台期初待回款",
  bank: "银行期初余额",
  sku_inventory: "SKU 期初库存",
  sku_cost: "SKU 期初成本",
  deposit: "保证金",
  frozen: "冻结款",
  other: "其他",
};

export default function SettingsPage() {
  const [items, setItems] = useState<IntegrationStatus[]>([]);
  const [jackyun, setJackyun] = useState<TestState>({ loading: false });
  const [opening, setOpening] = useState<OpeningData | null>(null);
  const [openMsg, setOpenMsg] = useState("");
  // 期初录入表单
  const [oKind, setOKind] = useState("platform_receivable");
  const [oRef, setORef] = useState("");
  const [oAmount, setOAmount] = useState("");
  const [oQty, setOQty] = useState("");
  const [oNote, setONote] = useState("");
  const [oDate, setODate] = useState("");

  // 修改密码
  const [pwOld, setPwOld] = useState("");
  const [pwNew, setPwNew] = useState("");
  const [pwMsg, setPwMsg] = useState("");

  async function submitPassword() {
    setPwMsg("");
    try {
      await changePassword(pwOld, pwNew);
      setPwMsg("密码已修改，下次登录请使用新密码");
      setPwOld(""); setPwNew("");
    } catch (e) {
      setPwMsg(`修改失败：${e instanceof Error ? e.message : String(e)}`);
    }
    setTimeout(() => setPwMsg(""), 4000);
  }

  async function load() {
    const data = await getOverview();
    setItems(data.integrations);
    openingApi.list().then(setOpening).catch(() => {});
  }
  useEffect(() => {
    load().catch(() => {});
  }, []);

  async function addOpening() {
    const body: Record<string, unknown> = { kind: oKind, ref: oRef, note: oNote };
    if (oAmount) body.amount = oAmount;
    if (oQty) body.quantity = oQty;
    if (oDate) body.as_of_date = oDate;
    try {
      await openingApi.upsert(body);
      setOpenMsg("期初已保存（写审计日志）");
      setORef(""); setOAmount(""); setOQty(""); setONote(""); setODate("");
      const d = await openingApi.list();
      setOpening(d);
    } catch (e) {
      setOpenMsg(`保存失败：${String(e)}`);
    }
    setTimeout(() => setOpenMsg(""), 3500);
  }

  async function runTest() {
    setJackyun({ loading: true });
    try {
      const res = await testJackyun();
      if (res.ok) {
        setJackyun({ loading: false, result: "连接成功", tools: res.tools });
      } else {
        setJackyun({ loading: false, result: `失败：${res.error}` });
      }
    } catch (e) {
      setJackyun({ loading: false, result: `请求异常：${String(e)}` });
    }
    load();
  }

  return (
    <div>
      <h1 className="text-xl font-semibold">设置 · 数据连接</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
        凭证只保存在服务器端（.env / 数据库加密），不回传前端。未配置的系统如实显示，不使用模拟数据伪装连接。
      </p>

      <div className="mt-6 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">账号安全 · 修改密码</div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <div className="text-xs text-gray-500">原密码</div>
            <input
              type="password"
              value={pwOld}
              onChange={(e) => setPwOld(e.target.value)}
              className="mt-1 block w-52 rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
            />
          </div>
          <div>
            <div className="text-xs text-gray-500">新密码（至少 8 位）</div>
            <input
              type="password"
              value={pwNew}
              onChange={(e) => setPwNew(e.target.value)}
              className="mt-1 block w-52 rounded-lg border border-gray-300 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
            />
          </div>
          <button
            onClick={submitPassword}
            disabled={!pwOld || pwNew.length < 8}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
          >
            修改密码
          </button>
        </div>
        {pwMsg && <div className="mt-2 text-xs text-gray-600">{pwMsg}</div>}
      </div>

      <div className="mt-6 grid max-w-3xl grid-cols-1 gap-3">
        {items.map((it) => (
          <div key={it.id} className="flex items-center justify-between rounded-xl border border-gray-200 bg-white p-4">
            <div>
              <div className="text-sm font-medium">{it.name}</div>
              <div className="mt-0.5 text-xs text-gray-400">
                {it.mode} · Phase {it.phase}
                {it.errorSummary ? ` · ${it.errorSummary.slice(0, 80)}` : ""}
              </div>
            </div>
            <StatusBadge status={it.status} />
          </div>
        ))}
      </div>

      <div className="mt-6 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">吉客云 MCP 连接测试</div>
        <p className="mt-1 text-xs text-gray-400">
          真实调用 MCP：initialize → tools/list，返回已订阅工具清单。失败原因会写入异常中心与同步日志。
        </p>
        <button
          onClick={runTest}
          disabled={jackyun.loading}
          className="mt-3 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {jackyun.loading ? "测试中…" : "立即测试连接"}
        </button>
        {jackyun.result && (
          <div className={`mt-3 text-sm ${jackyun.tools ? "text-emerald-700" : "text-red-600"}`}>
            {jackyun.result}
          </div>
        )}
        {jackyun.tools && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {jackyun.tools.map((t) => (
              <span key={t} className="rounded bg-gray-100 px-2 py-0.5 font-mono text-[11px] text-gray-600">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* 1688 OAuth（规格 7.2：未配置如实显示等待，不伪造已连接） */}
      <div className="mt-6 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">1688 采购授权</div>
        <p className="mt-1 text-xs text-gray-400">
          只读同步已发生的买家订单，不下单、不付款。需先在 1688 开放平台创建应用（AppKey/Secret + 回调地址），
          配置到 .env 后点击「连接 1688」跳官方授权。
        </p>
        <div className="mt-3 flex items-center gap-3">
          <button
            onClick={async () => {
              try {
                const res = await fetch("/api/v1/integrations/alibaba1688/auth-url", { cache: "no-store" });
                const d = await res.json();
                if (res.ok && d.authorizationUrl) {
                  window.location.href = d.authorizationUrl;
                } else {
                  setJackyun({ loading: false, result: `1688：${d.detail ?? "未配置"}` });
                }
              } catch (e) {
                setJackyun({ loading: false, result: `1688：${String(e)}` });
              }
            }}
            className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-indigo-600 ring-1 ring-indigo-200 hover:bg-indigo-50"
          >
            连接 1688
          </button>
          <span className="text-xs text-gray-400">回调地址示例：{typeof window !== "undefined" ? `${window.location.origin}/api/v1/integrations/alibaba1688/callback` : "…"}</span>
        </div>
      </div>

      <div className="mt-6 max-w-3xl rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-800">
        局域网信任模式：同一内网设备均可访问本平台。请勿在路由器做端口转发，勿将 18080 暴露公网；
        未来如需公网/跨网访问，必须先恢复认证、权限隔离与 TLS。
      </div>

      {/* 期初初始化（规格 11） */}
      <div className="mt-6 max-w-3xl rounded-xl border border-gray-200 bg-white p-4">
        <div className="text-sm font-medium">期初初始化（一次性向导）</div>
        <p className="mt-1 text-xs text-gray-400">
          允许不平：期初 + 本期发生 − 本期结算 = 期末；历史差异进入差异池，不篡改历史订单（规格 1.5 / 11）。
        </p>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <select value={oKind} onChange={(e) => setOKind(e.target.value)} className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm">
            {Object.entries(KIND_LABEL).map(([k, label]) => (
              <option key={k} value={k}>{label}</option>
            ))}
          </select>
          <input value={oRef} onChange={(e) => setORef(e.target.value)} placeholder="平台名 / 账户 / SKU" className="w-40 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" />
          <input value={oAmount} onChange={(e) => setOAmount(e.target.value)} placeholder="金额" className="w-28 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" />
          <input value={oQty} onChange={(e) => setOQty(e.target.value)} placeholder="数量(库存)" className="w-28 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" />
          <input value={oDate} onChange={(e) => setODate(e.target.value)} type="date" className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm" />
          <input value={oNote} onChange={(e) => setONote(e.target.value)} placeholder="备注" className="w-32 rounded-lg border border-gray-300 px-3 py-1.5 text-sm" />
          <button onClick={addOpening} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm text-white hover:bg-indigo-700">
            保存期初
          </button>
        </div>
        {openMsg && <div className="mt-2 text-sm text-emerald-700">{openMsg}</div>}

        {opening && opening.items.length > 0 && (
          <table className="mt-4 w-full text-left text-sm">
            <thead>
              <tr className="border-b border-gray-200 text-xs text-gray-400">
                <th className="py-2 pr-4 font-medium">类别</th>
                <th className="py-2 pr-4 font-medium">对象</th>
                <th className="py-2 pr-4 font-medium">金额</th>
                <th className="py-2 pr-4 font-medium">数量</th>
                <th className="py-2 font-medium">备注</th>
              </tr>
            </thead>
            <tbody>
              {opening.items.map((r) => (
                <tr key={r.id} className="border-b border-gray-100">
                  <td className="py-2 pr-4">{KIND_LABEL[r.kind] ?? r.kind}</td>
                  <td className="py-2 pr-4 text-gray-600">{r.ref || "—"}</td>
                  <td className="py-2 pr-4 tabular-nums">{r.amount !== null ? `¥${r.amount}` : "—"}</td>
                  <td className="py-2 pr-4 tabular-nums">{r.quantity ?? "—"}</td>
                  <td className="py-2 text-gray-400">{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {opening && (
          <div className="mt-4 flex flex-wrap gap-4 border-t border-gray-100 pt-3 text-xs text-gray-500">
            <span>差异池：<b className={Number(opening.summary.differencePool) !== 0 ? "text-amber-600" : ""}>
              {opening.summary.differencePool !== "0" ? `¥${opening.summary.differencePool}` : "0（平衡）"}
            </b></span>
            <span>调整次数：{opening.summary.adjustmentCount}</span>
            <span>已有成本 SKU：{opening.summary.skuWithCost}</span>
          </div>
        )}
      </div>
    </div>
  );
}
