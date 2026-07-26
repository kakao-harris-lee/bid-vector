import { useMemo, useState } from "react";
import { useShellContext } from "@/app/dashboardContext";
import { useCrossAppNavigate } from "@/shared/crossAppNav";
import { useApplyBidDecisionActionMutation } from "@/features/dashboard/hooks";
import type { BidDecisionActionType } from "@/shared/api";
import type { DecisionFunnelBreakdownItem } from "@/shared/types/decisions";
import {
  useDecisionFunnelQuery,
  useDecisionRecommendationsQuery,
  useOperationsKpiQuery
} from "./hooks";
import { OperationsKpiPanel } from "./OperationsKpiPanel";
import {
  DecisionReviewFlowCard,
  FunnelOverview,
  RecentDecisionsCard,
  RecommendationsList,
  SegmentBreakdownCard
} from "./components";

export function DecisionsScreen() {
  const { session, activeOperator } = useShellContext();
  // Admin screen linking into the user app (/dashboard/...) crosses the bundle
  // boundary, so use the cross-app helper (full-page nav in standalone admin).
  const navigate = useCrossAppNavigate();
  const [days, setDays] = useState(30);
  const [breakdownDimension, setBreakdownDimension] = useState<"category" | "workload" | "agency">(
    "category"
  );
  // Track per-row pending action so each "최근 결정" article gets its own
  // spinner. The shared dashboard mutation hook handles toasts + cache
  // invalidation; we only manage which row is currently in-flight.
  const [pendingActionByRecord, setPendingActionByRecord] = useState<
    Record<number, BidDecisionActionType | null>
  >({});
  const [reasonDraftByRecord, setReasonDraftByRecord] = useState<Record<number, string>>({});

  const funnel = useDecisionFunnelQuery(session, { days });
  const recs = useDecisionRecommendationsQuery(session, { days });
  const operationsKpi = useOperationsKpiQuery(
    session,
    { days, missedLimit: 10 },
    activeOperator.activeOperatorId
  );
  const actionMutation = useApplyBidDecisionActionMutation(session);

  const breakdown = useMemo(() => {
    if (!funnel.data) return [] as DecisionFunnelBreakdownItem[];
    if (breakdownDimension === "category") return funnel.data.category_breakdown;
    if (breakdownDimension === "workload") return funnel.data.workload_source_breakdown;
    return funnel.data.agency_breakdown;
  }, [funnel.data, breakdownDimension]);

  const handleAction = (decisionRecordId: number, action: BidDecisionActionType) => {
    if (!reasonDraftByRecord[decisionRecordId]?.trim()) return;
    setPendingActionByRecord((prev) => ({ ...prev, [decisionRecordId]: action }));
    actionMutation.mutate(
      {
        decisionRecordId,
        action,
        operatorId: activeOperator.activeOperatorId
      },
      {
        onSettled: () => {
          setPendingActionByRecord((prev) => {
            const next = { ...prev };
            next[decisionRecordId] = null;
            return next;
          });
        }
      }
    );
  };

  return (
    <section className="flex flex-col gap-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-[var(--color-fg)]">결정 게이트웨이</h2>
        <label className="flex items-center gap-2 text-xs">
          기간
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            aria-label="기간 (일)"
            className="h-8 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] px-2 text-xs"
          >
            <option value={7}>7일</option>
            <option value={14}>14일</option>
            <option value={30}>30일</option>
            <option value={60}>60일</option>
            <option value={90}>90일</option>
          </select>
        </label>
      </header>

      <FunnelOverview funnel={funnel.data} loading={funnel.isPending} error={funnel.error} />

      <OperationsKpiPanel
        data={operationsKpi.data}
        loading={operationsKpi.isPending}
        error={operationsKpi.error}
      />

      <DecisionReviewFlowCard />

      <SegmentBreakdownCard
        items={breakdown}
        dimension={breakdownDimension}
        onDimensionChange={setBreakdownDimension}
      />

      <RecommendationsList
        recommendations={recs.data?.recommendations ?? []}
        headline={recs.data?.headline}
        loading={recs.isPending}
        error={recs.error}
      />

      <RecentDecisionsCard
        funnel={funnel.data}
        reasonDraftByRecord={reasonDraftByRecord}
        pendingActionByRecord={pendingActionByRecord}
        onReasonChange={(recordId, next) =>
          setReasonDraftByRecord((prev) => ({ ...prev, [recordId]: next }))
        }
        onAction={handleAction}
        onNavigateSummary={(recordId) => navigate(`/dashboard/decisions/${recordId}/summary`)}
      />
    </section>
  );
}
