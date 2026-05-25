import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  DecisionExperimentRunDetailResponse,
  DecisionExperimentRunListResponse,
  DecisionExperimentThresholdApplyResponse,
  MLTaskStatusResponse
} from "@/shared/types/experiments";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      const detail = err.message?.includes("결정") ? err.message : fallback;
      throw new ApiError(err.status, detail);
    }
    throw err;
  });
}

export interface ExperimentListQuery {
  limit?: number;
  status?: string;
  outcome?: string;
  applicationStatus?: string;
  sort?: string;
}

export function fetchDecisionExperiments(
  query: ExperimentListQuery = {},
  token?: string | null
): Promise<DecisionExperimentRunListResponse> {
  const search = new URLSearchParams();
  if (typeof query.limit === "number") search.set("limit", String(query.limit));
  if (query.status) search.set("status", query.status);
  if (query.outcome) search.set("outcome", query.outcome);
  if (query.applicationStatus) search.set("application_status", query.applicationStatus);
  if (query.sort) search.set("sort", query.sort);
  const qs = search.toString();
  const path = qs
    ? `/api/v1/analytics/decision-experiments?${qs}`
    : "/api/v1/analytics/decision-experiments";
  return wrap(
    apiRequest<DecisionExperimentRunListResponse>(path, { token }),
    "실험 목록을 불러오지 못했습니다."
  );
}

export interface ApplyThresholdsRequest {
  dry_run?: boolean;
  force?: boolean;
  append_note?: string;
}

export function fetchDecisionExperimentDetail(
  runId: number,
  token?: string | null
): Promise<DecisionExperimentRunDetailResponse> {
  return wrap(
    apiRequest<DecisionExperimentRunDetailResponse>(
      `/api/v1/analytics/decision-experiments/${runId}`,
      { token }
    ),
    "실험 상세를 불러오지 못했습니다."
  );
}

export interface MLTaskResponse {
  task_id: string;
  task_name: string;
  queue: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  detail: string;
  poll_url: string;
}

export function queueDecisionExperimentReevaluation(
  runId: number,
  token?: string | null
): Promise<MLTaskResponse> {
  return wrap(
    apiRequest<MLTaskResponse>(
      `/api/v1/analytics/decision-experiments/${runId}/evaluate`,
      { method: "POST", token }
    ),
    "재평가 큐잉에 실패했습니다."
  );
}

export function fetchReevaluationStatus(
  taskId: string,
  token?: string | null
): Promise<MLTaskStatusResponse> {
  return wrap(
    apiRequest<MLTaskStatusResponse>(
      `/api/v1/ml/reevaluations/decision-experiments/tasks/${taskId}`,
      { token }
    ),
    "태스크 상태를 불러오지 못했습니다."
  );
}

export function applyExperimentThresholds(
  runId: number,
  payload: ApplyThresholdsRequest,
  token?: string | null
): Promise<DecisionExperimentThresholdApplyResponse> {
  return wrap(
    apiRequest<DecisionExperimentThresholdApplyResponse>(
      `/api/v1/analytics/decision-experiments/${runId}/apply-thresholds`,
      {
        method: "POST",
        body: payload,
        token
      }
    ),
    "임계값 적용에 실패했습니다."
  );
}
