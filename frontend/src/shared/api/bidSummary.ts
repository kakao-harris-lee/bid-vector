import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * GET /api/v1/operations/bid-decisions/{decisionRecordId}/summary
 *
 * Aggregates one persisted `BidDecisionRecord` into a decision-support summary
 * the operator consults while writing the 나라장터 투찰서 **by hand**. 404 when
 * the record id is unknown / belongs to a different operator / the linked
 * project is missing — translated to a Korean toast-ready message.
 */
export function fetchBidSummary(
  decisionRecordId: number,
  token?: string | null
): Promise<BidSummaryResponse> {
  return wrap(
    apiRequest<BidSummaryResponse>(
      `/api/v1/operations/bid-decisions/${decisionRecordId}/summary`,
      { token }
    ),
    "투찰 요약을 불러오지 못했습니다."
  );
}
