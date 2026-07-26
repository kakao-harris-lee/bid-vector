import { formatPercent } from "@/shared/lib";
import type {
  SyntheticExperimentCompareDelta,
  SyntheticExperimentCompareRunHeader,
  SyntheticExperimentCompareSide,
  SyntheticExperimentResponse
} from "@/shared/types/synthetic";

/** 드롭다운 한 항목: 완료된 런 + 소속 실험. */
export interface RunOption {
  runId: number;
  experimentId: number;
  experimentName: string;
  label: string;
}

export const TERMINAL_DONE = "completed";

/** delta 키별 의미와 "값이 클수록 좋은지" 방향. 오차는 작을수록 좋다. */
export const DELTA_FIELDS: {
  key: keyof SyntheticExperimentCompareDelta;
  label: string;
  higherIsBetter: boolean;
}[] = [
  { key: "win_rate_on_settled", label: "추정 승률", higherIsBetter: true },
  { key: "bid_submission_rate", label: "투찰률", higherIsBetter: true },
  { key: "average_absolute_bid_rate_error", label: "평균 |오차|", higherIsBetter: false }
];

/** win_rate 등 null이면 em dash로. (formatPercent는 null→"-"이라 별도 처리.) */
export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return formatPercent(value);
}

export function sideValue(
  side: SyntheticExperimentCompareSide,
  key: keyof SyntheticExperimentCompareDelta
): number | null | undefined {
  return side[key];
}

/** delta 부호 + 방향으로 색상 톤을 정한다(개선=초록, 악화=빨강, 0/null=중립). */
export function deltaToneClass(
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
export function formatDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${(delta * 100).toFixed(1)}%p`;
}

export function summaryField(
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
export function buildRunOptions(experiments: SyntheticExperimentResponse[]): RunOption[] {
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
