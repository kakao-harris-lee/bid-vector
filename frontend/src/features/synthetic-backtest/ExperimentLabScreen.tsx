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
import { Card, CardContent, toastApi } from "@/shared/components/ui";
import type {
  SyntheticExperimentResponse,
  SyntheticExperimentRunResponse,
  SyntheticExperimentSampleGapItem,
  SyntheticExperimentSampleGapRunCandidateResponse
} from "@/shared/types/synthetic";
import { SyntheticBacktestScreen } from "./SyntheticBacktestScreen";
import { ExperimentForm } from "./ExperimentForm";
import { ExperimentList } from "./ExperimentList";
import { ExperimentRunProgress } from "./ExperimentRunProgress";
import { RunComparePanel } from "./RunComparePanel";
import { CustomOperatorManager } from "./CustomOperatorManager";
import { PresetPanel, SampleGapCandidatePanel, SelectedExperimentCard } from "./components";

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
              <SelectedExperimentCard
                experiment={selected}
                running={runMutation.isPending}
                onRun={() => runMutation.mutate(selected.id)}
              />
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
