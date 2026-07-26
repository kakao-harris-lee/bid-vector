import { experimentRunCsvUrl } from "@/shared/api";
import type { RunOption } from "../runCompare.helpers";

export function RunCompareCsvButton({
  option,
  side
}: {
  option: RunOption | null;
  side: "A" | "B";
}) {
  if (!option) return null;
  return (
    <a
      href={experimentRunCsvUrl(option.experimentId, option.runId)}
      download
      className="inline-flex h-8 items-center rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2.5 text-xs font-medium text-[var(--color-fg)] transition-colors hover:bg-[var(--color-secondary)]"
      aria-label={`${side} 런 CSV 다운로드`}
    >
      {side} CSV 다운로드
    </a>
  );
}
