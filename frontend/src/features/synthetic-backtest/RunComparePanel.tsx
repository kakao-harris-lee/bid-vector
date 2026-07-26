import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { compareExperimentRuns, fetchExperiments } from "@/shared/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { buildRunOptions, type RunOption } from "./runCompare.helpers";
import {
  RunCompareCsvButton,
  RunCompareHeaders,
  RunCompareOnlyLists,
  RunCompareTable
} from "./components";

export interface RunComparePanelProps {
  /** 운영자 세션 토큰. */
  token?: string | null;
}

export function RunComparePanel({ token }: RunComparePanelProps) {
  const [runA, setRunA] = useState<number | null>(null);
  const [runB, setRunB] = useState<number | null>(null);

  const experiments = useQuery({
    queryKey: ["synthetic", "experiments"],
    queryFn: () => fetchExperiments(token),
    enabled: Boolean(token)
  });

  const options = useMemo(
    () => buildRunOptions(experiments.data ?? []),
    [experiments.data]
  );

  const optionByRun = useMemo(() => {
    const map = new Map<number, RunOption>();
    for (const option of options) map.set(option.runId, option);
    return map;
  }, [options]);

  const bothSelected = runA != null && runB != null;

  const compare = useQuery({
    queryKey: ["synthetic", "experiments", "compare", runA, runB],
    queryFn: () => compareExperimentRuns(runA as number, runB as number, token),
    enabled: bothSelected
  });

  const data = compare.data;
  const operators = data?.operators ?? [];
  const onlyInA = data?.only_in_a ?? [];
  const onlyInB = data?.only_in_b ?? [];

  const handleSelect = (value: string, setter: (id: number | null) => void) => {
    setter(value === "" ? null : Number(value));
  };

  return (
    <Card aria-label="실험 런 A/B 비교">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-2">
        <CardTitle>실험 런 A/B 비교</CardTitle>
        <span className="text-xs text-[var(--color-muted)]">
          Δ = B − A (양수=B 높음). 추정 승률은 가격 기준 추정 낙찰.
        </span>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-xs">
        {experiments.isLoading ? (
          <p className="text-[var(--color-muted)]">완료된 런을 불러오는 중…</p>
        ) : null}

        {!experiments.isLoading && options.length < 2 ? (
          <p className="text-[var(--color-muted)]">
            비교하려면 완료된 런이 2개 이상 필요합니다. "실험" 탭에서 실험을 실행하세요.
          </p>
        ) : null}

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="font-medium text-[var(--color-fg)]">런 A (기준)</span>
            <select
              aria-label="런 A 선택"
              value={runA == null ? "" : String(runA)}
              onChange={(event) => handleSelect(event.target.value, setRunA)}
              className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
            >
              <option value="">선택…</option>
              {options.map((option) => (
                <option key={option.runId} value={String(option.runId)}>
                  {option.label}
                </option>
              ))}
            </select>
            <RunCompareCsvButton
              option={runA == null ? null : optionByRun.get(runA) ?? null}
              side="A"
            />
          </label>

          <label className="flex flex-col gap-1">
            <span className="font-medium text-[var(--color-fg)]">런 B (후보)</span>
            <select
              aria-label="런 B 선택"
              value={runB == null ? "" : String(runB)}
              onChange={(event) => handleSelect(event.target.value, setRunB)}
              className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
            >
              <option value="">선택…</option>
              {options.map((option) => (
                <option key={option.runId} value={String(option.runId)}>
                  {option.label}
                </option>
              ))}
            </select>
            <RunCompareCsvButton
              option={runB == null ? null : optionByRun.get(runB) ?? null}
              side="B"
            />
          </label>
        </div>

        {runA != null && runB != null && runA === runB ? (
          <p className="text-[color-mix(in_oklch,var(--color-warn),black_30%)]">
            같은 런을 선택했습니다. 서로 다른 런을 골라야 비교가 의미 있습니다.
          </p>
        ) : null}

        {bothSelected && compare.isLoading ? (
          <p className="text-[var(--color-muted)]" role="status">
            비교 결과를 불러오는 중…
          </p>
        ) : null}

        {bothSelected && compare.isError ? (
          <div
            role="alert"
            className="rounded-md border border-[var(--color-danger)] bg-[color-mix(in_oklch,var(--color-danger),white_80%)] p-3"
          >
            <p className="text-[color-mix(in_oklch,var(--color-danger),black_30%)]">
              {compare.error instanceof Error
                ? compare.error.message
                : "비교 결과를 불러오지 못했습니다."}
            </p>
          </div>
        ) : null}

        {data ? (
          <>
            <RunCompareHeaders runA={data.run_a} runB={data.run_b} />

            <RunCompareTable operators={operators} />

            <RunCompareOnlyLists onlyInA={onlyInA} onlyInB={onlyInB} />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
