import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  BidDecisionRecord,
  DecisionFunnelResponse,
  DecisionRecommendationResponse,
  DecisionStatus
} from "@/shared/types/decisions";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export interface DecisionFunnelQuery {
  days?: number;
  limit?: number;
  breakdownLimit?: number;
  trendBucketDays?: number;
}

function funnelSearch(q: DecisionFunnelQuery): string {
  const search = new URLSearchParams();
  if (typeof q.days === "number") search.set("days", String(q.days));
  if (typeof q.limit === "number") search.set("limit", String(q.limit));
  if (typeof q.breakdownLimit === "number") search.set("breakdown_limit", String(q.breakdownLimit));
  if (typeof q.trendBucketDays === "number") search.set("trend_bucket_days", String(q.trendBucketDays));
  return search.toString();
}

export function fetchDecisionFunnel(
  query: DecisionFunnelQuery = {},
  token?: string | null
): Promise<DecisionFunnelResponse> {
  const qs = funnelSearch(query);
  const path = qs ? `/api/v1/analytics/decision-funnel?${qs}` : "/api/v1/analytics/decision-funnel";
  return wrap(
    apiRequest<DecisionFunnelResponse>(path, { token }),
    "결정 퍼널을 불러오지 못했습니다."
  );
}

export function fetchDecisionRecommendations(
  query: DecisionFunnelQuery = {},
  token?: string | null
): Promise<DecisionRecommendationResponse> {
  const qs = funnelSearch(query);
  const path = qs
    ? `/api/v1/analytics/decision-recommendations?${qs}`
    : "/api/v1/analytics/decision-recommendations";
  return wrap(
    apiRequest<DecisionRecommendationResponse>(path, { token }),
    "추천을 불러오지 못했습니다."
  );
}

export function updateBidDecisionStatus(
  decisionRecordId: number,
  decisionStatus: DecisionStatus,
  token?: string | null
): Promise<BidDecisionRecord> {
  return wrap(
    apiRequest<BidDecisionRecord>(
      `/api/v1/operations/bid-decisions/${decisionRecordId}/status`,
      {
        method: "PATCH",
        body: { decision_status: decisionStatus },
        token
      }
    ),
    "결정 상태 전환에 실패했습니다."
  );
}
