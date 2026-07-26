import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import { LabeledStat } from "@/shared/components";
import type { DecisionFunnelResponse } from "@/shared/types/decisions";

export function FunnelOverview({
  funnel,
  loading,
  error
}: {
  funnel?: DecisionFunnelResponse;
  loading: boolean;
  error: Error | null;
}) {
  if (loading && !funnel) {
    return <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>;
  }
  if (error) {
    return (
      <p
        className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        role="alert"
      >
        {error.message ?? "결정 퍼널을 불러오지 못했습니다."}
      </p>
    );
  }
  if (!funnel) return null;

  const stages = [
    { key: "decisions", label: "결정", count: funnel.decision_count },
    {
      key: "active",
      label: "진행",
      count: funnel.active_pending_count
    },
    { key: "submitted", label: "제출", count: funnel.submitted_count },
    { key: "skipped", label: "보류", count: funnel.skipped_count }
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>퍼널 ({funnel.period_days}일)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={stages} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" allowDecimals={false} />
              <YAxis type="category" dataKey="label" width={60} />
              <Tooltip />
              <Bar dataKey="count" radius={[0, 6, 6, 0]}>
                {stages.map((stage, index) => (
                  <Cell
                    key={stage.key}
                    fill={
                      stage.key === "submitted"
                        ? "var(--color-success)"
                        : stage.key === "skipped"
                          ? "var(--color-muted)"
                          : stage.key === "active"
                            ? "var(--color-warn)"
                            : "var(--color-primary)"
                    }
                    fillOpacity={1 - index * 0.05}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
          <LabeledStat variant="metric" label="제출률" value={formatPercent(funnel.overall_submission_rate)} />
          <LabeledStat variant="metric" label="투찰 → 제출" value={formatPercent(funnel.bid_now_submission_rate)} />
          <LabeledStat variant="metric" label="검토 → 제출" value={formatPercent(funnel.review_submission_rate)} />
          <LabeledStat variant="metric"
            label="평균 처리시간"
            value={funnel.average_hours_to_submit ? `${funnel.average_hours_to_submit.toFixed(1)}h` : "-"}
          />
        </div>
      </CardContent>
    </Card>
  );
}
