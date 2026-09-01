import PhasePlaceholder from "@/components/phase-placeholder";

export default function ProfitPage() {
  return (
    <PhasePlaceholder
      phase={5}
      title="利润"
      description="商品毛利 = 净销售 − 商品成本；贡献利润仅在平台费用/推广/物流数据可靠后开启。"
      points={["成本优先级：实际采购结算 > 采购订单 > SKU 默认 > 暂估", "成本缺失明确标识，不显示假装精确的最终利润", "利润结果保存 snapshot（formula_version + data_version）"]}
    />
  );
}
