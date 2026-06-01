import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { compareExperimentRuns, experimentRunCsvUrl, fetchExperiments } from "@/shared/api";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type {
  SyntheticExperimentCompareDelta,
  SyntheticExperimentCompareOperator,
  SyntheticExperimentCompareRunHeader,
  SyntheticExperimentCompareSide,
  SyntheticExperimentResponse
} from "@/shared/types/synthetic";

export interface RunComparePanelProps {
  /** 운영자 세션 토큰. */
  token?: string | null;
}

/** 드롭다운 한 항목: 완료된 런 + 소속 실험. */
interface RunOption {
  runId: number;
  experimentId: number;
  experimentName: string;
  label: string;
}

const TERMINAL_DONE = "completed";

/** delta 키별 의미와 "값이 클수록 좋은지" 방향. 오차는 작을수록 좋다. */
const DELTA_FIELDS: {
  key: keyof SyntheticExperimentCompareDelta;
  label: string;
  higherIsBetter: boolean;
}[] = [
  { key: "win_rate_on_settled", label: "추정 승률", higherIsBetter: true },
  { key: "bid_submission_rate", label: "투찰률", higherIsBetter: true },
  { key: "average_absolute_bid_rate_error", label: "평균 |오차|", higherIsBetter: false }
];

/** win_rate 등 null이면 em dash로. (formatPercent는 null→"-"이라 별도 처리.) */
function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return formatPercent(value);
}

function sideValue(
  side: SyntheticExperimentCompareSide,
  key: keyof SyntheticExperimentCompareDelta
): number | null | undefined {
  return side[key];
}

/** delta 부호 + 방향으로 색상 톤을 정한다(개선=초록, 악화=빨강, 0/null=중립). */
function deltaToneClass(
  delta: number | null | undefined,
  higherIsBetter: boolean
): string {
  if (delta === null || delta === undefined || delta === 0) {
    return "text-[var(--color-muted)]";
  }
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  return improved
    ? "text-[color-mix(in_oklch,var(--color-success),black_15%)]"
    : "text-[color-mix(in_oklch,var(--color-danger),black_10%)]";
}

/** delta를 +/-부호 포함 퍼센트 문자열로. null이면 null(호출부에서 "비교불가" 처리). */
function formatDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${(delta * 100).toFixed(1)}%p`;
}

function summaryField(
  header: SyntheticExperimentCompareRunHeader,
  key: string
): string {
  const summary = header.summary;
  if (!summary) return "—";
  const value = summary[key];
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

/** 완료된 런만 옵션으로 평탄화하고 런 id 내림차순(최신 우선)으로 정렬한다. */
function buildRunOptions(experiments: SyntheticExperimentResponse[]): RunOption[] {
  const options: RunOption[] = [];
  for (const experiment of experiments) {
    for (const run of experiment.runs ?? []) {
      if (run.status !== TERMINAL_DONE) continue;
      options.push({
        runId: run.id,
        experimentId: experiment.id,
        experimentName: experiment.name,
        label: `#${run.id} · ${experiment.name}`
      });
    }
  }
  return options.sort((a, b) => b.runId - a.runId);
}

