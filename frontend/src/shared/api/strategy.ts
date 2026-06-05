import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  OperatorStrategyCandidatesResponse,
  OperatorStrategyResponse,
  OperatorStrategyRunListResponse,
  OperatorStrategyUpdatePayload
} from "@/shared/types/strategy";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * Append `?operator_id=` only when the caller passes a concrete number.
 * `null`/`undefined` means "fall back to the token owner" so the URL must omit
 * the param entirely — mirrors the dashboard pattern (PR #71).
 */
function withOperator(path: string, operatorId?: number | null): string {
  if (typeof operatorId !== "number") return path;
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}operator_id=${operatorId}`;
}

export function fetchStrategy(
  token?: string | null,
  operatorId?: number | null
): Promise<OperatorStrategyResponse> {
  return wrap(
    apiRequest<OperatorStrategyResponse>(
      withOperator("/api/v1/operator/strategy", operatorId),
      { token }
    ),
    "전략 정보를 불러오지 못했습니다."
  );
}

export function updateStrategy(
  payload: OperatorStrategyUpdatePayload,
  token?: string | null
): Promise<OperatorStrategyResponse> {
  return wrap(
    apiRequest<OperatorStrategyResponse>("/api/v1/operator/strategy", {
      method: "PUT",
      body: payload,
      token
    }),
    "전략 저장에 실패했습니다."
  );
}

export interface StrategyCandidatesQuery {
  limit?: number;
  highPriorityOnly?: boolean;
}

export function fetchStrategyCandidates(
  params: StrategyCandidatesQuery = {},
  token?: string | null,
  operatorId?: number | null
): Promise<OperatorStrategyCandidatesResponse> {
  const search = new URLSearchParams();
  if (typeof params.limit === "number") search.set("limit", String(params.limit));
  if (typeof params.highPriorityOnly === "boolean") {
    search.set("high_priority_only", String(params.highPriorityOnly));
  }
  const query = search.toString();
  const path = query
    ? `/api/v1/operator/strategy/candidates?${query}`
    : "/api/v1/operator/strategy/candidates";
  return wrap(
    apiRequest<OperatorStrategyCandidatesResponse>(
      withOperator(path, operatorId),
      { token }
    ),
    "후보 미리보기를 불러오지 못했습니다."
  );
}

export function fetchStrategyRuns(
  limit = 5,
  token?: string | null
): Promise<OperatorStrategyRunListResponse> {
  const path = `/api/v1/operator/strategy/monitor/runs?limit=${limit}`;
  return wrap(
    apiRequest<OperatorStrategyRunListResponse>(path, { token }),
    "모니터링 이력을 불러오지 못했습니다."
  );
}
