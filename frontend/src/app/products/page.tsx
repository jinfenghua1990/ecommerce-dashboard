import PhasePlaceholder from "@/components/phase-placeholder";

export default function ProductsPage() {
  return (
    <PhasePlaceholder
      phase={2}
      title="商品与库存"
      description="商品库唯一来源为吉客云 erp.storage.goodslist，本平台不维护独立正式 SKU。"
      points={["商品 / SKU 检索（名称、SKU、条码）", "库存快照（erp.stockquantity.get，30 分钟增量）", "采购选择器数据源"]}
    />
  );
}