function CsvDownloadButton({
  option,
  side
}: {
  option: RunOption | null;
  side: "A" | "B";
}) {
  if (!option) return null;
  return (
    <a
      href={experimentRunCsvUrl(option.experimentId, option.runId)}
      download
      className="inline-flex h-8 items-center rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-xs font-medium text-[var(--color-fg)] transition-colors hover:bg-[var(--color-secondary)]"
      aria-label={`${side} 런 CSV 다운로드`}
    >
      {side} CSV 다운로드
    </a>
  );
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
            <CsvDownloadButton
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
            <CsvDownloadButton
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
            <RunHeaders runA={data.run_a} runB={data.run_b} />

            {operators.length === 0 ? (
              <p className="py-2 text-center text-[var(--color-muted)]">
                두 런에 공통으로 존재하는 회사가 없습니다.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                      <th className="py-1">회사(slug)</th>
                      {DELTA_FIELDS.map((field) => (
                        <th key={field.key} className="py-1 text-right" colSpan={3}>
                          {field.label}
                        </th>
                      ))}
                    </tr>
                    <tr className="border-b border-[var(--color-border)] text-right text-[10px] text-[var(--color-muted)]">
                      <th className="py-1 text-left" />
                      {DELTA_FIELDS.map((field) => (
                        <SubHeaderCells key={field.key} />
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {operators.map((operator) => (
                      <OperatorRow key={operator.operator_slug} operator={operator} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <OnlyInLists onlyInA={onlyInA} onlyInB={onlyInB} />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SubHeaderCells() {
  return (
    <>
      <th className="py-1 text-right font-normal">A</th>
      <th className="py-1 text-right font-normal">B</th>
      <th className="py-1 text-right font-normal">Δ</th>
    </>
  );
}

function RunHeaders({
  runA,
  runB
}: {
  runA: SyntheticExperimentCompareRunHeader;
  runB: SyntheticExperimentCompareRunHeader;
}) {
  return (
    <dl className="grid grid-cols-2 gap-3" aria-label="비교 런 요약">
      {(
        [
          { side: "A", header: runA },
          { side: "B", header: runB }
        ] as const
      ).map(({ side, header }) => (
        <div
          key={side}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge tone={side === "A" ? "muted" : "info"}>런 {side}</Badge>
            <span className="font-medium text-[var(--color-fg)]">
              #{header.id} (실험 {header.experiment_id})
            </span>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[var(--color-muted)]">
            <span>시나리오 {summaryField(header, "scenario")}</span>
            <span>limit {summaryField(header, "limit")}</span>
          </div>
        </div>
      ))}
    </dl>
  );
}

function OperatorRow({ operator }: { operator: SyntheticExperimentCompareOperator }) {
  return (
    <tr
      className="border-b border-[var(--color-border)]/60"
      aria-label={`${operator.operator_slug} 비교 행`}
    >
      <td className="py-1 pr-2 font-medium text-[var(--color-fg)]">
        {operator.operator_slug}
      </td>
      {DELTA_FIELDS.map((field) => {
        const deltaValue = operator.delta[field.key];
        const deltaText = formatDelta(deltaValue);
        return (
          <DeltaCells
            key={field.key}
            aValue={sideValue(operator.a, field.key)}
            bValue={sideValue(operator.b, field.key)}
            deltaText={deltaText}
            toneClass={deltaToneClass(deltaValue, field.higherIsBetter)}
          />
        );
      })}
    </tr>
  );
}

function DeltaCells({
  aValue,
  bValue,
  deltaText,
  toneClass
}: {
  aValue: number | null | undefined;
  bValue: number | null | undefined;
  deltaText: string | null;
  toneClass: string;
}) {
  return (
    <>
      <td className="py-1 text-right tabular-nums">{pct(aValue)}</td>
      <td className="py-1 text-right tabular-nums">{pct(bValue)}</td>
      <td className="py-1 text-right tabular-nums">
        {deltaText === null ? (
          <Badge tone="muted">비교불가</Badge>
        ) : (
          <span className={`font-medium ${toneClass}`}>{deltaText}</span>
        )}
      </td>
    </>
  );
}

function OnlyInLists({
  onlyInA,
  onlyInB
}: {
  onlyInA: string[];
  onlyInB: string[];
}) {
  if (onlyInA.length === 0 && onlyInB.length === 0) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <OnlyInList side="A" slugs={onlyInA} />
      <OnlyInList side="B" slugs={onlyInB} />
    </div>
  );
}

function OnlyInList({ side, slugs }: { side: "A" | "B"; slugs: string[] }) {
  return (
    <div
      className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
      aria-label={`런 ${side}에만 있는 회사`}
    >
      <p className="mb-1 font-medium text-[var(--color-fg)]">런 {side}에만 있는 회사</p>
      {slugs.length === 0 ? (
        <p className="text-[var(--color-muted)]">없음</p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {slugs.map((slug) => (
            <li key={slug}>
              <Badge tone={side === "A" ? "muted" : "info"}>{slug}</Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
