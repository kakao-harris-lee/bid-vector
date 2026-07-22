import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { components } from "@/shared/types/openapi";

// 생성된 OpenAPI 스키마에서 파생한 단일 출처 타입(수기 중복 정의 금지, §4.6).
export type OnboardingFieldSuggestion = components["schemas"]["OnboardingFieldSuggestion"];
export type OnboardingSuggestionsResponse = components["schemas"]["OnboardingSuggestionsResponse"];
export type OnboardingApplyRequest = components["schemas"]["OnboardingApplyRequest"];
export type OnboardingApplyResponse = components["schemas"]["OnboardingApplyResponse"];
export type OnboardingAppliedField = components["schemas"]["OnboardingAppliedField"];
export type OnboardingIgnoredField = components["schemas"]["OnboardingIgnoredField"];
/** apply 가 받는 확정 필드 집합 — GET 후보와 동일 단일 출처 union. */
export type OnboardingApplyField = components["schemas"]["OnboardingApplyField"];
/** 후보/확정값 형태(필드 종류별: 문자열/숫자/문자열 리스트). */
export type OnboardingSuggestionValue = OnboardingFieldSuggestion["value"];

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
 * `null`/`undefined` means "fall back to the token owner" — mirrors the
 * strategy/profile read pattern (PR #71/#74).
 */
function withOperator(search: URLSearchParams, operatorId?: number | null): void {
  if (typeof operatorId === "number") search.set("operator_id", String(operatorId));
}

export interface OnboardingSuggestionsQuery {
  /** 역추천 seed 키워드(반복 파라미터). 최소 1개 필요. */
  keywords: string[];
  region?: string | null;
  minBudget?: number | null;
  maxBudget?: number | null;
}

/**
 * 내부 공고에서 회사 프로필/전략 필드 후보를 역추천한다(읽기 전용, persist 없음).
 * 후보는 확정이 아니므로(§2 정직 명세) 모든 항목이 `needs_confirmation=true`.
 */
export function fetchOnboardingSuggestions(
  query: OnboardingSuggestionsQuery,
  token?: string | null,
  operatorId?: number | null
): Promise<OnboardingSuggestionsResponse> {
  const search = new URLSearchParams();
  for (const keyword of query.keywords) {
    const trimmed = keyword.trim();
    if (trimmed) search.append("keywords", trimmed);
  }
  if (query.region && query.region.trim()) search.set("region", query.region.trim());
  if (typeof query.minBudget === "number") search.set("min_budget", String(query.minBudget));
  if (typeof query.maxBudget === "number") search.set("max_budget", String(query.maxBudget));
  withOperator(search, operatorId);
  const qs = search.toString();
  const path = qs
    ? `/api/v1/operator/onboarding-suggestions?${qs}`
    : "/api/v1/operator/onboarding-suggestions";
  return wrap(
    apiRequest<OnboardingSuggestionsResponse>(path, { token }),
    "온보딩 후보를 불러오지 못했습니다."
  );
}

/**
 * 사용자가 확정한 후보 값만 현재 operator 의 프로필/전략에 부분 반영한다.
 * 확정하지 않은(=accepted 아님) 후보는 절대 전송하지 않는다(§2 정직 명세, 자동 반영 금지).
 */
export function applyOnboardingSuggestions(
  payload: OnboardingApplyRequest,
  token?: string | null,
  operatorId?: number | null
): Promise<OnboardingApplyResponse> {
  const search = new URLSearchParams();
  withOperator(search, operatorId);
  const qs = search.toString();
  const path = qs
    ? `/api/v1/operator/onboarding-suggestions/apply?${qs}`
    : "/api/v1/operator/onboarding-suggestions/apply";
  return wrap(
    apiRequest<OnboardingApplyResponse>(path, { method: "POST", body: payload, token }),
    "확정 반영에 실패했습니다."
  );
}
