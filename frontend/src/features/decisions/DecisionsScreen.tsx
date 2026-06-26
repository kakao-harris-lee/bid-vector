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
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatCurrencyCompact, formatDateTime, formatPercent } from "@/shared/lib";
import { useCrossAppNavigate } from "@/shared/crossAppNav";
import { InlineActionButtons, ReasonIndicators } from "@/features/dashboard/components";
import { useApplyBidDecisionActionMutation } from "@/features/dashboard/hooks";
import type { BidDecisionActionType } from "@/shared/api";
import type {
  DecisionFunnelBreakdownItem,
  DecisionFunnelRecentSubmissionItem,
  DecisionFunnelResponse
} from "@/shared/types/decisions";
import { LabeledStat } from "@/shared/components";
import {
  ACTION_LABEL,
  ACTION_TONE,
  DECISION_STATUS_LABEL
} from "@/shared/constants/decisionLabels";
import {
  useDecisionFunnelQuery,
  useDecisionRecommendationsQuery,
  useOperationsKpiQuery
} from "./hooks";
import { OperationsKpiPanel } from "./OperationsKpiPanel";

export function DecisionsScreen() {
  const { session, activeOperator } = useShellContext();
  // Admin screen linking into the user app (/dashboard/...) crosses the bundle
  // boundary, so use the cross-app helper (full-page nav in standalone admin).
  const navigate = useCrossAppNavigate();
  const [days, setDays] = useState(30);
  const [breakdownDimension, setBreakdownDimension] = useState<"category" | "workload" | "agency">(
    "category"
  );
  // Track per-row pending action so each "최근 결정" article gets its own
  // spinner. The shared dashboard mutation hook handles toasts + cache
  // invalidation; we only manage which row is currently in-flight.
  const [pendingActionByRecord, setPendingActionByRecord] = useState<
    Record<number, BidDecisionActionType | null>
  >({});
  const [reasonDraftByRecord, setReasonDraftByRecord] = useState<Record<number, string>>({});

  const funnel = useDecisionFunnelQuery(session, { days });
  const recs = useDecisionRecommendationsQuery(session, { days });
  const operationsKpi = useOperationsKpiQuery(
    session,
    { days, missedLimit: 10 },
    activeOperator.activeOperatorId
  );
  const actionMutation = useApplyBidDecisionActionMutation(session);

  const breakdown = useMemo(() => {
    if (!funnel.data) return [] as DecisionFunnelBreakdownItem[];
    if (breakdownDimension === "category") return funnel.data.category_breakdown;
    if (breakdownDimension === "workload") return funnel.data.workload_source_breakdown;
    return funnel.data.agency_breakdown;
  }, [funnel.data, breakdownDimension]);

  const handleAction = (decisionRecordId: number, action: BidDecisionActionType) => {
    if (!reasonDraftByRecord[decisionRecordId]?.trim()) return;
    setPendingActionByRecord((prev) => ({ ...prev, [decisionRecordId]: action }));
    actionMutation.mutate(
      {
        decisionRecordId,
        action,
        operatorId: activeOperator.activeOperatorId
      },
      {
        onSettled: () => {
          setPendingActionByRecord((prev) => {
            const next = { ...prev };
            next[decisionRecordId] = null;
            return next;
          });
        }
      }
    );
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

      <OperationsKpiPanel
        data={operationsKpi.data}
        loading={operationsKpi.isPending}
        error={operationsKpi.error}
      />

      <DecisionReviewFlowCard />

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
          {funnel.data?.recent_submissions.map((item) => {
            const reasonDraft = reasonDraftByRecord[item.decision_record_id] ?? "";
            const hasReasonDraft = reasonDraft.trim().length > 0;
            return (
              <article
                key={item.decision_record_id}
                className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate font-medium text-[var(--color-fg)]" title={item.project_title}>
                      {item.project_title}
                    </span>
                    <ReasonIndicators strengths={item.strengths} riskFlags={item.risk_flags} />
                  </span>
                  <div className="flex items-center gap-1">
                    <Badge tone={ACTION_TONE[item.action]}>{ACTION_LABEL[item.action]}</Badge>
                    <Badge tone="info">{DECISION_STATUS_LABEL[item.decision_status]}</Badge>
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-1 text-[var(--color-muted)]">
                  <span>
                    {item.category ?? "-"} · 우선순위 {formatPercent(item.priority_score)} · {formatDateTime(item.updated_at)}
                  </span>
                  <span className="tabular-nums">{formatCurrencyCompact(item.recommended_amount)}</span>
                </div>
                <DecisionEvidenceChecklist item={item} />
                <DecisionReasonDraft
                  recordId={item.decision_record_id}
                  value={reasonDraft}
                  onChange={(next) =>
                    setReasonDraftByRecord((prev) => ({
                      ...prev,
                      [item.decision_record_id]: next
                    }))
                  }
                />
                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <InlineActionButtons
                    decisionStatus={item.decision_status}
                    pendingAction={pendingActionByRecord[item.decision_record_id] ?? null}
                    disabled={!hasReasonDraft}
                    onAction={(action) => handleAction(item.decision_record_id, action)}
                  />
                  <span className="text-[11px] text-[var(--color-muted)]">
                    {hasReasonDraft ? "선택 사유 작성됨" : "선택 사유 작성 후 전환 가능"}
                  </span>
                  <button
                    type="button"
                    onClick={() => navigate(`/dashboard/decisions/${item.decision_record_id}/summary`)}
                    className="ml-auto rounded-md border border-[var(--color-border)] px-2 py-1 text-[var(--color-fg)] hover:bg-[var(--color-secondary)]"
                  >
                    투찰 요약
                  </button>
                </div>
              </article>
            );
          })}
        </CardContent>
      </Card>
    </section>
  );
}

function DecisionReviewFlowCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>투찰 전 확인 흐름</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3 text-xs md:grid-cols-3">
        <ReviewFlowStep
          label="1. 목록에서 1차 확인"
          detail="추천가, 우선순위, 강점과 리스크 신호를 먼저 확인합니다."
        />
        <ReviewFlowStep
          label="2. 요약에서 가격 근거 확인"
          detail="예측 가격대, 하한율 참고값, 분야 통계를 함께 보고 가격 신호를 검토합니다."
        />
        <ReviewFlowStep
          label="3. 선택 사유 기록"
          detail="투찰·검토·보류 전환 전 내부 판단 사유를 남겨 나중에 과거 오차와 비교합니다."
        />
      </CardContent>
    </Card>
  );
}

function ReviewFlowStep({ label, detail }: { label: string; detail: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3">
      <p className="font-medium text-[var(--color-fg)]">{label}</p>
      <p className="mt-1 text-[var(--color-muted)]">{detail}</p>
    </div>
  );
}

function DecisionEvidenceChecklist({ item }: { item: DecisionFunnelRecentSubmissionItem }) {
  const strengths = item.strengths ?? [];
  const risks = item.risk_flags ?? [];
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-secondary)] p-3"
      aria-label={`${item.project_title} 추천 근거 확인`}
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-[var(--color-fg)]">추천가 근거 확인</span>
        <Badge tone={risks.length > 0 ? "watch" : "healthy"}>
          리스크 {risks.length.toLocaleString("ko-KR")}개
        </Badge>
      </div>
      <dl className="grid gap-2 sm:grid-cols-3">
        <LabeledStat variant="evidence" label="추천가" value={formatCurrencyCompact(item.recommended_amount)} />
        <LabeledStat variant="evidence" label="우선순위" value={formatPercent(item.priority_score)} />
        <LabeledStat
          variant="evidence"
          label="근거 신호"
          value={`강점 ${strengths.length.toLocaleString("ko-KR")}개 / 리스크 ${risks.length.toLocaleString(
            "ko-KR"
          )}개`}
        />
      </dl>
      {(strengths.length > 0 || risks.length > 0) ? (
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          <SignalList title="강점" items={strengths} empty="기록된 강점 신호 없음" />
          <SignalList title="리스크" items={risks} empty="기록된 리스크 신호 없음" />
        </div>
      ) : null}
      <p className="mt-2 text-[11px] leading-tight text-[var(--color-muted)]">
        투찰 요약에서 예측 가격대와 하한율 참고값을 확인하고, 운영 KPI의 과거 추천 오차와
        함께 검토하세요.
      </p>
    </div>
  );
}

function SignalList({
  title,
  items,
  empty
}: {
  title: string;
  items: string[];
  empty: string;
}) {
  return (
    <div>
      <p className="mb-1 text-[11px] font-medium text-[var(--color-muted)]">{title}</p>
      {items.length > 0 ? (
        <ul className="flex flex-wrap gap-1" aria-label={`${title} 신호`}>
          {items.map((signal) => (
            <li
              key={signal}
              className="rounded-full bg-[var(--color-card)] px-2 py-0.5 text-[11px] text-[var(--color-fg)]"
            >
              {signal}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-[11px] text-[var(--color-muted)]">{empty}</p>
      )}
    </div>
  );
}

function DecisionReasonDraft({
  recordId,
  value,
  onChange
}: {
  recordId: number;
  value: string;
  onChange: (next: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium text-[var(--color-muted)]">
        선택 사유 메모 (임시)
      </span>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={`결정 ${recordId} 선택 사유 메모`}
        className="min-h-16 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2 text-xs text-[var(--color-fg)]"
      />
      <span className="text-[11px] text-[var(--color-muted)]">
        현재는 저장되지 않는 검토 초안이며, 전환 버튼 활성화에만 사용됩니다.
      </span>
    </label>
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

