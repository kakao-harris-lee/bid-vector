import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import type { DecisionFunnelBreakdownItem } from "@/shared/types/decisions";

export function BreakdownChart({ items }: { items: DecisionFunnelBreakdownItem[] }) {
  if (items.length === 0) {
    return <p className="text-xs text-[var(--color-muted)]">표시할 데이터가 없습니다.</p>;
  }
  const data = items.map((item) => ({
    name: item.label ?? item.key ?? "기타",
    decisions: item.decision_count,
    submitted: item.submitted_count
  }));
  return (
    <div className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="name" />
          <YAxis allowDecimals={false} />
          <Tooltip />
          <Bar dataKey="decisions" fill="var(--color-primary)" name="결정" />
          <Bar dataKey="submitted" fill="var(--color-success)" name="제출" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
