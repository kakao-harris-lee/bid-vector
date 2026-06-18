import { apiRequest } from "./client";
import { ApiError } from "./session";
import type {
  CustomOperatorCloneRequest,
  CustomOperatorCreateRequest,
  CustomOperatorDeleteResponse,
  CustomOperatorDetail,
  CustomOperatorUpdateRequest,
  SyntheticBacktestRunRequest,
  SyntheticBacktestRunResponse,
  SyntheticBacktestTaskResponse,
  SyntheticBacktestTaskStatusResponse,
  SyntheticExperimentCompareResponse,
  SyntheticExperimentCreateRequest,
  SyntheticExperimentPresetListResponse,
  SyntheticExperimentResponse,
  SyntheticExperimentRunResponse,
  SyntheticExperimentSampleGapCandidateRequest,
  SyntheticExperimentSampleGapPlanResponse,
  SyntheticExperimentSampleGapRunCandidateResponse,
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

export function fetchExperimentPresets(
  token?: string | null
): Promise<SyntheticExperimentPresetListResponse> {
  return wrap(
    apiRequest<SyntheticExperimentPresetListResponse>(
      "/api/v1/synthetic/experiments/presets",
      { token }
    ),
    "G-1 preset 목록을 불러오지 못했습니다."
  );
}

export function ensureExperimentPreset(
  presetName: string,
  token?: string | null
): Promise<SyntheticExperimentResponse> {
  return wrap(
    apiRequest<SyntheticExperimentResponse>(
      `/api/v1/synthetic/experiments/presets/${encodeURIComponent(presetName)}`,
      { method: "POST", token }
    ),
    "G-1 preset 저장에 실패했습니다."
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

export function fetchExperimentSampleGaps(
  maxRuns = 20,
  token?: string | null
): Promise<SyntheticExperimentSampleGapPlanResponse> {
  const query = new URLSearchParams({ max_runs: String(maxRuns) });
  return wrap(
    apiRequest<SyntheticExperimentSampleGapPlanResponse>(
      `/api/v1/synthetic/experiments/sample-gaps?${query.toString()}`,
      { token }
    ),
    "sample-gap 계획을 불러오지 못했습니다."
  );
}

export function buildExperimentSampleGapCandidate(
  payload: SyntheticExperimentSampleGapCandidateRequest,
  token?: string | null
): Promise<SyntheticExperimentSampleGapRunCandidateResponse> {
  return wrap(
    apiRequest<SyntheticExperimentSampleGapRunCandidateResponse>(
      "/api/v1/synthetic/experiments/sample-gaps/candidates",
      {
        method: "POST",
        body: payload,
        token
      }
    ),
    "sample-gap 실행 후보를 만들지 못했습니다."
  );
}

// --- Experiment Lab compare + export (Phase 4) -------------------------------

/**
 * 두 완료 런을 operator_slug로 조인한 A/B 비교를 가져온다.
 * delta는 b-a(양수=B 높음), 한쪽 값이 null이면 해당 delta도 null.
 * 런이 없으면 404, run_a/run_b 누락이면 422.
 */
export function compareExperimentRuns(
  runA: number,
  runB: number,
  token?: string | null
): Promise<SyntheticExperimentCompareResponse> {
  const query = new URLSearchParams({
    run_a: String(runA),
    run_b: String(runB)
  });
  return wrap(
    apiRequest<SyntheticExperimentCompareResponse>(
      `/api/v1/synthetic/experiments/compare?${query.toString()}`,
      { token }
    ),
    "실험 런 비교를 불러오지 못했습니다."
  );
}

/**
 * 런 결과 CSV 내보내기 URL을 만든다(text/csv 첨부).
 * 이 라우터는 인증 토큰이 필요 없으므로 단순 `<a href download>`로 다운로드한다.
 */
export function experimentRunCsvUrl(experimentId: number, runId: number): string {
  return `/api/v1/synthetic/experiments/${experimentId}/runs/${runId}/export.csv`;
}

// --- Custom virtual companies (Phase 3) --------------------------------------

export function createCustomOperator(
  payload: CustomOperatorCreateRequest,
  token?: string | null
): Promise<CustomOperatorDetail> {
  return wrap(
    apiRequest<CustomOperatorDetail>("/api/v1/synthetic/custom-operators", {
      method: "POST",
      body: payload,
      token
    }),
    "커스텀 회사 생성에 실패했습니다."
  );
}

/**
 * 커스텀 회사 단건 상세를 가져온다(편집 폼 정확 프리필용).
 * 프리셋/canonical operator slug면 400, 미존재면 404.
 */
export function getCustomOperator(
  slug: string,
  token?: string | null
): Promise<CustomOperatorDetail> {
  return wrap(
    apiRequest<CustomOperatorDetail>(
      `/api/v1/synthetic/custom-operators/${encodeURIComponent(slug)}`,
      { token }
    ),
    "커스텀 회사 상세를 불러오지 못했습니다."
  );
}

export function updateCustomOperator(
  slug: string,
  payload: CustomOperatorUpdateRequest,
  token?: string | null
): Promise<CustomOperatorDetail> {
  return wrap(
    apiRequest<CustomOperatorDetail>(
      `/api/v1/synthetic/custom-operators/${encodeURIComponent(slug)}`,
      { method: "PUT", body: payload, token }
    ),
    "커스텀 회사 편집에 실패했습니다."
  );
}

export function cloneCustomOperator(
  slug: string,
  payload: CustomOperatorCloneRequest,
  token?: string | null
): Promise<CustomOperatorDetail> {
  return wrap(
    apiRequest<CustomOperatorDetail>(
      `/api/v1/synthetic/custom-operators/${encodeURIComponent(slug)}/clone`,
      { method: "POST", body: payload, token }
    ),
    "커스텀 회사 복제에 실패했습니다."
  );
}

export function deleteCustomOperator(
  slug: string,
  token?: string | null
): Promise<CustomOperatorDeleteResponse> {
  return wrap(
    apiRequest<CustomOperatorDeleteResponse>(
      `/api/v1/synthetic/custom-operators/${encodeURIComponent(slug)}`,
      { method: "DELETE", token }
    ),
    "커스텀 회사 삭제에 실패했습니다."
  );
}
