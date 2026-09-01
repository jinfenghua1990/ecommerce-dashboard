import PhasePlaceholder from "@/components/phase-placeholder";

export default function AutomationPage() {
  return (
    <PhasePlaceholder
      phase={6}
      title="自动化"
      description="Celery Beat 定时任务：吉客云订单/售后 15 分钟、库存 30 分钟、商品每天、采购 60 分钟、1688 每天 1 次、月初完整校验。"
      points={["checkpoint / retry / backoff / rate limit / 幂等", "同步日志与 raw payload 存档", "频率后台可配置"]}
    />
  );
}
