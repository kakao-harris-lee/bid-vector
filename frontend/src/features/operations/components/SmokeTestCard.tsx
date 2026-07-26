import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatDateTime, formatPercent } from "@/shared/lib";
import type { OperationsDashboardResponse } from "@/shared/types/operations";
import { smokeFailureCategoryLabel, smokePhaseLabel, smokePhaseTone } from "./helpers";

export function SmokeTestCard({ summary }: { summary: OperationsDashboardResponse["smoke_test"] }) {
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
