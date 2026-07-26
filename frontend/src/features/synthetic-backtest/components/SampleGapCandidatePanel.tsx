import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import type {
  SyntheticExperimentSampleGapItem,
  SyntheticExperimentSampleGapPlanResponse,
  SyntheticExperimentSampleGapRunCandidateResponse
} from "@/shared/types/synthetic";
import { MIXED_DATA_WARNING, gapTitle, hasMixedWarning } from "./helpers";
import { SelectedSampleGapCandidate } from "./SelectedSampleGapCandidate";

export function SampleGapCandidatePanel({
  plan,
  loading,
  error,
  candidate,
  building,
  selecting,
  saving,
  onBuild,
  onSelectExperiment,
  onSavePreset
}: {
  plan?: SyntheticExperimentSampleGapPlanResponse;
  loading: boolean;
  error: unknown;
  candidate: SyntheticExperimentSampleGapRunCandidateResponse | null;
  building: boolean;
  selecting: boolean;
  saving: boolean;
  onBuild: (gap: SyntheticExperimentSampleGapItem, actionCode?: string | null) => void;
  onSelectExperiment: (experimentId: number) => void;
  onSavePreset: (name: string) => void;
}) {
  const gaps = plan?.gaps ?? [];
  const visibleGaps = gaps.slice(0, 5);
  const planHasMixedWarning =
    plan?.warnings?.some((warning) => warning.code === MIXED_DATA_WARNING) ?? false;

  return (
    <Card aria-label="sample-gap 실행 후보">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>Sample gap 실행 후보</CardTitle>
        {plan ? <Badge tone={plan.gap_count > 0 ? "watch" : "healthy"}>{plan.gap_count}건</Badge> : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {loading ? <p className="text-[var(--color-muted)]">sample-gap 계획을 불러오는 중…</p> : null}
        {error ? (
          <p className="text-[var(--color-danger)]">sample-gap 계획을 불러오지 못했습니다.</p>
        ) : null}
        {planHasMixedWarning ? (
          <div className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-3 py-2 text-[var(--color-danger)]">
            canonical 데이터가 섞인 run이 있습니다. 실행보다 synthetic-only 재실행 또는 정리 확인이 우선입니다.
          </div>
        ) : null}
        {!loading && visibleGaps.length === 0 ? (
          <p className="text-[var(--color-muted)]">최근 완료 run에서 부족 표본이 없습니다.</p>
        ) : null}
        {visibleGaps.map((gap) => (
          <div
            key={`${gap.dimension}:${gap.key}`}
            className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] px-3 py-2"
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="font-medium text-[var(--color-fg)]">{gapTitle(gap)}</span>
                <span className="text-[var(--color-muted)]">
                  부족 {gap.missing_settled_count}/{gap.sample_target} · source {gap.source_run_count}
                </span>
              </span>
              <Badge tone={hasMixedWarning(gap.warnings) ? "critical" : "watch"}>
                priority {gap.priority}
              </Badge>
            </div>
            {hasMixedWarning(gap.warnings) ? (
              <p className="rounded-md bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-2 py-1 text-[var(--color-danger)]">
                혼합 데이터 경고가 있어 실행 후보보다 synthetic-only 재실행/정리 확인이 먼저 필요합니다.
              </p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              {(gap.recommendation.actions ?? []).map((action) => (
                <Button
                  key={action.code}
                  type="button"
                  size="sm"
                  variant={action.code === "rerun_synthetic_only" ? "destructive" : "secondary"}
                  onClick={() => onBuild(gap, action.code)}
                  disabled={building}
                >
                  {building ? "생성 중…" : action.label}
                </Button>
              ))}
              {(gap.recommendation.actions ?? []).length === 0 ? (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => onBuild(gap)}
                  disabled={building}
                >
                  후보 생성
                </Button>
              ) : null}
            </div>
          </div>
        ))}
        {candidate ? (
          <SelectedSampleGapCandidate
            candidate={candidate}
            selecting={selecting}
            saving={saving}
            onSelectExperiment={onSelectExperiment}
            onSavePreset={onSavePreset}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}
