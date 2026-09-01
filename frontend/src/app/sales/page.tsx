import PhasePlaceholder from "@/components/phase-placeholder";

export default function SalesPage() {
  return (
    <PhasePlaceholder
      phase={2}
      title="销售"
      description="销售趋势 / 平台店铺排行 / SKU 排行 / 订单列表，数据来自吉客云 oms.trade.fullinfoget 等已订阅接口的本地同步副本。"
      points={["订单下钻：平台 → 店铺 → SPU → SKU → 订单", "售后与退款率", "页面只查本地库，不触发吉客云全量查询"]}
    />
  );
}
