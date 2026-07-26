import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import type { OperationsDashboardResponse } from "@/shared/types/operations";
import { TONE } from "./helpers";

export function MlReleaseCard({ summary }: { summary: OperationsDashboardResponse["ml_release"] }) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">ML release</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.status]}>
          {summary.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <p className="break-words text-[var(--color-fg)]">{summary.detail}</p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <LabeledStat label="latest_tag" value={summary.latest_release_tag ?? "-"} />
          <LabeledStat label="signature" value={summary.latest_signature_status} />
          <LabeledStat label="gate" value={summary.latest_gate_status} />
        </div>
        <p className="break-words text-[var(--color-muted)]">backtest: {summary.backtest_detail}</p>
      </CardContent>
    </Card>
  );
}
