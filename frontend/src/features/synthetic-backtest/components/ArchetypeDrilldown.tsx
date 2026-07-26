import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatCurrencyCompact, formatPercent } from "@/shared/lib";
import type { SyntheticBacktestOperatorResult } from "@/shared/types/synthetic";

export function ArchetypeDrilldown({
  row,
  onClose
}: {
  row: SyntheticBacktestOperatorResult;
  onClose: () => void;
}) {
  const items = row.settlement_items;
  const errorBuckets = useMemo(() => bucketizeBidRateErrors(items), [items]);

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>
          {row.display_name} 드릴다운
          <span className="ml-2 text-xs font-normal text-[var(--color-muted)]">
            ({row.slug} · 샘플 {row.settlement_sample_count}/settled {row.settled_count})
          </span>
        </CardTitle>
        <Button type="button" variant="ghost" size="sm" onClick={onClose}>
          닫기
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-xs">
        {items.length === 0 ? (
          <p className="text-[var(--color-muted)]">
            정산된 settlement row가 없어 드릴다운할 데이터가 없습니다.
          </p>
        ) : (
          <>
            <section>
              <h3 className="mb-1 text-[var(--color-fg)]">정산 샘플</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-muted)]">
                      <th className="py-1">공고</th>
                      <th className="py-1 text-right">투찰가</th>
                      <th className="py-1 text-right">낙찰가</th>
                      <th className="py-1 text-right">|err|</th>
                      <th className="py-1 text-right">결과</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr
                        key={item.paper_bid_id ?? `${item.project_id}-${item.settled_at}`}
                        className="border-b border-[var(--color-border)]/60"
                      >
                        <td className="py-1 pr-2">
                          <span className="block truncate text-[var(--color-fg)]" title={item.project_title}>
                            {item.project_title || "—"}
                          </span>
                          <span className="text-[var(--color-muted)]">{item.category ?? "-"}</span>
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {item.bid_amount != null ? formatCurrencyCompact(item.bid_amount) : "-"}
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {item.winning_amount != null ? formatCurrencyCompact(item.winning_amount) : "-"}
                        </td>
                        <td className="py-1 text-right tabular-nums">
                          {formatPercent(item.absolute_bid_rate_error)}
                        </td>
                        <td className="py-1 text-right">
                          <Badge tone={item.would_have_won ? "healthy" : "muted"}>
                            {item.would_have_won ? "추정 낙찰" : "추정 실패"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section>
              <h3 className="mb-1 text-[var(--color-fg)]">|bid_rate_error| 분포</h3>
              <div className="h-40">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={errorBuckets}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="label" />
                    <YAxis allowDecimals={false} />
                    <Tooltip />
                    <Bar dataKey="count" fill="var(--color-warn)" />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>
          </>
        )}
      </CardContent>
    </Card>
  );
}

function bucketizeBidRateErrors(items: SyntheticBacktestOperatorResult["settlement_items"]) {
  const buckets = [
    { label: "0–1%", min: 0, max: 0.01, count: 0 },
    { label: "1–3%", min: 0.01, max: 0.03, count: 0 },
    { label: "3–5%", min: 0.03, max: 0.05, count: 0 },
    { label: "5–10%", min: 0.05, max: 0.1, count: 0 },
    { label: "10%+", min: 0.1, max: Infinity, count: 0 }
  ];
  for (const item of items) {
    const err = item.absolute_bid_rate_error;
    if (err === null || err === undefined) continue;
    const bucket = buckets.find((b) => err >= b.min && err < b.max);
    if (bucket) bucket.count += 1;
  }
  return buckets;
}
