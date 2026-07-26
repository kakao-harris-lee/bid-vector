import { Badge, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type { OperationsDashboardCard } from "@/shared/types/operations";
import { TONE } from "./helpers";

export function SummaryCard({ card }: { card: OperationsDashboardCard }) {
  const valueLabel =
    card.unit === "ratio" ? formatPercent(card.value) : card.value.toLocaleString("ko-KR");
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-2">
        <CardTitle className="min-w-0 break-words text-sm leading-snug">{card.label}</CardTitle>
        <Badge className="shrink-0 whitespace-nowrap" tone={TONE[card.status]}>
          {card.status}
        </Badge>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <strong className="text-2xl tabular-nums text-[var(--color-fg)]">{valueLabel}</strong>
        <span className="break-words text-xs text-[var(--color-muted)]">{card.detail}</span>
      </CardContent>
    </Card>
  );
}
