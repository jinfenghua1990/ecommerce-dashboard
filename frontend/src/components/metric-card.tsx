export default function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1.5 text-xl font-semibold tabular-nums">{value}</div>
      {hint && <div className="mt-1 text-[11px] text-gray-400">{hint}</div>}
    </div>
  );
}
