import type { WizardStep } from "./formModel";

interface WizardProgressProps {
  current: number;
  total: number;
  steps: readonly WizardStep[];
}

export function WizardProgress({ current, total, steps }: WizardProgressProps) {
  const active = steps[Math.min(current, steps.length - 1)];
  const pct = Math.round(((current + 1) / total) * 100);
  return (
    <div
      className="flex flex-col gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3"
      aria-label="등록 마법사 진행 상태"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-[var(--color-fg)]">
          {active.title}
        </span>
        <span className="text-[11px] text-[var(--color-muted)] tabular-nums">
          {current + 1} / {total}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-border)]"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className="h-full bg-[var(--color-primary)] transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
      <p className="text-[11px] text-[var(--color-muted)]">{active.description}</p>
    </div>
  );
}
