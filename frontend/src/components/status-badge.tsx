const COLORS: Record<string, string> = {
  connected: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  available: "bg-sky-50 text-sky-700 ring-sky-200",
  untested: "bg-amber-50 text-amber-700 ring-amber-200",
  unconfigured: "bg-gray-100 text-gray-500 ring-gray-200",
  error: "bg-red-50 text-red-700 ring-red-200",
};

const LABELS: Record<string, string> = {
  connected: "已连接",
  available: "文件导入可用",
  untested: "已配置·未测试",
  unconfigured: "未配置",
  error: "错误",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        COLORS[status] ?? COLORS.unconfigured
      }`}
    >
      {LABELS[status] ?? status}
    </span>
  );
}
