import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { LabeledStat } from "@/shared/components";
import { formatPercent } from "@/shared/lib";
import type { OperationsDashboardResponse } from "@/shared/types/operations";
import { TONE } from "./helpers";

export function TelegramHealth({ summary }: { summary: OperationsDashboardResponse["notifications"] }) {
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="break-words leading-snug">텔레그램 / 알림</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[summary.telegram_status]}>
          {summary.telegram_status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <LabeledStat label="전송 시도" value={summary.telegram_delivery_attempt_count.toString()} />
          <LabeledStat label="성공" value={summary.telegram_sent_count.toString()} />
          <LabeledStat label="실패" value={summary.telegram_failed_count.toString()} />
          <LabeledStat label="성공률" value={formatPercent(summary.telegram_success_rate)} />
        </div>
        <p className="break-words text-[var(--color-muted)]">{summary.telegram_detail}</p>
      </CardContent>
    </Card>
  );
}
