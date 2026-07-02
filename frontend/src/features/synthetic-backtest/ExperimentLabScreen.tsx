import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useShellContext } from "@/app/dashboardContext";
import {
  buildExperimentSampleGapCandidate,
  ensureExperimentPreset,
  fetchExperiment,
  fetchExperimentPresets,
  fetchExperimentSampleGaps,
  triggerExperimentRun
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatDateTime } from "@/shared/lib";
import type {
  SyntheticExperimentPreset,
  SyntheticExperimentResponse,
  SyntheticExperimentRunResponse,
  SyntheticExperimentSampleGapItem,
  SyntheticExperimentSampleGapOperatorTarget,
  SyntheticExperimentSampleGapPlanResponse,
  SyntheticExperimentSampleGapRunCandidateResponse
} from "@/shared/types/synthetic";
import { SyntheticBacktestScreen } from "./SyntheticBacktestScreen";
import { ExperimentForm } from "./ExperimentForm";
import { ExperimentList } from "./ExperimentList";
import { ExperimentRunProgress } from "./ExperimentRunProgress";
import { RunComparePanel } from "./RunComparePanel";
import { CustomOperatorManager } from "./CustomOperatorManager";

type LabTab = "experiments" | "companies" | "compare";

export function ExperimentLabScreen() {
  const { session } = useShellContext();
  const token = session?.token;
  const queryClient = useQueryClient();

  const [tab, setTab] = useState<LabTab>("experiments");
  const [selected, setSelected] = useState<SyntheticExperimentResponse | null>(null);
  const [activeRunId, setActiveRunId] = useState<number | null>(null);
  const [candidate, setCandidate] =
    useState<SyntheticExperimentSampleGapRunCandidateResponse | null>(null);

  const presets = useQuery({
    queryKey: ["synthetic", "experiments", "presets"],
    queryFn: () => fetchExperimentPresets(token),
    enabled: Boolean(token)
  });

  const sampleGaps = useQuery({
    queryKey: ["synthetic", "experiments", "sample-gaps", 20],
    queryFn: () => fetchExperimentSampleGaps(20, token),
    enabled: Boolean(token) && tab === "experiments"
  });

  const presetMutation = useMutation<SyntheticExperimentResponse, Error, string>({
    mutationFn: (presetName) => ensureExperimentPreset(presetName, token),
    onSuccess: (experiment) => {
      setSelected(experiment);
      setActiveRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["synthetic", "experiments"] });
      void queryClient.invalidateQueries({
        queryKey: ["synthetic", "experiments", "sample-gaps"]
      });
      toastApi.success({
        title: "Preset 저장",
        description: experiment.name
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "Preset 저장 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const runMutation = useMutation<SyntheticExperimentRunResponse, Error, number>({
    mutationFn: (experimentId) => triggerExperimentRun(experimentId, token),
    onSuccess: (run) => {
      setActiveRunId(run.id);
      void queryClient.invalidateQueries({ queryKey: ["synthetic", "experiments"] });
      void queryClient.invalidateQueries({
        queryKey: ["synthetic", "experiments", "sample-gaps"]
      });
      toastApi.success({
        title: "실행 시작",
        description: "실험을 비동기로 실행합니다. 진행 상황을 폴링합니다."
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "실행 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const handleSelect = (experiment: SyntheticExperimentResponse) => {
    setSelected(experiment);
    setActiveRunId(null);
  };

  const candidateMutation = useMutation<
    SyntheticExperimentSampleGapRunCandidateResponse,
    Error,
    { gap: SyntheticExperimentSampleGapItem; actionCode?: string | null }
  >({
    mutationFn: ({ gap, actionCode }) =>
      buildExperimentSampleGapCandidate(
        {
          dimension: gap.dimension,
          key: gap.key,
          max_runs: 20,
          action_code: actionCode ?? undefined
        },
        token
      ),
    onSuccess: (nextCandidate) => {
      setCandidate(nextCandidate);
      toastApi.success({
        title: "후보 생성",
        description: nextCandidate.preset_name ?? `${nextCandidate.gap.dimension}:${nextCandidate.gap.key}`
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "후보 생성 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const candidateSelectMutation = useMutation<SyntheticExperimentResponse, Error, number>({
    mutationFn: (experimentId) => fetchExperiment(experimentId, token),
    onSuccess: (experiment) => {
      handleSelect(experiment);
      toastApi.success({
        title: "실험 선택",
        description: experiment.name
      });
    },
    onError: (err) =>
      toastApi.danger({
        title: "실험 선택 실패",
        description: err instanceof Error ? err.message : "알 수 없는 오류"
      })
  });

  const tabButtonClass = (value: LabTab) =>
    `rounded-md px-3 py-1.5 text-sm transition-colors ${
      tab === value
        ? "bg-[var(--color-primary)] text-[var(--color-primary-foreground)]"
        : "bg-[var(--color-secondary)] text-[var(--color-secondary-foreground)] hover:opacity-90"
    }`;

  return (
    <section className="flex flex-col gap-4" aria-label="가상 회사 낙찰 실험실">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">가상 회사 낙찰 실험실</h2>
        <span className="text-xs text-[var(--color-muted)]">
          win_rate는 가격 기준 추정 낙찰. 실제 낙찰이 아님.
        </span>
      </header>

      <div className="flex gap-2" role="tablist" aria-label="실험실 탭">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "experiments"}
          className={tabButtonClass("experiments")}
          onClick={() => setTab("experiments")}
        >
          실험
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "companies"}
          className={tabButtonClass("companies")}
          onClick={() => setTab("companies")}
        >
          가상 회사
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "compare"}
          className={tabButtonClass("compare")}
          onClick={() => setTab("compare")}
        >
          비교 / 시드
        </button>
      </div>

      {tab === "companies" ? <CustomOperatorManager token={token} /> : null}

      {tab === "experiments" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="flex flex-col gap-4">
            <ExperimentForm token={token} onCreated={handleSelect} />
            <SampleGapCandidatePanel
              plan={sampleGaps.data}
              loading={sampleGaps.isLoading}
              error={sampleGaps.error}
              candidate={candidate}
              building={candidateMutation.isPending}
              selecting={candidateSelectMutation.isPending}
              saving={presetMutation.isPending}
              onBuild={(gap, actionCode) =>
                candidateMutation.mutate({ gap, actionCode })
              }
              onSelectExperiment={(experimentId) =>
                candidateSelectMutation.mutate(experimentId)
              }
              onSavePreset={(name) => presetMutation.mutate(name)}
            />
            <PresetPanel
              presets={presets.data?.presets ?? []}
              loading={presets.isPending}
              savingName={presetMutation.variables ?? null}
              saving={presetMutation.isPending}
              onSave={(name) => presetMutation.mutate(name)}
            />
            <ExperimentList
              token={token}
              selectedId={selected?.id ?? null}
              onSelect={handleSelect}
            />
          </div>

          <div className="flex flex-col gap-4">
            {selected ? (
              <Card aria-label="선택된 실험">
                <CardHeader className="flex-row items-center justify-between">
                  <CardTitle>{selected.name}</CardTitle>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => runMutation.mutate(selected.id)}
                    disabled={runMutation.isPending}
                  >
                    {runMutation.isPending ? "실행 중…" : "실행"}
                  </Button>
                </CardHeader>
                <CardContent className="flex flex-col gap-2 text-xs text-[var(--color-muted)]">
                  {selected.description ? (
                    <p className="text-sm text-[var(--color-fg)]">{selected.description}</p>
                  ) : null}
                  <dl className="grid grid-cols-2 gap-1">
                    <dt>시나리오</dt>
                    <dd className="text-right text-[var(--color-fg)]">{selected.params.scenario}</dd>
                    <dt>limit</dt>
                    <dd className="text-right text-[var(--color-fg)]">{selected.params.limit}</dd>
                    <dt>카테고리</dt>
                    <dd className="text-right text-[var(--color-fg)]">
                      {selected.params.category ?? "전체"}
                    </dd>
                    <dt>참여 회사</dt>
                    <dd className="text-right text-[var(--color-fg)]">
                      {selected.operator_slugs && selected.operator_slugs.length > 0
                        ? selected.operator_slugs.join(", ")
                        : "전체"}
                    </dd>
                    <dt>생성</dt>
                    <dd className="text-right text-[var(--color-fg)]">
                      {selected.created_at ? formatDateTime(selected.created_at) : "—"}
                    </dd>
                  </dl>
                </CardContent>
              </Card>
            ) : (
              <Card aria-label="실험 안내">
                <CardContent className="py-6 text-center text-sm text-[var(--color-muted)]">
                  좌측에서 실험을 생성하거나 이력에서 선택하세요.
                </CardContent>
              </Card>
            )}

            {selected && activeRunId != null ? (
              <ExperimentRunProgress
                experimentId={selected.id}
                runId={activeRunId}
                token={token}
              />
            ) : null}
          </div>
        </div>
      ) : null}

      {tab === "compare" ? (
        <div className="flex flex-col gap-4">
          <RunComparePanel token={token} />
          <SyntheticBacktestScreen />
        </div>
      ) : null}
    </section>
  );
}

const MIXED_DATA_WARNING = "canonical_synthetic_mixed";

const GAP_DIMENSION_LABEL: Record<string, string> = {
  preset: "preset",
  category: "카테고리",
  business_type: "업종",
  budget_band: "예산구간"
};

const NEXT_STEP_LABEL: Record<string, string> = {
  resolve_mixed_data: "혼합 데이터 정리",
  run_existing_experiment: "기존 실험 선택",
  save_preset: "preset 저장",
  create_experiment: "실험 생성"
};

function gapTitle(gap: SyntheticExperimentSampleGapItem): string {
  return `${GAP_DIMENSION_LABEL[gap.dimension] ?? gap.dimension} · ${gap.key}`;
}

function hasMixedWarning(warnings?: string[] | null): boolean {
  return (warnings ?? []).includes(MIXED_DATA_WARNING);
}

function boolValue(value?: boolean | null): string {
  if (value === true) return "true";
  if (value === false) return "false";
  return "unknown";
}

function unresolvedOperatorTargets(
  targets?: SyntheticExperimentSampleGapOperatorTarget[] | null
): SyntheticExperimentSampleGapOperatorTarget[] {
  return (targets ?? []).filter(
    (target) =>
      target.resolved === false ||
      target.operator_id_scope_ready === false ||
      target.operator_id == null
  );
}

function SampleGapCandidatePanel({
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

function SelectedSampleGapCandidate({
  candidate,
  selecting,
  saving,
  onSelectExperiment,
  onSavePreset
}: {
  candidate: SyntheticExperimentSampleGapRunCandidateResponse;
  selecting: boolean;
  saving: boolean;
  onSelectExperiment: (experimentId: number) => void;
  onSavePreset: (name: string) => void;
}) {
  const blockedWarnings = candidate.blocked_by_warnings ?? [];
  const hasMixedBlock = hasMixedWarning(blockedWarnings) || hasMixedWarning(candidate.warnings);
  const unresolvedTargets = unresolvedOperatorTargets(candidate.operator_targets);
  const operatorScopeBlocked =
    candidate.operator_id_scope_ready === false || unresolvedTargets.length > 0;
  const actionBlocked = !candidate.run_allowed || hasMixedBlock || operatorScopeBlocked;

  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--color-primary)] px-3 py-2"
      aria-label="선택된 sample-gap 후보"
    >
      {hasMixedBlock ? (
        <div className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-2 py-1 text-[var(--color-danger)]">
          {candidate.message}
        </div>
      ) : null}
      {operatorScopeBlocked ? (
        <div className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_92%)] px-2 py-1 text-[var(--color-danger)]">
          operator_id scope가 준비되지 않았습니다. 이 실행 경로를 선택하거나 저장하기 전에 synthetic operator target을 먼저 정리하세요.
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium text-[var(--color-fg)]">
          {candidate.preset_name ?? gapTitle(candidate.gap)}
        </span>
        <Badge tone={actionBlocked ? "critical" : "info"}>
          {NEXT_STEP_LABEL[candidate.next_step] ?? candidate.next_step}
        </Badge>
      </div>
      <dl className="grid grid-cols-2 gap-1 text-[var(--color-muted)]">
        <dt>추천 action</dt>
        <dd className="text-right text-[var(--color-fg)]">{candidate.action_label}</dd>
        <dt>카테고리</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {candidate.params.category ?? "전체"}
        </dd>
        <dt>limit</dt>
        <dd className="text-right text-[var(--color-fg)]">{candidate.params.limit}</dd>
        <dt>참여 회사</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {candidate.operator_slugs && candidate.operator_slugs.length > 0
            ? `${candidate.operator_slugs.length}개`
            : "전체"}
        </dd>
        <dt>operator_id_scope_ready</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {boolValue(candidate.operator_id_scope_ready)}
        </dd>
        <dt>run_allowed</dt>
        <dd className="text-right text-[var(--color-fg)]">{boolValue(candidate.run_allowed)}</dd>
        <dt>blocked_by_warnings</dt>
        <dd className="text-right text-[var(--color-fg)]">
          {blockedWarnings.length > 0 ? blockedWarnings.join(", ") : "none"}
        </dd>
      </dl>
      {unresolvedTargets.length > 0 ? (
        <section
          aria-label="unresolved operator targets"
          className="rounded-md border border-[var(--color-border)] px-2 py-1"
        >
          <h4 className="mb-1 text-[var(--color-muted)]">미해결 operator targets</h4>
          <ul className="flex flex-col gap-1">
            {unresolvedTargets.map((target) => (
              <li
                key={target.slug}
                className="flex flex-wrap items-center justify-between gap-2 text-[var(--color-fg)]"
              >
                <span>{target.slug}</span>
                <span className="text-[var(--color-muted)]">
                  {target.username ?? "username unknown"} · operator_id{" "}
                  {target.operator_id ?? "unresolved"}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      <div className="flex flex-wrap gap-2">
        {candidate.preset_name && !candidate.experiment_id ? (
          <Button
            type="button"
            size="sm"
            onClick={() => onSavePreset(candidate.preset_name as string)}
            disabled={saving || actionBlocked}
          >
            {saving ? "저장 중…" : "Preset 저장"}
          </Button>
        ) : null}
        {candidate.experiment_id ? (
          <Button
            type="button"
            size="sm"
            onClick={() => onSelectExperiment(candidate.experiment_id as number)}
            disabled={selecting || actionBlocked}
          >
            {selecting ? "선택 중…" : "기존 실험 선택"}
          </Button>
        ) : null}
        {actionBlocked ? (
          <Button type="button" size="sm" variant="outline" disabled>
            경고 확인 필요
          </Button>
        ) : null}
      </div>
    </div>
  );
}

function PresetPanel({
  presets,
  loading,
  saving,
  savingName,
  onSave
}: {
  presets: SyntheticExperimentPreset[];
  loading: boolean;
  saving: boolean;
  savingName: string | null;
  onSave: (name: string) => void;
}) {
  return (
    <Card aria-label="G-1 preset">
      <CardHeader>
        <CardTitle>G-1 preset</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        {loading ? <p className="text-[var(--color-muted)]">불러오는 중…</p> : null}
        {presets.map((preset) => (
          <div
            key={preset.name}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-[var(--color-border)] px-2 py-1.5"
          >
            <span className="flex min-w-0 flex-col">
              <span className="truncate font-medium text-[var(--color-fg)]">{preset.name}</span>
              <span className="truncate text-[var(--color-muted)]">
                {preset.params.category ?? "전체"} · limit {preset.params.limit}
              </span>
            </span>
            <span className="flex items-center gap-2">
              {preset.latest_run_status ? (
                <Badge tone={preset.latest_run_status === "completed" ? "healthy" : "info"}>
                  {preset.latest_run_status}
                </Badge>
              ) : preset.experiment_id ? (
                <Badge tone="muted">saved</Badge>
              ) : null}
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => onSave(preset.name)}
                disabled={saving}
              >
                {saving && savingName === preset.name ? "저장 중…" : "저장"}
              </Button>
            </span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
