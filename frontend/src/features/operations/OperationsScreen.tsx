import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { fetchG2EvidenceSummary, fetchOperationsDashboard, queryKeys } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatDateTime, formatPercent } from "@/shared/lib";
import { OperationsKpiPanel, useOperationsKpiQuery } from "@/features/decisions";
import type {
  G2EvidenceSectionSummary,
  G2EvidenceStatus,
  G2EvidenceSummaryResponse,
  OperationsCardStatus,
  OperationsDashboardCard,
  OperationsDashboardResponse,
  SmokeTestPhaseRate
} from "@/shared/types/operations";

const REFRESH_INTERVAL_MS = 30_000;
const G2_EVIDENCE_WINDOW_DAYS = 7;
const KPI_DAYS_OPTIONS = [7, 30, 90] as const;
type KpiDays = (typeof KPI_DAYS_OPTIONS)[number];

const TONE: Record<OperationsCardStatus, "info" | "healthy" | "watch" | "critical"> = {
  info: "info",
  healthy: "healthy",
  watch: "watch",
  critical: "critical"
};

// smoke 사이클 phase 이름 → 한국어 라벨. 모르는 이름은 원본 그대로 노출.
const SMOKE_PHASE_LABELS: Record<string, string> = {
  koneps_collect: "공고 수집",
  sbert_embedding: "임베딩",
  predict_price: "가격 예측",
  telegram_ping: "텔레그램 발신"
};

const SMOKE_FAILURE_CATEGORY_LABELS: Record<string, string> = {
  credential: "인증/키",
  koneps_response: "나라장터 응답",
  no_candidate: "후보 없음",
  telegram: "텔레그램",
  task_broker: "태스크/브로커",
  db_schema: "DB/schema",
  prediction: "예측",
  unknown: "미분류"
};

function smokePhaseLabel(name: string): string {
  return SMOKE_PHASE_LABELS[name] ?? name;
}

function smokeFailureCategoryLabel(name: string): string {
  return SMOKE_FAILURE_CATEGORY_LABELS[name] ?? name;
}

// 표본 상태 → 라벨. 미지/미보고 상태는 "미실행"로 폴백.
const SAMPLE_STATUS_LABELS: Record<string, string> = {
  sufficient: "충분",
  insufficient_sample: "표본 부족"
};

function sampleStatusLabel(status?: string | null): string {
  return SAMPLE_STATUS_LABELS[status ?? ""] ?? "미실행";
}

// run 상태 → 배지 톤. 미지 상태는 muted 로 폴백.
const RUN_STATUS_TONES: Record<string, "healthy" | "watch" | "critical" | "info" | "muted"> = {
  completed: "healthy",
  failed: "critical",
  running: "watch",
  queued: "watch"
};

function runStatusTone(status?: string | null): "healthy" | "watch" | "critical" | "info" | "muted" {
  return RUN_STATUS_TONES[status ?? ""] ?? "muted";
}

// phase 통과율 → 톤. 데이터가 없으면(평가 0건) muted로 정직하게 표기.
function smokePhaseTone(rate: SmokeTestPhaseRate): "healthy" | "watch" | "critical" | "muted" {
  if (rate.evaluated_count === 0) return "muted";
  if (rate.pass_rate >= 0.9) return "healthy";
  if (rate.pass_rate >= 0.6) return "watch";
  return "critical";
}

