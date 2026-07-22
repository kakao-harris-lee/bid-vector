import { useState } from "react";
import { Button } from "@/shared/components/ui";
import type { EligibilityVerdict } from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";
import { useEligibilityFeedbackMutation } from "./hooks";

/**
 * Declarative verdict table — labels equal the backend enum values
 * (적합/부적합/보류), so the button copy is the payload. Extend the daily-review
 * vocabulary here, not in the render body (§4.5).
 */
const VERDICTS: readonly EligibilityVerdict[] = ["적합", "부적합", "보류"];

interface EligibilityFeedbackButtonsProps {
  projectId: number;
  session: AuthSession | null;
}

/**
 * Three-verdict feedback control rendered per recommended notice. Clicking a
 * verdict POSTs it and highlights the chosen button; re-clicking a different
 * verdict overwrites it (the backend upserts). Each instance owns its own
 * mutation, so the pending/disabled state is scoped to a single notice.
 */
export function EligibilityFeedbackButtons({
  projectId,
  session
}: EligibilityFeedbackButtonsProps) {
  const mutation = useEligibilityFeedbackMutation(session);
  const [selected, setSelected] = useState<EligibilityVerdict | null>(null);

  const submit = (verdict: EligibilityVerdict) => {
    mutation.mutate(
      { project_id: projectId, verdict },
      { onSuccess: (data) => setSelected(data.verdict as EligibilityVerdict) }
    );
  };

  return (
    <div
      className="flex items-center gap-1 pt-1"
      role="group"
      aria-label="공고 식별 피드백"
    >
      {VERDICTS.map((verdict) => {
        const active = selected === verdict;
        return (
          <Button
            key={verdict}
            size="sm"
            variant={active ? "primary" : "outline"}
            aria-pressed={active}
            aria-label={`${verdict} 피드백`}
            disabled={mutation.isPending}
            onClick={() => submit(verdict)}
          >
            {verdict}
          </Button>
        );
      })}
    </div>
  );
}
