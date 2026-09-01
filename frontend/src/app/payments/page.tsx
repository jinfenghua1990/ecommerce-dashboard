import PhasePlaceholder from "@/components/phase-placeholder";

export default function PaymentsPage() {
  return (
    <PhasePlaceholder
      phase={5}
      title="回款"
      description="平台应结算/应收 vs 浙江农信实际到账 → 综合平台/日期/金额/户名/摘要评分匹配 → 差异进入差异池。"
      points={["对方户名映射规则可配置（规则变更有日志）", "匹配置信度输出：high / medium / low", "期初允许不平，历史差异不篡改历史订单"]}
    />
  );
}
