import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type { SyntheticBacktestOperatorResult } from "@/shared/types/synthetic";

export type SortKey = "win_rate_on_settled" | "bid_submission_rate" | "average_absolute_bid_rate_error";

export function ComparisonTable({
  results,
  sortKey,
  onSortKeyChange,
  onSelect
}: {
  results: SyntheticBacktestOperatorResult[];
  sortKey: SortKey;
  onSortKeyChange: (key: SortKey) => void;
  onSelect?: (row: SyntheticBacktestOperatorResult) => void;
}) {
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>비교 결과</CardTitle>
        <label className="flex items-center gap-2 text-xs">
          정렬
          <select
            value={sortKey}
            onChange={(event) => onSortKeyChange(event.target.value as SortKey)}
            aria-label="정렬 기준"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            <option value="win_rate_on_settled">win_rate_on_settled</option>
            <option value="bid_submission_rate">bid_submission_rate</option>
            <option value="average_absolute_bid_rate_error">average_absolute_bid_rate_error</option>
          </select>
        </label>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                <th className="py-1">운영자</th>
                <th className="py-1 text-right">candidates</th>
                <th className="py-1 text-right">settled</th>
                <th className="py-1 text-right">win_rate</th>
                <th className="py-1 text-right">submission_rate</th>
                <th className="py-1 text-right">avg |err|</th>
              </tr>
            </thead>
            <tbody>
              {results.map((row) => (
                <tr
                  key={row.user_id}
                  className="cursor-pointer border-b border-[var(--color-border)]/60 hover:bg-[var(--color-secondary)]"
                  onClick={() => onSelect?.(row)}
                  aria-label={`${row.display_name} 드릴다운`}
                >
                  <td className="py-1 pr-2">
                    <div className="flex flex-col">
                      <span className="font-medium text-[var(--color-fg)]">{row.display_name}</span>
                      <span className="text-[var(--color-muted)]">{row.slug}</span>
                    </div>
                  </td>
                  <td className="py-1 text-right tabular-nums">{row.candidate_count}</td>
                  <td className="py-1 text-right tabular-nums">{row.settled_count}</td>
                  <td className="py-1 text-right tabular-nums">{formatPercent(row.win_rate_on_settled)}</td>
                  <td className="py-1 text-right tabular-nums">{formatPercent(row.bid_submission_rate)}</td>
                  <td className="py-1 text-right tabular-nums">
                    {formatPercent(row.average_absolute_bid_rate_error)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
