import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  BidDecisionRecord,
  DecisionFunnelResponse,
  DecisionRecommendationResponse,
  DecisionStatus
} from "@/shared/types/decisions";
import type { components } from "@/shared/types/openapi.d";

/**
 * Dashboard inline action vocabulary. Mirrors the
 * `BidDecisionActionRequest.action` enum in the OpenAPI schema — kept narrow so
 * callers don't have to import the generated type just to type a button.
 */
export type BidDecisionActionType = "submit" | "review" | "skip";

/** Response payload for the inline action endpoint (same shape as status PATCH). */
export type BidDecisionRecordResponse =
  components["schemas"]["BidDecisionRecordResponse"];

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

export interface ApplyBidDecisionActionArgs {
  decisionRecordId: number;
  action: BidDecisionActionType;
  /**
   * Operator the dashboard is currently scoped to. `null` falls back to the
   * token owner — matches the same `?operator_id=` convention used by the
   * dashboard list endpoints (PR #70/#71).
   */
  operatorId?: number | null;
  token?: string | null;
}

/**
 * POST /api/v1/operations/bid-decisions/{id}/actions
 *
 * Inline dashboard action that reuses the Telegram apply_telegram_action
 * transition rules on the backend (submit/review/skip → action /
 * decision_status / pursue_bid). Errors are translated to Korean toast-ready
 * messages: 404 → record not found, 403 → 권한 없음, 422 → 잘못된 액션.
 * 401은 client에서 session-expired modal로 처리되므로 그대로 throw.
 */
export function applyBidDecisionAction({
  decisionRecordId,
  action,
  operatorId = null,
  token
}: ApplyBidDecisionActionArgs): Promise<BidDecisionRecordResponse> {
  const qs =
    operatorId !== null && operatorId !== undefined
      ? `?operator_id=${operatorId}`
      : "";
  const promise = apiRequest<BidDecisionRecordResponse>(
    `/api/v1/operations/bid-decisions/${decisionRecordId}/actions${qs}`,
    {
      method: "POST",
      body: { action },
      token
    }
  );
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      if (err.status === 404) {
        throw new ApiError(404, "결정 기록을 찾을 수 없습니다.");
      }
      if (err.status === 403) {
        throw new ApiError(403, "권한이 없습니다.");
      }
      if (err.status === 422) {
        throw new ApiError(422, "잘못된 액션 요청입니다.");
      }
      throw new ApiError(err.status, "결정 액션 적용에 실패했습니다.");
    }
    throw err;
  });
}
