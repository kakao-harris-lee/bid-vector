import { useNavigate } from "react-router-dom";
import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { MiniBar } from "@/features/dashboard/components";
import { formatPercent } from "@/shared/lib";
import type { DecisionStatus } from "@/shared/types/decisions";
import type {
  OperationsKpiConversion,
  OperationsKpiManualOverride,
  OperationsKpiMissedOpportunities,
  OperationsKpiMissedOpportunityItem,
  OperationsKpiPredictionAccuracy,
  OperationsKpiResponse
} from "@/shared/types/operations";

const DASH = "—";

const ACTION_LABEL: Record<string, string> = {
  bid_now: "투찰",
  review: "검토",
  skip: "보류"
};

const ACTION_TONE: Record<string, "healthy" | "watch" | "muted"> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

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
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            <ManualOverrideCard data={data.manual_override} />
            <ConversionCard data={data.conversion} />
            <PredictionAccuracyCard data={data.prediction_accuracy} />
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
  const navigate = useNavigate();
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
          <Badge tone={ACTION_TONE[item.initial_action] ?? "muted"}>
            {ACTION_LABEL[item.initial_action] ?? item.initial_action}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-1 text-[var(--color-muted)]">
          <span>
            {deadlineLabel(item.deadline)} · 우선순위 {formatPercent(item.priority_score)}
          </span>
          <span className="tabular-nums">{statusLabel(item.decision_status)}</span>
        </div>
      </button>
    </li>
  );
}

const STATUS_LABEL: Record<string, string> = {
  planned: "예정",
  reviewing: "검토 중",
  submitted: "제출",
  skipped: "보류"
};

function statusLabel(status: string): string {
  return STATUS_LABEL[status as DecisionStatus] ?? status;
}
