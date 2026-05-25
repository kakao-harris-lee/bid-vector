import { useMemo, useState } from "react";
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
import { useShellContext } from "@/app/dashboardContext";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatCurrencyCompact, formatDateTime, formatPercent } from "@/shared/lib";
import { ApiError } from "@/shared/api";
import { logoutSession } from "@/app/layout/AuthGate";
import type {
  DecisionAction,
  DecisionFunnelBreakdownItem,
  DecisionFunnelResponse,
  DecisionStatus
} from "@/shared/types/decisions";
import {
  useDecisionFunnelQuery,
  useDecisionRecommendationsQuery,
  useUpdateDecisionStatusMutation
} from "./hooks";

const ACTION_LABEL: Record<DecisionAction, string> = {
  bid_now: "투찰",
  review: "검토",
  skip: "보류"
};

const ACTION_TONE: Record<DecisionAction, "healthy" | "watch" | "muted"> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

const STATUS_LABEL: Record<DecisionStatus, string> = {
  planned: "예정",
  reviewing: "검토 중",
  submitted: "제출",
  skipped: "보류"
};

const STATUSES: DecisionStatus[] = ["planned", "reviewing", "submitted", "skipped"];

export function DecisionsScreen() {
  const { session } = useShellContext();
  const [days, setDays] = useState(30);
  const [breakdownDimension, setBreakdownDimension] = useState<"category" | "workload" | "agency">(
    "category"
  );

  const funnel = useDecisionFunnelQuery(session, { days });
  const recs = useDecisionRecommendationsQuery(session, { days });
  const mutation = useUpdateDecisionStatusMutation(session);

  const breakdown = useMemo(() => {
    if (!funnel.data) return [] as DecisionFunnelBreakdownItem[];
    if (breakdownDimension === "category") return funnel.data.category_breakdown;
    if (breakdownDimension === "workload") return funnel.data.workload_source_breakdown;
    return funnel.data.agency_breakdown;
  }, [funnel.data, breakdownDimension]);

  const handleStatusChange = async (decisionRecordId: number, nextStatus: DecisionStatus) => {
    try {
      await mutation.mutateAsync({ decisionRecordId, decisionStatus: nextStatus });
      toastApi.success({ title: "상태 변경 완료", description: STATUS_LABEL[nextStatus] });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        logoutSession();
        return;
      }
      toastApi.danger({
        title: "상태 변경 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      });
    }
  };

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">결정 게이트웨이</h2>
        <label className="flex items-center gap-2 text-xs">
          기간
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            aria-label="기간 (일)"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            <option value={7}>7일</option>
            <option value={14}>14일</option>
            <option value={30}>30일</option>
            <option value={60}>60일</option>
            <option value={90}>90일</option>
          </select>
        </label>
      </header>

      <FunnelOverview funnel={funnel.data} loading={funnel.isPending} error={funnel.error} />

      <Card>
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>세그먼트 breakdown</CardTitle>
          <div role="tablist" aria-label="세그먼트 차원" className="flex gap-1 text-xs">
            {(["category", "workload", "agency"] as const).map((dim) => (
              <button
                key={dim}
                type="button"
                role="tab"
                aria-selected={breakdownDimension === dim}
                onClick={() => setBreakdownDimension(dim)}
                className={`rounded-md border px-2 py-1 ${
                  breakdownDimension === dim
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
                    : "border-[var(--color-border)] text-[var(--color-muted)]"
                }`}
              >
                {dim === "category" ? "카테고리" : dim === "workload" ? "워크로드" : "기관"}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent>
          <BreakdownChart items={breakdown} />
        </CardContent>
      </Card>

      <RecommendationsList
        recommendations={recs.data?.recommendations ?? []}
        headline={recs.data?.headline}
        loading={recs.isPending}
        error={recs.error}
      />

      <Card>
        <CardHeader>
          <CardTitle>
            최근 결정
            {funnel.data ? (
              <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
                (제출 {funnel.data.submitted_count}건)
              </span>
            ) : null}
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-xs">
          {funnel.data && funnel.data.recent_submissions.length === 0 ? (
            <p className="text-[var(--color-muted)]">최근 제출 결정이 없습니다.</p>
          ) : null}
          {funnel.data?.recent_submissions.map((item) => (
            <article
              key={item.decision_record_id}
              className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium text-[var(--color-fg)]" title={item.project_title}>
                  {item.project_title}
                </span>
                <div className="flex items-center gap-1">
                  <Badge tone={ACTION_TONE[item.action]}>{ACTION_LABEL[item.action]}</Badge>
                  <Badge tone="info">{STATUS_LABEL[item.decision_status]}</Badge>
                </div>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-1 text-[var(--color-muted)]">
                <span>
                  {item.category ?? "-"} · 우선순위 {formatPercent(item.priority_score)} · {formatDateTime(item.updated_at)}
                </span>
                <span className="tabular-nums">{formatCurrencyCompact(item.recommended_amount)}</span>
              </div>
              <div className="flex flex-wrap gap-1 pt-1">
                {STATUSES.map((status) => {
                  const active = status === item.decision_status;
                  return (
                    <Button
                      key={status}
                      type="button"
                      variant={active ? "primary" : "outline"}
                      size="sm"
                      disabled={active || mutation.isPending}
                      onClick={() => handleStatusChange(item.decision_record_id, status)}
                    >
                      {STATUS_LABEL[status]}
                    </Button>
                  );
                })}
              </div>
            </article>
          ))}
        </CardContent>
      </Card>
    </section>
  );
}

function FunnelOverview({
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
          <Metric label="제출률" value={formatPercent(funnel.overall_submission_rate)} />
          <Metric label="투찰 → 제출" value={formatPercent(funnel.bid_now_submission_rate)} />
          <Metric label="검토 → 제출" value={formatPercent(funnel.review_submission_rate)} />
          <Metric
            label="평균 처리시간"
            value={funnel.average_hours_to_submit ? `${funnel.average_hours_to_submit.toFixed(1)}h` : "-"}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function BreakdownChart({ items }: { items: DecisionFunnelBreakdownItem[] }) {
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

function RecommendationsList({
  recommendations,
  headline,
  loading,
  error
}: {
  recommendations: import("@/shared/types/decisions").DecisionRecommendationItem[];
  headline?: string;
  loading: boolean;
  error: Error | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          추천
          {headline ? (
            <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">{headline}</span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {loading && recommendations.length === 0 ? (
          <p className="text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {error ? (
          <p className="text-[var(--color-danger)]" role="alert">
            {error.message ?? "추천을 불러오지 못했습니다."}
          </p>
        ) : null}
        {!loading && recommendations.length === 0 ? (
          <p className="text-[var(--color-muted)]">현재 권장 액션이 없습니다.</p>
        ) : null}
        {recommendations.map((item) => (
          <article
            key={item.key}
            className="flex flex-col gap-1 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
          >
            <div className="flex items-center justify-between gap-2">
              <strong className="text-[var(--color-fg)]">{item.title}</strong>
              <Badge tone={item.severity === "action" ? "watch" : item.severity === "info" ? "info" : "healthy"}>
                {item.severity}
              </Badge>
            </div>
            <p className="text-[var(--color-muted)]">{item.summary}</p>
            {item.suggested_adjustment ? (
              <p className="text-[var(--color-fg)]">제안: {item.suggested_adjustment}</p>
            ) : null}
          </article>
        ))}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[var(--color-muted)]">{label}</span>
      <strong className="text-sm text-[var(--color-fg)] tabular-nums">{value}</strong>
    </div>
  );
}
