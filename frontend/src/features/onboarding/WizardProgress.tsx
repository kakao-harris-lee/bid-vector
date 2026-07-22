import { cn } from "@/shared/lib";
import { STEP_META, STEP_ORDER, type StepKey } from "./constants";

export interface WizardProgressProps {
  current: StepKey;
}

/** 단계 진행 표시. 색만이 아니라 aria-current + 순번/라벨로도 현재 단계를 나타낸다. */
export function WizardProgress({ current }: WizardProgressProps) {
  const currentOrder = STEP_META[current].order;
  return (
    <ol className="flex flex-wrap items-center gap-2" aria-label="온보딩 단계">
      {STEP_ORDER.map((key) => {
        const meta = STEP_META[key];
        const state =
          meta.order === currentOrder ? "current" : meta.order < currentOrder ? "done" : "todo";
        return (
          <li
            key={key}
            aria-current={state === "current" ? "step" : undefined}
            className={cn(
              "flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
              state === "current" &&
                "border-[var(--color-primary)] bg-[var(--color-primary)] text-[var(--color-primary-foreground)]",
              state === "done" &&
                "border-[var(--color-success)] text-[color-mix(in_oklch,var(--color-success),black_20%)]",
              state === "todo" && "border-[var(--color-border)] text-[var(--color-muted)]"
            )}
          >
            <span className="tabular-nums font-semibold">{meta.order}</span>
            <span>{meta.title}</span>
          </li>
        );
      })}
    </ol>
  );
}