export function OperationsScreen() {
  const { session, activeOperator } = useShellContext();
  const activeOperatorId = activeOperator.activeOperatorId;
  const [kpiDays, setKpiDays] = useState<KpiDays>(30);
  const g2Evidence = useQuery<G2EvidenceSummaryResponse | null, Error>({
    queryKey: queryKeys.operations.g2Evidence(activeOperatorId, G2_EVIDENCE_WINDOW_DAYS),
    queryFn: () =>
      fetchG2EvidenceSummary(
        { days: G2_EVIDENCE_WINDOW_DAYS },
        session?.token,
        activeOperatorId
      ),
    enabled: Boolean(session?.token),
    staleTime: 30_000
  });
  const operations = useQuery<OperationsDashboardResponse, Error>({
    queryKey: queryKeys.operations.dashboard(activeOperatorId),
    queryFn: () => fetchOperationsDashboard({ days: 7 }, session?.token, activeOperatorId),
    enabled: Boolean(session?.token),
    refetchInterval: (query) => (document.visibilityState === "visible" ? REFRESH_INTERVAL_MS : false),
    refetchIntervalInBackground: false
  });
  // 회사별 KPI는 시스템 헬스(7일 고정)와 별도 윈도우를 가진다 — 운영자가 KPI
  // 패널에서 직접 7/30/90일을 선택할 수 있도록 별도 셀렉터 + 별도 useQuery 호출.
  const operationsKpi = useOperationsKpiQuery(
    session,
    { days: kpiDays, missedLimit: 10 },
    activeOperatorId
  );

  const criticalCards =
    operations.data?.cards.filter((card) => card.status === "critical") ?? [];
  const selectedOperatorLabel =
    activeOperator.currentOperator?.company ||
    activeOperator.currentOperator?.full_name ||
    activeOperator.currentOperator?.username ||
    operations.data?.current_operator_username ||
    "토큰 소유자";

  return (
    <section className="flex flex-col gap-4" aria-label="운영 대시보드">
      <header className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="break-words text-lg font-semibold text-[var(--color-fg)]">
            G-2 운영 증적 대시보드
          </h2>
          <p className="mt-1 max-w-2xl break-words text-xs text-[var(--color-muted)]">
            관리자 전용 화면입니다. G-2 evidence, blocking gaps, operator별 상태 확인에 집중합니다.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs sm:justify-end">
          <span className="text-[var(--color-muted)]">
            30초마다 자동 갱신 (탭 비활성 시 정지)
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => operations.refetch()}
            disabled={operations.isFetching}
            aria-label="새로고침"
          >
            <RefreshCw size={14} />
          </Button>
        </div>
      </header>

      {criticalCards.length > 0 ? (
        <div
          role="alert"
          aria-label="인시던트 알림"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          <strong>인시던트 {criticalCards.length}건:</strong>{" "}
          {criticalCards.map((card) => card.label).join(", ")}
        </div>
      ) : null}

      {operations.error ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-3 py-2 text-sm text-[var(--color-danger)]"
        >
          {operations.error.message ?? "운영 대시보드를 불러오지 못했습니다."}
        </p>
      ) : null}

      {operations.isPending && !operations.data ? (
        <p className="text-sm text-[var(--color-muted)]">불러오는 중…</p>
      ) : null}

      {operations.data ? (
        <>
          <AdminFocusStrip
            operatorLabel={selectedOperatorLabel}
            operations={operations.data}
            g2Evidence={g2Evidence.data ?? null}
          />

          <G2EvidenceCard
            data={g2Evidence.data ?? null}
            loading={g2Evidence.isPending}
            error={g2Evidence.error}
          />

          <section
            className="flex flex-col gap-2"
            aria-label="G-2 운영 검증 증거"
          >
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              G-2 운영 검증 증거
            </h3>
            <SmokeTestCard summary={operations.data.smoke_test} />
            <SyntheticValidationCard summary={operations.data.synthetic_validation} />
          </section>

          <section
            className="flex flex-col gap-2"
            aria-label="보조 운영 상태"
          >
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              보조 운영 상태
            </h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {operations.data.cards.map((card) => (
                <SummaryCard key={card.key} card={card} />
              ))}
            </div>
          </section>

          <section
            className="flex flex-col gap-2"
            aria-label="회사별 KPI"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-[var(--color-muted)]">
                현재 선택한 회사 기준 — 시스템 헬스(7일)와 별도 윈도우입니다.
              </p>
              <label className="flex items-center gap-2 text-xs">
                KPI 기간
                <select
                  value={kpiDays}
                  onChange={(event) =>
                    setKpiDays(Number(event.target.value) as KpiDays)
                  }
                  aria-label="KPI 기간 (일)"
                  className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
                >
                  {KPI_DAYS_OPTIONS.map((value) => (
                    <option key={value} value={value}>
                      {value}일
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <OperationsKpiPanel
              data={operationsKpi.data}
              loading={operationsKpi.isPending}
              error={operationsKpi.error}
            />
          </section>

          <section className="flex flex-col gap-2" aria-label="데이터 수집/알림 상태">
            <h3 className="text-sm font-semibold text-[var(--color-fg)]">
              데이터 수집/알림 상태
            </h3>
            <CrawlHealth summary={operations.data.crawl} />
            <TelegramHealth summary={operations.data.notifications} />
            <MlReleaseCard summary={operations.data.ml_release} />
          </section>
        </>
      ) : null}
    </section>
  );
}

function AdminFocusStrip({
  operatorLabel,
  operations,
  g2Evidence
}: {
  operatorLabel: string;
  operations: OperationsDashboardResponse;
  g2Evidence: G2EvidenceSummaryResponse | null;
}) {
  const scopedOperator =
    g2Evidence?.current_operator_username ??
    operations.current_operator_username ??
    operatorLabel;
  const gapCount = g2Evidence?.blocking_gaps.length ?? null;
  return (
    <section
      aria-label="G-2 admin 점검 범위"
      className="grid grid-cols-1 gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3 text-xs sm:grid-cols-3"
    >
      <LabeledStat label="점검 operator" value={scopedOperator || "토큰 소유자"} />
      <LabeledStat
        label="G-2 evidence"
        value={g2Evidence ? g2StatusLabel(g2Evidence.evidence_status) : "미연결"}
      />
      <LabeledStat
        label="blocking gaps"
        value={gapCount === null ? "확인 대기" : `${gapCount}건`}
      />
    </section>
  );
}

function SummaryCard({ card }: { card: OperationsDashboardCard }) {
  const valueLabel =
    card.unit === "ratio" ? formatPercent(card.value) : card.value.toLocaleString("ko-KR");
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="min-w-0 break-words text-sm leading-snug">{card.label}</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[card.status]}>
          {card.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <strong className="text-2xl tabular-nums text-[var(--color-fg)]">{valueLabel}</strong>
        <span className="break-words text-xs text-[var(--color-muted)]">{card.detail}</span>
      </CardContent>
    </Card>
  );
}

