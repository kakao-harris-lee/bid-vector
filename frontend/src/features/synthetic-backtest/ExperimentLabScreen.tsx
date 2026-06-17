import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useShellContext } from "@/app/dashboardContext";
import {
  ensureExperimentPreset,
  fetchExperimentPresets,
  triggerExperimentRun
} from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, toastApi } from "@/shared/components/ui";
import { formatDateTime } from "@/shared/lib";
import type {
  SyntheticExperimentPreset,
  SyntheticExperimentResponse,
  SyntheticExperimentRunResponse
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

  const presets = useQuery({
    queryKey: ["synthetic", "experiments", "presets"],
    queryFn: () => fetchExperimentPresets(token),
    enabled: Boolean(token)
  });

  const presetMutation = useMutation<SyntheticExperimentResponse, Error, string>({
    mutationFn: (presetName) => ensureExperimentPreset(presetName, token),
    onSuccess: (experiment) => {
      setSelected(experiment);
      setActiveRunId(null);
      void queryClient.invalidateQueries({ queryKey: ["synthetic", "experiments"] });
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
