"use client";

import { GoodsInventoryPanel } from "../inventory-panel";

export default function InventoryGoodsPage() {
  return <div>
    <h1 className="text-xl font-semibold">库存 · 正品</h1>
    <p className="mt-1 text-sm text-gray-400">正品库存由吉客云管理，本系统只读展示最新快照；仓库分布与零库存货品一目了然。</p>
    <GoodsInventoryPanel />
  </div>;
}
