import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { MiniBar } from "@/features/dashboard/components";
import { formatPercent } from "@/shared/lib";
import { useCrossAppNavigate } from "@/shared/crossAppNav";
import {
  ACTION_LABEL,
  ACTION_TONE,
  labelDecisionStatus
} from "@/shared/constants/decisionLabels";
import type { DecisionAction } from "@/shared/types/decisions";
import type {
  OperationsKpiConversion,
  OperationsKpiManualOverride,
  OperationsKpiMissedOpportunities,
  OperationsKpiMissedOpportunityItem,
  OperationsKpiPredictionAccuracy,
  OperationsKpiRecommendationFeedback,
  OperationsKpiResponse,
  OperationsKpiReviewTime,
  OperationsKpiSettlementCoverage
} from "@/shared/types/operations";

const DASH = "—";

/** 0~1 비율을 % 문자열로. null/undefined면 "데이터 없음" 대시. */
function rateOrDash(value?: number | null): string {
  if (value === null || value === undefined) return DASH;
  return formatPercent(value);
}

/** 정수 카운트를 천단위 콤마로. */
function formatCount(value: number): string {
  return value.toLocaleString("ko-KR");
}

function deadlineLabel(deadline?: string | null): string {
  if (!deadline) return "마감 미정";
  const formatted = new Intl.DateTimeFormat("ko-KR", {
    month: "short",
    day: "numeric"
  }).format(new Date(deadline));
  return `${formatted} 마감 지남`;
}

export interface OperationsKpiPanelProps {
  data?: OperationsKpiResponse;
  loading: boolean;
  error: Error | null;
}

export function OperationsKpiPanel({ data, loading, error }: OperationsKpiPanelProps) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          운영 KPI
          {data ? (
            <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
              (최근 {data.period_days}일)
            </span>
          ) : null}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <p className="text-xs text-[var(--color-muted)]">불러오는 중…</p>
        ) : null}
        {error ? (
          <p className="text-xs text-[var(--color-danger)]" role="alert">
            {error.message ?? "운영 KPI를 불러오지 못했습니다."}
          </p>
        ) : null}
        {data ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
            <ManualOverrideCard data={data.manual_override} />
            <ConversionCard data={data.conversion} />
            <PredictionAccuracyCard data={data.prediction_accuracy} />
            <ReviewTimeCard data={data.review_time} />
            <RecommendationFeedbackCard data={data.recommendation_feedback} />
            <SettlementCoverageCard data={data.settlement_coverage} />
            <MissedOpportunitiesCard data={data.missed_opportunities} />
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function KpiGroup({
  title,
  hint,
  children
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <article
      className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3"
      aria-label={title}
    >
      <header className="flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-[var(--color-fg)]">{title}</h4>
        {hint ? (
          <span className="text-[10px] text-[var(--color-muted)]" title={hint}>
            ⓘ
          </span>
        ) : null}
      </header>
      {children}
    </article>
  );
}

function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-[var(--color-muted)]">{label}</span>
      <strong className="text-[var(--color-fg)] tabular-nums">{value}</strong>
    </div>
  );
}

function RateBarRow({ label, value }: { label: string; value?: number | null }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 shrink-0 text-[var(--color-muted)]">{label}</span>
      <MiniBar value={value ?? 0} />
      <strong className="w-12 shrink-0 text-right text-[var(--color-fg)] tabular-nums">
        {rateOrDash(value)}
      </strong>
    </div>
  );
}

function ManualOverrideCard({ data }: { data: OperationsKpiManualOverride }) {
  return (
    <KpiGroup title="수동 수정" hint="낮을수록 추천을 그대로 수용 (좋음)">
      <StatRow label="수정률" value={rateOrDash(data.modification_rate)} />
      <p className="text-[11px] text-[var(--color-muted)]">
        결정 {formatCount(data.decision_count)}건 중 {formatCount(data.modified_count)}건 수정
      </p>
      {data.decision_count === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">집계할 결정이 없습니다.</p>
      ) : null}
    </KpiGroup>
  );
}

function ConversionCard({ data }: { data: OperationsKpiConversion }) {
  return (
    <KpiGroup title="투찰 전환율">
      <div className="flex flex-col gap-1.5">
        <RateBarRow label="전체" value={data.overall_submission_rate} />
        <RateBarRow label="투찰 → 제출" value={data.bid_now_submission_rate} />
        <RateBarRow label="검토 → 제출" value={data.review_submission_rate} />
      </div>
      <StatRow
        label="평균 제출 소요"
        value={
          data.average_hours_to_submit === null || data.average_hours_to_submit === undefined
            ? DASH
            : `평균 ${data.average_hours_to_submit.toFixed(1)}시간`
        }
      />
      <p className="text-[11px] text-[var(--color-muted)]">
        결정 {formatCount(data.decision_count)}건 · 제출 {formatCount(data.submitted_count)}건
      </p>
    </KpiGroup>
  );
}

function PredictionAccuracyCard({ data }: { data: OperationsKpiPredictionAccuracy }) {
  return (
    <KpiGroup title="예측 정확도" hint="최근 표본 기준 스냅샷입니다. 오차율은 낮을수록 좋음.">
      <StatRow label="예측 오차율" value={rateOrDash(data.average_prediction_error_rate)} />
      <StatRow
        label="추천 오차율"
        value={rateOrDash(data.average_recommendation_error_rate)}
      />
      <div className="grid grid-cols-2 gap-1 pt-1 text-[11px] text-[var(--color-muted)]">
        <span>
          예측 ±1% {formatCount(data.prediction_within_1_percent_count)}건 / ±3%{" "}
          {formatCount(data.prediction_within_3_percent_count)}건
        </span>
        <span>
          추천 ±1% {formatCount(data.recommendation_within_1_percent_count)}건 / ±3%{" "}
          {formatCount(data.recommendation_within_3_percent_count)}건
        </span>
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">
        결과 {formatCount(data.result_count)}건 · 예측표본{" "}
        {formatCount(data.prediction_sample_count)} · 추천표본{" "}
        {formatCount(data.recommendation_sample_count)}
      </p>
      {data.result_count === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">정확도 표본이 아직 없습니다.</p>
      ) : null}
    </KpiGroup>
  );
}

