import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useShellContext } from "@/app/dashboardContext";
import { fetchOperationsDashboard, queryKeys } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatDateTime, formatPercent } from "@/shared/lib";
import { OperationsKpiPanel, useOperationsKpiQuery } from "@/features/decisions";
import type {
  OperationsCardStatus,
  OperationsDashboardCard,
  OperationsDashboardResponse,
  SmokeTestPhaseRate
} from "@/shared/types/operations";

const REFRESH_INTERVAL_MS = 30_000;
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

function sampleStatusLabel(status?: string | null): string {
  if (status === "sufficient") return "충분";
  if (status === "insufficient_sample") return "표본 부족";
  return "미실행";
}

function runStatusTone(status?: string | null): "healthy" | "watch" | "critical" | "info" | "muted" {
  if (status === "completed") return "healthy";
  if (status === "failed") return "critical";
  if (status === "running" || status === "queued") return "watch";
  return "muted";
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

  return (
    <section className="flex flex-col gap-4" aria-label="운영 대시보드">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">운영 대시보드</h2>
        <div className="flex items-center gap-2 text-xs">
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
          <section
            className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
            aria-label="요약 카드"
          >
            {operations.data.cards.map((card) => (
              <SummaryCard key={card.key} card={card} />
            ))}
          </section>

          <SmokeTestCard summary={operations.data.smoke_test} />
          <SyntheticValidationCard summary={operations.data.synthetic_validation} />

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

          <CrawlHealth summary={operations.data.crawl} />
          <TelegramHealth summary={operations.data.notifications} />
          <MlReleaseCard summary={operations.data.ml_release} />
        </>
      ) : null}
    </section>
  );
}

function SummaryCard({ card }: { card: OperationsDashboardCard }) {
  const valueLabel =
    card.unit === "ratio" ? formatPercent(card.value) : card.value.toLocaleString("ko-KR");
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle className="text-sm">{card.label}</CardTitle>
        <Badge tone={TONE[card.status]}>{card.status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <strong className="text-2xl tabular-nums text-[var(--color-fg)]">{valueLabel}</strong>
        <span className="text-xs text-[var(--color-muted)]">{card.detail}</span>
      </CardContent>
    </Card>
  );
}

function CrawlHealth({ summary }: { summary: OperationsDashboardResponse["crawl"] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>크롤 상태</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="job_count" value={summary.job_count.toString()} />
          <Stat label="completed" value={summary.completed_count.toString()} />
          <Stat label="failed" value={summary.failed_count.toString()} />
          <Stat label="success_rate" value={formatPercent(summary.success_rate)} />
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
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>텔레그램 / 알림</CardTitle>
        <Badge tone={TONE[summary.telegram_status]}>{summary.telegram_status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="전송 시도" value={summary.telegram_delivery_attempt_count.toString()} />
          <Stat label="성공" value={summary.telegram_sent_count.toString()} />
          <Stat label="실패" value={summary.telegram_failed_count.toString()} />
          <Stat label="성공률" value={formatPercent(summary.telegram_success_rate)} />
        </div>
        <p className="text-[var(--color-muted)]">{summary.telegram_detail}</p>
      </CardContent>
    </Card>
  );
}

function MlReleaseCard({ summary }: { summary: OperationsDashboardResponse["ml_release"] }) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>ML release</CardTitle>
        <Badge tone={TONE[summary.status]}>{summary.status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <p className="text-[var(--color-fg)]">{summary.detail}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="latest_tag" value={summary.latest_release_tag ?? "-"} />
          <Stat label="signature" value={summary.latest_signature_status} />
          <Stat label="gate" value={summary.latest_gate_status} />
        </div>
        <p className="text-[var(--color-muted)]">backtest: {summary.backtest_detail}</p>
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
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>G-1 가상 회사 검증</CardTitle>
        <Badge tone={TONE[summary.status]}>{summary.status}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat
            label="preset 저장"
            value={`${summary.saved_preset_count}/${summary.preset_count}`}
          />
          <Stat
            label="완료 preset"
            value={`${summary.completed_preset_count}/${summary.preset_count}`}
          />
          <Stat
            label="충분 표본"
            value={`${summary.sufficient_preset_count}/${summary.preset_count}`}
          />
          <Stat
            label="최근 실행"
            value={`${summary.recent_completed_count}/${summary.recent_run_count}`}
          />
        </div>

        <p className="text-[var(--color-muted)]">{summary.detail}</p>

        {latest ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[var(--color-muted)]">최근 run</span>
            <span className="text-[var(--color-fg)]">
              {latest.experiment_name ?? `experiment ${latest.experiment_id}`}
            </span>
            <Badge tone={runStatusTone(latest.status)}>{latest.status}</Badge>
            <span className="text-[var(--color-fg)]">
              settled {latest.total_settled_count}/{summary.sample_target}
            </span>
            <span className="text-[var(--color-muted)]">
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
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <span className="truncate font-medium text-[var(--color-fg)]">
                    {preset.name}
                  </span>
                  <Badge tone={runStatusTone(preset.latest_run_status)}>
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
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>운영 검증 (스모크 사이클)</CardTitle>
        <Badge tone={overallTone}>{formatPercent(summary.pass_rate)}</Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="통과율" value={formatPercent(summary.pass_rate)} />
          <Stat label="G-0 연속 통과" value={`${summary.current_streak}/${target}회`} />
          <Stat
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
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {perPhase.map((phase) => (
                <div
                  key={phase.name}
                  className="flex items-center justify-between gap-1 rounded-md border border-[var(--color-border)] px-2 py-1"
                >
                  <span className="text-[var(--color-fg)]">{smokePhaseLabel(phase.name)}</span>
                  {phase.evaluated_count === 0 ? (
                    <Badge tone="muted">데이터 없음</Badge>
                  ) : (
                    <Badge tone={smokePhaseTone(phase)}>{formatPercent(phase.pass_rate)}</Badge>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {latest ? (
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[var(--color-muted)]">최근 실행</span>
              <span className="text-[var(--color-fg)]">
                {latest.started_at ? formatDateTime(latest.started_at) : "시각 미상"}
              </span>
              <Badge tone={latest.overall_passed ? "healthy" : "critical"}>
                {latest.overall_passed ? "PASS" : "FAIL"}
              </Badge>
            </div>
            {latest.phases && latest.phases.length > 0 ? (
              <ul className="flex flex-col gap-1">
                {latest.phases.map((phase) => (
                  <li key={phase.name} className="flex flex-wrap items-center gap-1.5">
                    <Badge tone={phase.passed ? "healthy" : "critical"}>
                      {phase.passed ? "PASS" : "FAIL"}
                    </Badge>
                    <span className="text-[var(--color-fg)]">{smokePhaseLabel(phase.name)}</span>
                    {!phase.passed && phase.failure_category ? (
                      <Badge tone="watch">
                        {smokeFailureCategoryLabel(phase.failure_category)}
                      </Badge>
                    ) : null}
                    {!phase.passed && phase.detail ? (
                      <span className="text-[var(--color-muted)]">{phase.detail}</span>
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
                  className="text-[var(--color-danger)]"
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

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[var(--color-muted)]">{label}</span>
      <strong className="tabular-nums text-[var(--color-fg)]">{value}</strong>
    </div>
  );
}
