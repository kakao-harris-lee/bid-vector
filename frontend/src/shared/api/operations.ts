import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  OperationsDashboardResponse,
  OperationsKpiResponse
} from "@/shared/types/operations";

/** Recommendation usefulness verdict — value matches backend event_data contract. */
export type RecommendationFeedbackVerdict = "useful" | "not_useful";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export function fetchOperationsDashboard(
  options: { days?: number; limit?: number } = {},
  token?: string | null
): Promise<OperationsDashboardResponse> {
  const search = new URLSearchParams();
  if (typeof options.days === "number") search.set("days", String(options.days));
  if (typeof options.limit === "number") search.set("limit", String(options.limit));
  const qs = search.toString();
  const path = qs
    ? `/api/v1/analytics/operations-dashboard?${qs}`
    : "/api/v1/analytics/operations-dashboard";
  return wrap(
    apiRequest<OperationsDashboardResponse>(path, { token }),
    "운영 대시보드를 불러오지 못했습니다."
  );
}

export interface OperationsKpiQuery {
  days?: number;
  missedLimit?: number;
}

export function fetchOperationsKpi(
  options: OperationsKpiQuery = {},
  token?: string | null
): Promise<OperationsKpiResponse> {
  const search = new URLSearchParams();
  if (typeof options.days === "number") search.set("days", String(options.days));
  if (typeof options.missedLimit === "number") {
    search.set("missed_limit", String(options.missedLimit));
  }
  const qs = search.toString();
  const path = qs
    ? `/api/v1/analytics/operations-kpi?${qs}`
    : "/api/v1/analytics/operations-kpi";
  return wrap(
    apiRequest<OperationsKpiResponse>(path, { token }),
    "운영 KPI를 불러오지 못했습니다."
  );
}

/**
 * Best-effort analytics event logger.
 *
 * Roadmap C-1 instrumentation: client tracks `project_view` and
 * `recommendation_feedback` so the backend can derive review-time and
 * recommendation-usefulness KPIs. Telemetry failures must NEVER break UX —
 * we swallow errors and surface them via console.warn only. If there is no
 * token, we skip silently (anonymous events aren't supported).
 */
export async function logAnalyticsEvent(
  eventType: string,
  eventData: Record<string, unknown>,
  token?: string | null
): Promise<void> {
  if (!token) return;
  try {
    await apiRequest<unknown>("/api/v1/analytics/event", {
      method: "POST",
      token,
      body: { event_type: eventType, event_data: eventData }
    });
  } catch (err) {
    // Best-effort: never throw to caller. Surface in console for debugging.
    // eslint-disable-next-line no-console
    console.warn(`[analytics] ${eventType} 이벤트 송신 실패`, err);
  }
}

/** Roadmap C-1 (a) — fire on tender/opportunity detail view. */
export function trackProjectView(
  projectId: number,
  token?: string | null
): Promise<void> {
  return logAnalyticsEvent("project_view", { project_id: projectId }, token);
}

/** Roadmap C-1 (c) — fire on operator 👍/👎 against a recommendation. */
export function submitRecommendationFeedback(
  decisionRecordId: number,
  projectId: number,
  verdict: RecommendationFeedbackVerdict,
  token?: string | null
): Promise<void> {
  return logAnalyticsEvent(
    "recommendation_feedback",
    {
      decision_record_id: decisionRecordId,
      project_id: projectId,
      verdict
    },
    token
  );
}
