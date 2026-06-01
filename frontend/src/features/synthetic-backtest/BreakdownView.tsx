import { useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type {
  SyntheticBudgetBandBreakdownItem,
  SyntheticCategoryBreakdownItem,
  SyntheticExperimentResultItem
} from "@/shared/types/synthetic";

export interface BreakdownViewProps {
  /** 완료된 run의 회사별 결과(각 항목에 breakdown 포함). */
  results: SyntheticExperimentResultItem[];
}

/** 예산구간 키 → 한글 라벨 매핑. 알 수 없는 키는 키 그대로 표기. */
const BUDGET_BAND_LABELS: Record<string, string> = {
  lt_1eok: "1억 미만",
  "1eok_5eok": "1–5억",
  "5eok_10eok": "5–10억",
  "10eok_50eok": "10–50억",
  gte_50eok: "50억+"
};

/** 예산구간 정렬 순서(낮은 구간 → 높은 구간). */
const BUDGET_BAND_ORDER = ["lt_1eok", "1eok_5eok", "5eok_10eok", "10eok_50eok", "gte_50eok"];

function budgetBandLabel(band: string): string {
  return BUDGET_BAND_LABELS[band] ?? band;
}

function budgetBandRank(band: string): number {
  const index = BUDGET_BAND_ORDER.indexOf(band);
  return index === -1 ? BUDGET_BAND_ORDER.length : index;
}

const ALL_OPERATORS = "__all__";

export function BreakdownView({ results }: BreakdownViewProps) {
  const [selected, setSelected] = useState<string>(ALL_OPERATORS);

  const slugs = useMemo(
    () => results.map((result) => result.operator_slug).filter(Boolean),
    [results]
  );

  // 선택된 회사(또는 전체 집계)의 카테고리/예산구간 분해를 계산한다.
  const { categories, bands } = useMemo(() => {
    const source =
      selected === ALL_OPERATORS
        ? results
        : results.filter((result) => result.operator_slug === selected);
    return {
      categories: aggregateCategory(source),
      bands: aggregateBudgetBand(source)
    };
  }, [results, selected]);

  const hasData = categories.length > 0 || bands.length > 0;

  return (
    <Card aria-label="분해 시각화">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>카테고리 · 예산구간 분해</CardTitle>
        {slugs.length > 0 ? (
          <label className="flex items-center gap-2 text-xs">
            회사
            <select
              value={selected}
              onChange={(event) => setSelected(event.target.value)}
              aria-label="분해 대상 회사"
              className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
            >
              <option value={ALL_OPERATORS}>전체 집계</option>
              {slugs.map((slug) => (
                <option key={slug} value={slug}>
                  {slug}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-xs">
        <p className="text-[var(--color-muted)]">
          win_rate는 가격 기준 추정 낙찰. 정산 건수가 적으면 신뢰도가 낮습니다.
        </p>

        {!hasData ? (
          <p className="py-4 text-center text-[var(--color-muted)]">
            분해할 정산 데이터가 없습니다.
          </p>
        ) : (
          <>
            <BreakdownSection
              title="카테고리별"
              rowLabel="카테고리"
              rows={categories.map((item) => ({
                key: item.category,
                label: item.category || "(미분류)",
                settledCount: item.settled_count,
                winRate: item.win_rate ?? null,
                avgError: item.avg_abs_bid_rate_error ?? null
              }))}
              emptyMessage="카테고리별 정산 데이터가 없습니다."
            />
            <BreakdownSection
              title="예산구간별"
              rowLabel="예산구간"
              rows={bands.map((item) => ({
                key: item.budget_band,
                label: budgetBandLabel(item.budget_band),
                settledCount: item.settled_count,
                winRate: item.win_rate ?? null,
                avgError: item.avg_abs_bid_rate_error ?? null
              }))}
              emptyMessage="예산구간별 정산 데이터가 없습니다."
            />
          </>
        )}
      </CardContent>
    </Card>
  );
}

interface BreakdownRow {
  key: string;
  label: string;
  settledCount: number;
  winRate: number | null;
  avgError: number | null;
}

function BreakdownSection({
  title,
  rowLabel,
  rows,
  emptyMessage
}: {
  title: string;
  rowLabel: string;
  rows: BreakdownRow[];
  emptyMessage: string;
}) {
  return (
    <section aria-label={title}>
      <h3 className="mb-1 font-medium text-[var(--color-fg)]">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-[var(--color-muted)]">{emptyMessage}</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                <th className="py-1">{rowLabel}</th>
                <th className="py-1">추정 승률</th>
                <th className="py-1 text-right">평균 |오차|</th>
                <th className="py-1 text-right">정산 건수</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.key}
                  aria-label={`${row.label} 분해`}
                  className="border-b border-[var(--color-border)]/60"
                >
                  <td className="py-1 pr-2 text-[var(--color-fg)]">{row.label}</td>
                  <td className="py-1 pr-2">
                    <MiniBar value={row.winRate} />
                  </td>
                  <td className="py-1 text-right tabular-nums">{formatNullablePercent(row.avgError)}</td>
                  <td className="py-1 text-right tabular-nums">{row.settledCount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** win_rate를 0~1 비율 미니 막대 + 라벨로 표기. null이면 "—". */
function MiniBar({ value }: { value: number | null }) {
  if (value === null) {
    return <span className="text-[var(--color-muted)]">—</span>;
  }
  const widthPct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <span className="flex items-center gap-2">
      <span
        className="h-2 w-20 overflow-hidden rounded-full bg-[var(--color-secondary)]"
        aria-hidden="true"
      >
        <span
          className="block h-full rounded-full bg-[var(--color-primary)]"
          style={{ width: `${widthPct}%` }}
        />
      </span>
      <span className="tabular-nums text-[var(--color-fg)]">{formatPercent(value)}</span>
    </span>
  );
}

function formatNullablePercent(value: number | null): string {
  if (value === null) return "—";
  return formatPercent(value);
}

// --- 집계 헬퍼 ---------------------------------------------------------------
// 전체 집계 시 여러 회사의 같은 키(카테고리/예산구간) 항목을 합산한다.
// win_rate / avg_abs_bid_rate_error는 settled_count 가중 평균으로 재계산한다.

interface Accumulator {
  settled: number;
  won: number;
  errorWeightedSum: number;
  errorWeight: number;
}

function emptyAccumulator(): Accumulator {
  return { settled: 0, won: 0, errorWeightedSum: 0, errorWeight: 0 };
}

function accumulate(
  acc: Accumulator,
  settledCount: number,
  wouldHaveWon: number,
  avgError: number | null | undefined
): void {
  acc.settled += settledCount;
  acc.won += wouldHaveWon;
  if (typeof avgError === "number" && settledCount > 0) {
    acc.errorWeightedSum += avgError * settledCount;
    acc.errorWeight += settledCount;
  }
}

function finalizeWinRate(acc: Accumulator): number | null {
  return acc.settled > 0 ? acc.won / acc.settled : null;
}

function finalizeAvgError(acc: Accumulator): number | null {
  return acc.errorWeight > 0 ? acc.errorWeightedSum / acc.errorWeight : null;
}

function aggregateCategory(
  results: SyntheticExperimentResultItem[]
): SyntheticCategoryBreakdownItem[] {
  const map = new Map<string, Accumulator>();
  for (const result of results) {
    for (const item of result.breakdown?.by_category ?? []) {
      const acc = map.get(item.category) ?? emptyAccumulator();
      accumulate(acc, item.settled_count, item.would_have_won_count, item.avg_abs_bid_rate_error);
      map.set(item.category, acc);
    }
  }
  return [...map.entries()]
    .map(([category, acc]) => ({
      category,
      settled_count: acc.settled,
      would_have_won_count: acc.won,
      win_rate: finalizeWinRate(acc),
      avg_abs_bid_rate_error: finalizeAvgError(acc)
    }))
    .sort((a, b) => b.settled_count - a.settled_count);
}

function aggregateBudgetBand(
  results: SyntheticExperimentResultItem[]
): SyntheticBudgetBandBreakdownItem[] {
  const map = new Map<string, Accumulator>();
  for (const result of results) {
    for (const item of result.breakdown?.by_budget_band ?? []) {
      const acc = map.get(item.budget_band) ?? emptyAccumulator();
      accumulate(acc, item.settled_count, item.would_have_won_count, item.avg_abs_bid_rate_error);
      map.set(item.budget_band, acc);
    }
  }
  return [...map.entries()]
    .map(([budget_band, acc]) => ({
      budget_band,
      settled_count: acc.settled,
      would_have_won_count: acc.won,
      win_rate: finalizeWinRate(acc),
      avg_abs_bid_rate_error: finalizeAvgError(acc)
    }))
    .sort((a, b) => budgetBandRank(a.budget_band) - budgetBandRank(b.budget_band));
}
