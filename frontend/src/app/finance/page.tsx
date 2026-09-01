import PhasePlaceholder from "@/components/phase-placeholder";

export default function FinancePage() {
  return (
    <PhasePlaceholder
      phase={4}
      title="财务资料"
      description="给财务的是原始资料：收集 → 完整性检查 → 按月归档 → 原样 ZIP → SMTP 发送 → 留版本。"
      points={["浙江农信 Excel/PDF 上传，SHA256 + 版本化，同名不覆盖", "缺资料不发送（INCOMPLETE → 建异常）", "已发送 V1 不可静默覆盖，修正生成 V2/V3"]}
    />
  );
}
