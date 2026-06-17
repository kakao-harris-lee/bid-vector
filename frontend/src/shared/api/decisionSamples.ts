import { apiRequest } from "./client";
import { ApiError, getStoredToken } from "./session";
import type { DecisionSamplesResponse } from "@/shared/types/decisionSamples";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export interface DecisionSamplesQuery {
  days?: number;
  limit?: number;
}

function buildSearch(params: DecisionSamplesQuery): URLSearchParams {
  const search = new URLSearchParams();
  if (typeof params.days === "number") search.set("days", String(params.days));
  if (typeof params.limit === "number") search.set("limit", String(params.limit));
  return search;
}

/**
 * GET /api/v1/operations/decision-samples?format=json — 최근 추천/예측 증적
 * 샘플 목록.
 *
 * 감사 목적의 증적이며 정산·실낙찰 결과가 아니다. probability_score 는 가격
 * 적합도(추정)이지 P(낙찰)이 아니다(§1.5). 단일 운영자(canonical) 기준.
 */
export function fetchDecisionSamples(
  params: DecisionSamplesQuery = {},
  token?: string | null
): Promise<DecisionSamplesResponse> {
  const search = buildSearch(params);
  search.set("format", "json");
  const path = `/api/v1/operations/decision-samples?${search.toString()}`;
  return wrap(
    apiRequest<DecisionSamplesResponse>(path, { token }),
    "의사결정 증적 샘플을 불러오지 못했습니다."
  );
}

/**
 * GET …/decision-samples?format=csv — 백엔드가 렌더한 CSV 첨부를 그대로 받는다.
 *
 * `apiRequest` 는 항상 응답 body 를 JSON 파싱하므로, 여기서는 `fetch` 를 직접
 * 호출(Bearer 헤더 + 401 session-expired dispatch 를 최소 미러링)하고
 * `response.text()` 를 반환한다 — 클라이언트 측 drift 없이 백엔드 CSV 를 재사용.
 */
export async function fetchDecisionSamplesCsv(
  params: DecisionSamplesQuery = {},
  token?: string | null
): Promise<string> {
  const search = buildSearch(params);
  search.set("format", "csv");
  const path = `/api/v1/operations/decision-samples?${search.toString()}`;
  const authToken = token ?? getStoredToken();
  const headers: Record<string, string> = {};
  if (authToken) headers.Authorization = `Bearer ${authToken}`;

  const response = await fetch(path, { method: "GET", headers });

  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("bid-vector:session-expired"));
    }
    throw new ApiError(response.status, "의사결정 증적 CSV 를 불러오지 못했습니다.");
  }

  return response.text();
}
