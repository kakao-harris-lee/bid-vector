import { LabeledStat } from "@/shared/components";
import type {
  G2EvidenceSummaryResponse,
  OperationsDashboardResponse
} from "@/shared/types/operations";
import { g2StatusLabel } from "./helpers";

export function AdminFocusStrip({
  operatorLabel,
  operations,
  g2Evidence
}: {
  operatorLabel: string;
  operations: OperationsDashboardResponse;
  g2Evidence: G2EvidenceSummaryResponse | null;
}) {
  const scopedOperator =
    g2Evidence?.current_operator_username ??
    operations.current_operator_username ??
    operatorLabel;
  const gapCount = g2Evidence?.blocking_gaps.length ?? null;
  return (
    <section
      aria-label="G-2 admin 점검 범위"
      className="grid grid-cols-1 gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3 text-xs sm:grid-cols-3"
    >
      <LabeledStat label="점검 operator" value={scopedOperator || "토큰 소유자"} />
      <LabeledStat
        label="G-2 evidence"
        value={g2Evidence ? g2StatusLabel(g2Evidence.evidence_status) : "미연결"}
      />
      <LabeledStat
        label="blocking gaps"
        value={gapCount === null ? "확인 대기" : `${gapCount}건`}
      />
    </section>
  );
}
