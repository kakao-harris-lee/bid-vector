import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type { SyntheticBacktestOperatorResult } from "@/shared/types/synthetic";

export function WinRateBarChart({ results }: { results: SyntheticBacktestOperatorResult[] }) {
  const data = results
    .map((row) => ({
      name: row.display_name,
      slug: row.slug,
      winRate: row.win_rate_on_settled,
      submissionRate: row.bid_submission_rate
    }))
    .filter((row) => row.winRate !== null && row.winRate !== undefined);

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>승률 차트</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-xs text-[var(--color-muted)]">
            정산된 settled rows가 없어 승률 차트를 그릴 수 없습니다.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>아키타입별 승률 (가격 기준 추정)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="slug" />
              <YAxis domain={[0, 1]} tickFormatter={(value) => formatPercent(value)} />
              <Tooltip formatter={(value) => formatPercent(Number(value))} />
              <Bar dataKey="winRate" name="win_rate_on_settled">
                {data.map((row, index) => (
                  <Cell key={row.slug} fill="var(--color-primary)" fillOpacity={1 - index * 0.04} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
