import PhasePlaceholder from "@/components/phase-placeholder";

export default function PurchasePage() {
  return (
    <PhasePlaceholder
      phase={3}
      title="采购"
      description="1688 定制订单 → 待完善采购 → 从吉客云选 SKU 分配 → 金额平衡校验 → 关联吉客云采购单 → 入库 → 发票跟踪。"
      points={["1688 OAuth 连接入口（未配置时明确显示等待配置）", "多 SKU 分配 + 附加费用，未分配≠0 禁止确认", "采购状态与发票状态分离，一单多票 / 一票多单"]}
    />
  );
}