function MissedOpportunitiesCard({ data }: { data: OperationsKpiMissedOpportunities }) {
  // OperationsKpiPanel renders inside admin screens (OperationsScreen /
  // DecisionsScreen); the project-detail link targets the user app
  // (/dashboard/projects/...), so it crosses the bundle boundary.
  const navigate = useCrossAppNavigate();
  const items = data.items ?? [];
  return (
    <KpiGroup title="놓친 유효 공고" hint="추천(투찰/검토)했지만 마감까지 미처리된 공고">
      <div className="flex items-baseline gap-2">
        <strong className="text-2xl text-[var(--color-danger)] tabular-nums">
          {formatCount(data.missed_count)}
        </strong>
        <span className="text-xs text-[var(--color-muted)]">건</span>
      </div>
      {items.length === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">놓친 유효 공고가 없습니다.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {items.map((item) => (
            <MissedItem
              key={item.decision_record_id}
              item={item}
              onOpen={() => {
                if (item.project_id > 0) {
                  navigate(`/dashboard/projects/${item.project_id}`);
                }
              }}
            />
          ))}
        </ul>
      )}
    </KpiGroup>
  );
}

function MissedItem({
  item,
  onOpen
}: {
  item: OperationsKpiMissedOpportunityItem;
  onOpen: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-1.5 text-left text-[11px] hover:border-[var(--color-primary)]"
      >
        <div className="flex items-center justify-between gap-2">
          <span
            className="truncate font-medium text-[var(--color-fg)]"
            title={item.project_title}
          >
            {item.project_title}
          </span>
          <Badge tone={ACTION_TONE[item.initial_action as DecisionAction] ?? "muted"}>
            {ACTION_LABEL[item.initial_action as DecisionAction] ?? item.initial_action}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-1 text-[var(--color-muted)]">
          <span>
            {deadlineLabel(item.deadline)} · 우선순위 {formatPercent(item.priority_score)}
          </span>
          <span className="tabular-nums">{labelDecisionStatus(item.decision_status)}</span>
        </div>
      </button>
    </li>
  );
}

function ReviewTimeCard({ data }: { data: OperationsKpiReviewTime }) {
  const minutes = data.average_review_minutes;
  const minutesLabel =
    minutes === null || minutes === undefined ? DASH : `평균 ${minutes.toFixed(1)}분`;
  return (
    <KpiGroup
      title="검토 시간"
      hint="공고 첫 열람부터 결정까지 평균 소요 시간 (낮을수록 빠른 의사결정)"
    >
      <div className="flex items-baseline gap-2">
        <strong className="text-2xl text-[var(--color-fg)] tabular-nums">{minutesLabel}</strong>
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">표본 {formatCount(data.sample_count)}건</p>
      {data.sample_count === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">측정 표본이 아직 없습니다.</p>
      ) : null}
    </KpiGroup>
  );
}

function RecommendationFeedbackCard({
  data
}: {
  data: OperationsKpiRecommendationFeedback;
}) {
  return (
    <KpiGroup title="추천 유용성" hint="운영자 👍/👎 피드백 비율 (높을수록 좋음)">
      <div className="flex items-baseline gap-2">
        <strong className="text-2xl text-[var(--color-fg)] tabular-nums">
          {rateOrDash(data.review_value_rate)}
        </strong>
      </div>
      <div className="flex items-center justify-between text-[11px] text-[var(--color-muted)]">
        <span>👍 {formatCount(data.useful_count)}</span>
        <span>👎 {formatCount(data.not_useful_count)}</span>
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">
        피드백 {formatCount(data.feedback_count)}건
      </p>
      {data.feedback_count === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">아직 피드백이 없습니다.</p>
      ) : null}
    </KpiGroup>
  );
}

function SettlementCoverageCard({ data }: { data: OperationsKpiSettlementCoverage }) {
  const totalPaperBids = data?.total_paper_bids ?? 0;
  const settledCount = data?.settled_count ?? 0;
  const forwardPaperBids = data?.forward_paper_bids ?? 0;
  const forwardSettledCount = data?.forward_settled_count ?? 0;
  return (
    <KpiGroup title="정산 커버리지" hint="paper bid가 실제 개찰 결과로 검증된 비율">
      <div className="flex items-baseline gap-2">
        <strong className="text-2xl text-[var(--color-fg)] tabular-nums">
          {rateOrDash(data?.forward_coverage_rate)}
        </strong>
        <span className="text-xs text-[var(--color-muted)]">forward</span>
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">
        forward {formatCount(forwardPaperBids)}건 중 {formatCount(forwardSettledCount)}건 정산
      </p>
      <StatRow label="전체 정산률" value={rateOrDash(data?.coverage_rate)} />
      <p className="text-[11px] text-[var(--color-muted)]">
        전체 {formatCount(totalPaperBids)}건 중 {formatCount(settledCount)}건
      </p>
      {forwardPaperBids === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">forward 정산 대상이 아직 없습니다.</p>
      ) : null}
      {totalPaperBids === 0 ? (
        <p className="text-[11px] text-[var(--color-muted)]">집계할 paper bid가 없습니다.</p>
      ) : null}
    </KpiGroup>
  );
}
