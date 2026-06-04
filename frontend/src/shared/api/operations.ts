import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  OperationsDashboardResponse,
  OperationsKpiResponse
} from "@/shared/types/operations";

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
