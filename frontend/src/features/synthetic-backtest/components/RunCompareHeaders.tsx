import { Badge } from "@/shared/components/ui";
import type { SyntheticExperimentCompareRunHeader } from "@/shared/types/synthetic";
import { summaryField } from "../runCompare.helpers";

export function RunCompareHeaders({
  runA,
  runB
}: {
  runA: SyntheticExperimentCompareRunHeader;
  runB: SyntheticExperimentCompareRunHeader;
}) {
  return (
    <dl className="grid grid-cols-2 gap-3" aria-label="비교 런 요약">
      {(
        [
          { side: "A", header: runA },
          { side: "B", header: runB }
        ] as const
      ).map(({ side, header }) => (
        <div
          key={side}
          className="rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-2"
        >
          <div className="mb-1 flex items-center gap-2">
            <Badge tone={side === "A" ? "muted" : "info"}>런 {side}</Badge>
            <span className="font-medium text-[var(--color-fg)]">
              #{header.id} (실험 {header.experiment_id})
            </span>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[var(--color-muted)]">
            <span>시나리오 {summaryField(header, "scenario")}</span>
            <span>limit {summaryField(header, "limit")}</span>
          </div>
        </div>
      ))}
    </dl>
  );
}
