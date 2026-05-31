import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  SyntheticBacktestRunRequest,
  SyntheticBacktestRunResponse,
  SyntheticBacktestTaskResponse,
  SyntheticBacktestTaskStatusResponse,
  SyntheticExperimentCreateRequest,
  SyntheticExperimentResponse,
  SyntheticExperimentRunResponse,
  SyntheticOperatorListResponse,
  SyntheticSeedResponse
} from "@/shared/types/synthetic";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, err.message || fallback);
    }
    throw err;
  });
}

export function fetchSyntheticOperators(token?: string | null): Promise<SyntheticOperatorListResponse> {
  return wrap(
    apiRequest<SyntheticOperatorListResponse>("/api/v1/synthetic/operators", { token }),
    "synthetic 운영자를 불러오지 못했습니다."
  );
}

export function seedSyntheticOperators(
  options: { purge?: boolean } = {},
  token?: string | null
): Promise<SyntheticSeedResponse> {
  return wrap(
    apiRequest<SyntheticSeedResponse>("/api/v1/synthetic/operators/seed", {
      method: "POST",
      body: { purge: options.purge ?? false },
      token
    }),
    "synthetic 운영자 시드에 실패했습니다."
  );
}

export function runSyntheticBacktest(
  payload: SyntheticBacktestRunRequest,
  token?: string | null
): Promise<SyntheticBacktestRunResponse> {
  return wrap(
    apiRequest<SyntheticBacktestRunResponse>("/api/v1/synthetic/backtests/run", {
      method: "POST",
      body: payload,
      token
    }),
    "synthetic 백테스트 실행에 실패했습니다."
  );
}

export function queueSyntheticBacktest(
  payload: SyntheticBacktestRunRequest,
  token?: string | null
): Promise<SyntheticBacktestTaskResponse> {
  return wrap(
    apiRequest<SyntheticBacktestTaskResponse>("/api/v1/synthetic/backtests/run-async", {
      method: "POST",
      body: payload,
      token
    }),
    "synthetic 백테스트 비동기 실행에 실패했습니다."
  );
}

export function fetchSyntheticBacktestTaskStatus(
  taskId: string,
  token?: string | null
): Promise<SyntheticBacktestTaskStatusResponse> {
  return wrap(
    apiRequest<SyntheticBacktestTaskStatusResponse>(
      `/api/v1/synthetic/backtests/tasks/${taskId}`,
      { token }
    ),
    "synthetic 백테스트 태스크 상태를 불러오지 못했습니다."
  );
}

// --- Experiment Lab (Phase 1) -------------------------------------------------

export function createExperiment(
  payload: SyntheticExperimentCreateRequest,
  token?: string | null
): Promise<SyntheticExperimentResponse> {
  return wrap(
    apiRequest<SyntheticExperimentResponse>("/api/v1/synthetic/experiments", {
      method: "POST",
      body: payload,
      token
    }),
    "실험 생성에 실패했습니다."
  );
}

export function fetchExperiments(
  token?: string | null
): Promise<SyntheticExperimentResponse[]> {
  return wrap(
    apiRequest<SyntheticExperimentResponse[]>("/api/v1/synthetic/experiments", {
      token
    }),
    "실험 목록을 불러오지 못했습니다."
  );
}

export function fetchExperiment(
  id: number,
  token?: string | null
): Promise<SyntheticExperimentResponse> {
  return wrap(
    apiRequest<SyntheticExperimentResponse>(
      `/api/v1/synthetic/experiments/${id}`,
      { token }
    ),
    "실험 상세를 불러오지 못했습니다."
  );
}

export function triggerExperimentRun(
  id: number,
  token?: string | null
): Promise<SyntheticExperimentRunResponse> {
  return wrap(
    apiRequest<SyntheticExperimentRunResponse>(
      `/api/v1/synthetic/experiments/${id}/runs`,
      { method: "POST", token }
    ),
    "실험 실행 트리거에 실패했습니다."
  );
}

export function fetchExperimentRun(
  id: number,
  runId: number,
  token?: string | null
): Promise<SyntheticExperimentRunResponse> {
  return wrap(
    apiRequest<SyntheticExperimentRunResponse>(
      `/api/v1/synthetic/experiments/${id}/runs/${runId}`,
      { token }
    ),
    "실험 런 상태를 불러오지 못했습니다."
  );
}
