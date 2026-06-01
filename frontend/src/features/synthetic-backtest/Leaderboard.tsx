import { useMemo, useState } from "react";
import { Badge, type BadgeTone, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type {
  SyntheticExperimentMetrics,
  SyntheticExperimentResultItem
} from "@/shared/types/synthetic";

/** 리더보드 정렬 기준. */
export type LeaderboardSortKey =
  | "win_rate_on_settled"
  | "bid_submission_rate"
  | "average_absolute_bid_rate_error";

export interface LeaderboardProps {
  /** 완료된 run의 회사별 결과. */
  results: SyntheticExperimentResultItem[];
  /** 초기 정렬 기준. 기본 win_rate_on_settled. */
  initialSortKey?: LeaderboardSortKey;
}

const SORT_OPTIONS: { key: LeaderboardSortKey; label: string }[] = [
  { key: "win_rate_on_settled", label: "추정 승률" },
  { key: "bid_submission_rate", label: "투찰률" },
  { key: "average_absolute_bid_rate_error", label: "평균 |오차|" }
];

/** 값이 클수록 좋은 지표(내림차순)인지 여부. 오차는 작을수록 좋다(오름차순). */
function isDescending(key: LeaderboardSortKey): boolean {
  return key !== "average_absolute_bid_rate_error";
}

function metricNumber(
  metrics: SyntheticExperimentMetrics | undefined,
  key: keyof SyntheticExperimentMetrics
): number | null {
  const value = metrics?.[key];
  return typeof value === "number" ? value : null;
}

/** null/undefined는 항상 뒤로 보내며, 그 외에는 방향에 따라 정렬한다. */
function compareForSort(
  a: number | null,
  b: number | null,
  descending: boolean
): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return descending ? b - a : a - b;
}

const MEDAL_TONE: Record<number, BadgeTone> = {
  0: "watch", // 금색 계열(경고 톤이 amber)
  1: "muted", // 은색
  2: "info" // 동색 대용
};

const MEDAL_LABEL: Record<number, string> = {
  0: "🥇 1위",
  1: "🥈 2위",
  2: "🥉 3위"
};

export function Leaderboard({ results, initialSortKey = "win_rate_on_settled" }: LeaderboardProps) {
  const [sortKey, setSortKey] = useState<LeaderboardSortKey>(initialSortKey);

  const ranked = useMemo(() => {
    const descending = isDescending(sortKey);
    return [...results].sort((a, b) =>
      compareForSort(metricNumber(a.metrics, sortKey), metricNumber(b.metrics, sortKey), descending)
    );
  }, [results, sortKey]);

  return (
    <Card aria-label="리더보드">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>리더보드</CardTitle>
        <label className="flex items-center gap-2 text-xs">
          정렬
          <select
            value={sortKey}
            onChange={(event) => setSortKey(event.target.value as LeaderboardSortKey)}
            aria-label="리더보드 정렬 기준"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        <p className="text-[var(--color-muted)]">
          추정 승률 = 가격 기준 추정 낙찰(would_have_won / settled). 실제 낙찰이 아님.
        </p>
        {ranked.length === 0 ? (
          <p className="py-4 text-center text-[var(--color-muted)]">
            랭킹을 표시할 결과 데이터가 없습니다.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                  <th className="py-1">순위</th>
                  <th className="py-1">회사(slug)</th>
                  <th className="py-1 text-right">추정 승률</th>
                  <th className="py-1 text-right">투찰률</th>
                  <th className="py-1 text-right">평균 |오차|</th>
                  <th className="py-1 text-right">정산 건수</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((result, index) => {
                  const slug = result.operator_slug ?? result.metrics?.operator_slug ?? "—";
                  const medalLabel = MEDAL_LABEL[index];
                  const isTopThree = index < 3;
                  return (
                    <tr
                      key={result.operator_slug ?? index}
                      aria-label={`${slug} 랭킹 ${index + 1}위`}
                      className={`border-b border-[var(--color-border)]/60 ${
                        isTopThree ? "bg-[color-mix(in_oklch,var(--color-primary),transparent_92%)]" : ""
                      }`}
                    >
                      <td className="py-1 pr-2">
                        {medalLabel ? (
                          <Badge tone={MEDAL_TONE[index]}>{medalLabel}</Badge>
                        ) : (
                          <span className="text-[var(--color-muted)]">{index + 1}</span>
                        )}
                      </td>
                      <td className="py-1 pr-2 font-medium text-[var(--color-fg)]">{slug}</td>
                      <td className="py-1 text-right tabular-nums">
                        {formatNullablePercent(metricNumber(result.metrics, "win_rate_on_settled"))}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {formatNullablePercent(metricNumber(result.metrics, "bid_submission_rate"))}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {formatNullablePercent(
                          metricNumber(result.metrics, "average_absolute_bid_rate_error")
                        )}
                      </td>
                      <td className="py-1 text-right tabular-nums">
                        {metricNumber(result.metrics, "settled_count") ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** win_rate 등 null이면 "—"(em dash)로 표기. formatPercent는 null→"-"라 별도 처리. */
function formatNullablePercent(value: number | null): string {
  if (value === null) return "—";
  return formatPercent(value);
}
