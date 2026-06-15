import { useQuery } from "@tanstack/react-query";
import { fetchBidFormDraft, fetchBidSummary, queryKeys } from "@/shared/api";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import type { BidFormDraftResponse } from "@/shared/types/bidFormDraft";
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

/**
 * GET /api/v1/operations/bid-decisions/{id}/bid-form-draft (json)
 *
 * Read-only 투찰서 초안 — 나라장터 입력 항목에 매핑된 구조화 산출물(자동 제출 아님).
 * Same `enabled` guards as `useBidSummaryQuery`: disabled when there is no token
 * or the record id is not a finite number.
 */
export function useBidFormDraftQuery(
  session: AuthSession | null,
  decisionRecordId: number | null
) {
  return useQuery<BidFormDraftResponse, Error>({
    queryKey: queryKeys.decisions.bidFormDraft(decisionRecordId ?? -1),
    queryFn: () => fetchBidFormDraft(decisionRecordId as number, session?.token),
    enabled:
      Boolean(session?.token) &&
      decisionRecordId !== null &&
      Number.isFinite(decisionRecordId)
  });
}
