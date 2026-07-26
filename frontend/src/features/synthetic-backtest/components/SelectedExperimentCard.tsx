import { Button, Card, CardContent, CardHeader, CardTitle } from "@/shared/components/ui";
import { formatDateTime } from "@/shared/lib";
import type { SyntheticExperimentResponse } from "@/shared/types/synthetic";

export function SelectedExperimentCard({
  experiment,
  running,
  onRun
}: {
  experiment: SyntheticExperimentResponse;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <Card aria-label="선택된 실험">
      <CardHeader className="flex-row items-center justify-between">
        <CardTitle>{experiment.name}</CardTitle>
        <Button type="button" size="sm" onClick={onRun} disabled={running}>
          {running ? "실행 중…" : "실행"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-xs text-[var(--color-muted)]">
        {experiment.description ? (
          <p className="text-sm text-[var(--color-fg)]">{experiment.description}</p>
        ) : null}
        <dl className="grid grid-cols-2 gap-1">
          <dt>시나리오</dt>
          <dd className="text-right text-[var(--color-fg)]">{experiment.params.scenario}</dd>
          <dt>limit</dt>
          <dd className="text-right text-[var(--color-fg)]">{experiment.params.limit}</dd>
          <dt>카테고리</dt>
          <dd className="text-right text-[var(--color-fg)]">
            {experiment.params.category ?? "전체"}
          </dd>
          <dt>참여 회사</dt>
          <dd className="text-right text-[var(--color-fg)]">
            {experiment.operator_slugs && experiment.operator_slugs.length > 0
              ? experiment.operator_slugs.join(", ")
              : "전체"}
          </dd>
          <dt>생성</dt>
          <dd className="text-right text-[var(--color-fg)]">
            {experiment.created_at ? formatDateTime(experiment.created_at) : "—"}
          </dd>
        </dl>
      </CardContent>
    </Card>
  );
}