function G2EvidenceCard({
  data,
  loading,
  error
}: {
  data: G2EvidenceSummaryResponse | null;
  loading: boolean;
  error: Error | null;
}) {
  const sections = [
    { key: "smoke", label: "Smoke 증적", summary: data?.smoke ?? null },
    { key: "strategy_monitor", label: "Strategy monitor", summary: data?.strategy_monitor ?? null },
    { key: "decision_experiments", label: "Decision experiment", summary: data?.decision_experiments ?? null },
    { key: "synthetic_experiments", label: "Synthetic experiment", summary: data?.synthetic_experiments ?? null },
    { key: "notifications", label: "알림 증적", summary: data?.notifications ?? null }
  ];
  return (
    <Card aria-label="G-2 증적 요약">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <CardTitle className="break-words leading-snug">
            G-2 evidence / blocking gaps
          </CardTitle>
          <p className="mt-1 break-words text-xs text-[var(--color-muted)]">
            선택한 operator 기준으로 증적 범위와 남은 차단 gap을 분리 확인합니다.
          </p>
        </div>
        {data ? (
          <Badge className="shrink-0 whitespace-nowrap" tone={g2StatusTone(data.evidence_status)}>
            {g2StatusLabel(data.evidence_status)}
          </Badge>
        ) : (
          <Badge className="shrink-0 whitespace-nowrap" tone="muted">미연결</Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {loading ? (
          <p className="text-[var(--color-muted)]">G-2 증적을 불러오는 중…</p>
        ) : null}

        {error ? (
          <p
            role="alert"
            className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_85%)] px-2 py-1.5 text-[var(--color-danger)]"
          >
            {error.message}
          </p>
        ) : null}

        {!loading && !error && !data ? (
          <p className="rounded-md border border-[var(--color-border)] px-2 py-1.5 text-[var(--color-muted)]">
            G-2 evidence API가 아직 연결되지 않았습니다. Agent A 응답이 들어오면 이 영역에 operator별 증적 상태가 표시됩니다.
          </p>
        ) : null}

        {data ? (
          <>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <LabeledStat
                label="점검 operator"
                value={
                  data.current_operator_username ??
                  (data.current_operator_id ? `#${data.current_operator_id}` : "-")
                }
              />
              <LabeledStat label="증적 window" value={`${data.window_days}일`} />
              <LabeledStat label="blocking gaps" value={data.blocking_gaps.length.toString()} />
              <LabeledStat
                label="생성 시각"
                value={data.generated_at ? formatDateTime(data.generated_at) : "-"}
              />
            </div>

            {data.blocking_gaps.length > 0 ? (
              <div className="flex flex-col gap-1" aria-label="G-2 차단 gap">
                <span className="font-medium text-[var(--color-fg)]">차단 gap</span>
                <ul className="flex flex-col gap-1">
                  {data.blocking_gaps.map((gap, index) => (
                    <li
                      key={`${gap}-${index}`}
                      className="break-words rounded-md bg-[color-mix(in_oklch,var(--color-warn),white_84%)] px-2 py-1 text-[color-mix(in_oklch,var(--color-warn),black_45%)]"
                    >
                      {gap}
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="text-[var(--color-muted)]">차단 gap 없음</p>
            )}

            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {sections.map((section) => (
                <EvidenceDomainRow
                  key={section.key}
                  label={section.label}
                  summary={section.summary}
                />
              ))}
            </div>

            {(data.warnings ?? []).length > 0 ? (
              <div className="flex flex-wrap gap-1" aria-label="G-2 증적 경고">
                {(data.warnings ?? []).map((warning, index) => (
                  <Badge key={`${warning}-${index}`} className="max-w-full whitespace-normal break-words" tone="watch">
                    {warning}
                  </Badge>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EvidenceDomainRow({
  label,
  summary
}: {
  label: string;
  summary: G2EvidenceSectionSummary | null;
}) {
  const status = summary?.evidence_status ?? summary?.status ?? "missing";
  const count = summary?.evidence_count ?? summary?.run_count ?? null;
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-[var(--color-border)] px-2 py-1.5">
      <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
        <span className="truncate font-medium text-[var(--color-fg)]">{label}</span>
        <Badge className="shrink-0 whitespace-nowrap" tone={g2StatusTone(status)}>
          {g2StatusLabel(status)}
        </Badge>
      </div>
      <p className="break-words text-[var(--color-muted)]">
        {summary?.detail ?? summary?.summary ?? "증적 없음"}
      </p>
      <div className="flex flex-wrap gap-1 text-[var(--color-muted)]">
        {typeof count === "number" ? <span>evidence {count}</span> : null}
        {summary?.latest_at ? <span>latest {formatDateTime(summary.latest_at)}</span> : null}
        {(summary?.blocking_gaps ?? []).length > 0 ? (
          <span>gap {(summary?.blocking_gaps ?? []).length}</span>
        ) : null}
      </div>
    </div>
  );
}

// G-2 증적 상태 → 배지 톤. 미지 상태는 info(중립 고지)로 폴백.
const G2_STATUS_TONES: Record<string, "healthy" | "watch" | "critical" | "info" | "muted"> = {
  ready: "healthy",
  insufficient: "watch",
  mixed_scope: "critical",
  missing: "muted"
};

function g2StatusTone(status?: string | null): "healthy" | "watch" | "critical" | "info" | "muted" {
  return G2_STATUS_TONES[status ?? ""] ?? "info";
}

// G-2 증적 상태 → 한국어 라벨. 미지 상태는 원문 그대로 노출(조용한 오표시 금지).
const G2_STATUS_LABELS: Record<G2EvidenceStatus, string> = {
  ready: "증적 충분",
  insufficient: "증적 부족",
  mixed_scope: "범위 혼합",
  missing: "증적 없음"
};

function g2StatusLabel(status?: string | null): string {
  if (status && status in G2_STATUS_LABELS) return G2_STATUS_LABELS[status as G2EvidenceStatus];
  return status ?? "missing";
}

function CrawlHealth({ summary }: { summary: OperationsDashboardResponse["crawl"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>크롤 상태</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <LabeledStat label="job_count" value={summary.job_count.toString()} />
          <LabeledStat label="completed" value={summary.completed_count.toString()} />
          <LabeledStat label="failed" value={summary.failed_count.toString()} />
          <LabeledStat label="success_rate" value={formatPercent(summary.success_rate)} />
        </div>
        <p className="text-[var(--color-muted)]">
          last_success {summary.last_success_at ? formatDateTime(summary.last_success_at) : "-"} ·
          last_failure {summary.last_failure_at ? formatDateTime(summary.last_failure_at) : "-"}
        </p>
        {summary.recent_failures.length > 0 ? (
          <details>
            <summary className="cursor-pointer text-[var(--color-fg)]">최근 실패 ({summary.recent_failures.length})</summary>
            <ul className="mt-1 flex flex-col gap-1">
              {summary.recent_failures.slice(0, 5).map((failure) => (
                <li key={failure.crawl_job_id} className="text-[var(--color-danger)]">
                  {failure.source} · {failure.error_message ?? failure.status}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

function TelegramHealth({ summary }: { summary: OperationsDashboardResponse["notifications"] }) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">텔레그램 / 알림</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.telegram_status]}>
          {summary.telegram_status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <LabeledStat label="전송 시도" value={summary.telegram_delivery_attempt_count.toString()} />
          <LabeledStat label="성공" value={summary.telegram_sent_count.toString()} />
          <LabeledStat label="실패" value={summary.telegram_failed_count.toString()} />
          <LabeledStat label="성공률" value={formatPercent(summary.telegram_success_rate)} />
        </div>
        <p className="break-words text-[var(--color-muted)]">{summary.telegram_detail}</p>
      </CardContent>
    </Card>
  );
}

function MlReleaseCard({ summary }: { summary: OperationsDashboardResponse["ml_release"] }) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">ML release</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.status]}>
          {summary.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <p className="break-words text-[var(--color-fg)]">{summary.detail}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <LabeledStat label="latest_tag" value={summary.latest_release_tag ?? "-"} />
          <LabeledStat label="signature" value={summary.latest_signature_status} />
          <LabeledStat label="gate" value={summary.latest_gate_status} />
        </div>
        <p className="break-words text-[var(--color-muted)]">backtest: {summary.backtest_detail}</p>
      </CardContent>
    </Card>
  );
}

function SyntheticValidationCard({
  summary
}: {
  summary: OperationsDashboardResponse["synthetic_validation"];
}) {
  const latest = summary.latest ?? null;
  const presets = summary.presets ?? [];
  return (
    <Card aria-label="G-1 가상 회사 검증">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">G-1 가상 회사 검증</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.status]}>
          {summary.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <LabeledStat
            label="preset 저장"
            value={`${summary.saved_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="완료 preset"
            value={`${summary.completed_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="충분 표본"
            value={`${summary.sufficient_preset_count}/${summary.preset_count}`}
          />
          <LabeledStat
            label="최근 실행"
            value={`${summary.recent_completed_count}/${summary.recent_run_count}`}
          />
        </div>

        <p className="break-words text-[var(--color-muted)]">{summary.detail}</p>

        {latest ? (
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="text-[var(--color-muted)]">최근 run</span>
            <span className="break-words text-[var(--color-fg)]">
              {latest.experiment_name ?? `experiment ${latest.experiment_id}`}
            </span>
            <Badge className="shrink-0 whitespace-nowrap" tone={runStatusTone(latest.status)}>
              {latest.status}
            </Badge>
            <span className="break-words text-[var(--color-fg)]">
              settled {latest.total_settled_count}/{summary.sample_target}
            </span>
            <span className="break-words text-[var(--color-muted)]">
              {latest.finished_at ? formatDateTime(latest.finished_at) : "종료 시각 없음"}
            </span>
          </div>
        ) : (
          <p className="rounded-md border border-[var(--color-border)] px-2 py-1.5 text-[var(--color-muted)]">
            아직 G-1 synthetic experiment run이 없습니다.
          </p>
        )}

        {presets.length > 0 ? (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {presets.map((preset) => (
              <div
                key={preset.name}
                className="flex min-w-0 flex-col gap-1 rounded-md border border-[var(--color-border)] px-2 py-1.5"
              >
                <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                  <span className="truncate font-medium text-[var(--color-fg)]">
                    {preset.name}
                  </span>
                  <Badge className="shrink-0 whitespace-nowrap" tone={runStatusTone(preset.latest_run_status)}>
                    {preset.latest_run_status ?? "not saved"}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-center gap-1 text-[var(--color-muted)]">
                  <span>{sampleStatusLabel(preset.sample_status)}</span>
                  <span>· settled {preset.total_settled_count}</span>
                  {preset.missing_total_settled_count > 0 ? (
                    <span>· 부족 {preset.missing_total_settled_count}</span>
                  ) : null}
                  {preset.insufficient_operator_count > 0 ? (
                    <span>· 표본 부족 회사 {preset.insufficient_operator_count}</span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SmokeTestCard({ summary }: { summary: OperationsDashboardResponse["smoke_test"] }) {
  const perPhase = summary.per_phase ?? [];
  const latest = summary.latest ?? null;
  const recentFailures = summary.recent_failures ?? [];
  const target = summary.healthy_streak_target ?? 7;
  const categoryBreakdown = summary.failure_category_breakdown ?? {};
  const disabledEmpty = !summary.schedule_enabled && summary.cycle_count === 0;
  const disabledManual = !summary.schedule_enabled && summary.cycle_count > 0;
  const overallTone: "healthy" | "watch" | "critical" | "muted" =
    summary.cycle_count === 0
      ? "muted"
      : summary.current_streak_meets_target
        ? "healthy"
        : summary.pass_rate >= 0.6
          ? "watch"
          : "critical";

  return (
    <Card aria-label="운영 검증 (스모크 사이클)">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">운영 검증 (스모크 사이클)</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={overallTone}>
          {formatPercent(summary.pass_rate)}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <LabeledStat label="통과율" value={formatPercent(summary.pass_rate)} />
          <LabeledStat label="G-0 연속 통과" value={`${summary.current_streak}/${target}회`} />
          <LabeledStat
            label="총 사이클"
            value={`${summary.cycle_count}건 (통과 ${summary.passed_count}/실패 ${summary.failed_count})`}
          />
        </div>

        {disabledEmpty ? (
          <p className="rounded-md border border-[var(--color-warn)] bg-[color-mix(in_oklch,var(--color-warn),white_82%)] px-2 py-1.5 text-[color-mix(in_oklch,var(--color-warn),black_40%)]">
            smoke 스케줄이 비활성 상태입니다 (<code>SMOKE_TEST_SCHEDULE_ENABLED=false</code>). 자동 검증 데이터가 아직 없습니다.
          </p>
        ) : null}
        {disabledManual ? (
          <p className="text-[var(--color-muted)]">스케줄 비활성 — 수동 실행분</p>
        ) : null}

        {perPhase.length > 0 ? (
          <div className="flex flex-col gap-1.5">
            <span className="text-[var(--color-muted)]">단계별 통과율</span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {perPhase.map((phase) => (
                <div
                  key={phase.name}
                  className="flex min-w-0 flex-wrap items-center justify-between gap-1 rounded-md border border-[var(--color-border)] px-2 py-1"
                >
                  <span className="break-words text-[var(--color-fg)]">{smokePhaseLabel(phase.name)}</span>
                  {phase.evaluated_count === 0 ? (
                    <Badge className="shrink-0 whitespace-nowrap" tone="muted">데이터 없음</Badge>
                  ) : (
                    <Badge className="shrink-0 whitespace-nowrap" tone={smokePhaseTone(phase)}>
                      {formatPercent(phase.pass_rate)}
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {latest ? (
          <div className="flex flex-col gap-1.5">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <span className="text-[var(--color-muted)]">최근 실행</span>
              <span className="break-words text-[var(--color-fg)]">
                {latest.started_at ? formatDateTime(latest.started_at) : "시각 미상"}
              </span>
              <Badge className="shrink-0 whitespace-nowrap" tone={latest.overall_passed ? "healthy" : "critical"}>
                {latest.overall_passed ? "PASS" : "FAIL"}
              </Badge>
            </div>
            {latest.phases && latest.phases.length > 0 ? (
              <ul className="flex flex-col gap-1">
                {latest.phases.map((phase) => (
                  <li key={phase.name} className="flex min-w-0 flex-wrap items-center gap-1.5">
                    <Badge className="shrink-0 whitespace-nowrap" tone={phase.passed ? "healthy" : "critical"}>
                      {phase.passed ? "PASS" : "FAIL"}
                    </Badge>
                    <span className="break-words text-[var(--color-fg)]">{smokePhaseLabel(phase.name)}</span>
                    {!phase.passed && phase.failure_category ? (
                      <Badge className="shrink-0 whitespace-nowrap" tone="watch">
                        {smokeFailureCategoryLabel(phase.failure_category)}
                      </Badge>
                    ) : null}
                    {!phase.passed && phase.detail ? (
                      <span className="break-words text-[var(--color-muted)]">{phase.detail}</span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {Object.keys(categoryBreakdown).length > 0 ? (
          <div className="flex flex-col gap-1">
            <span className="text-[var(--color-muted)]">실패 원인 분류</span>
            <div className="flex flex-wrap gap-1">
              {Object.entries(categoryBreakdown).map(([category, count]) => (
                <Badge key={category} tone="watch">
                  {smokeFailureCategoryLabel(category)} {count}
                </Badge>
              ))}
            </div>
          </div>
        ) : null}

        {recentFailures.length > 0 ? (
          <div className="flex flex-col gap-1">
            <span className="text-[var(--color-muted)]">최근 실패 사이클</span>
            <ul className="flex flex-col gap-1">
              {recentFailures.map((failure, index) => (
                <li
                  key={`${failure.started_at ?? "unknown"}-${index}`}
                  className="break-words text-[var(--color-danger)]"
                >
                  {failure.started_at ? formatDateTime(failure.started_at) : "시각 미상"} · 실패 단계{" "}
                  {(failure.failed_phases ?? []).map(smokePhaseLabel).join(", ") || "-"}
                  {(failure.failure_categories ?? []).length > 0 ? (
                    <span>
                      {" "}· 원인{" "}
                      {(failure.failure_categories ?? [])
                        .map(smokeFailureCategoryLabel)
                        .join(", ")}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
