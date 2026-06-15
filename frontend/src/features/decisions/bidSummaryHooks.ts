import { useQuery } from "@tanstack/react-query";
import { fetchBidSummary, queryKeys } from "@/shared/api";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * GET /api/v1/operations/bid-decisions/{id}/summary
 *
 * Read-only decision-support aggregate for one persisted `BidDecisionRecord`.
 * Disabled when there is no token or the record id is not a finite number
 * (e.g. a malformed `/decisions/:id/summary` route param).
 */
export function useBidSummaryQuery(
  session: AuthSession | null,
  decisionRecordId: number | null
) {
  return useQuery<BidSummaryResponse, Error>({
    queryKey: queryKeys.decisions.summary(decisionRecordId ?? -1),
    queryFn: () => fetchBidSummary(decisionRecordId as number, session?.token),
    enabled:
      Boolean(session?.token) &&
      decisionRecordId !== null &&
      Number.isFinite(decisionRecordId)
  });
}
