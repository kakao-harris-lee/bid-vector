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

/**
 * apply 로 전송하는 결정의 감사 상태(백엔드 `apply.py::DecisionStatus` 단일 출처).
 *
 * ⚠️ 타입 드리프트: 백엔드 스키마(`OnboardingApplyDecision`)는 `status`(기본 accepted)
 * 를 이미 갖고 있으나 생성된 `openapi.d.ts` 는 아직 `field`/`value` 만 노출한다
 * (origin/main 부터 재생성 안 됨). 백엔드 env 없이 sync-types 재생성이 불가한
 * 프론트 경계라, 그 간극만 여기서 수기로 보강한다. sync-types 재실행 시 이 수기
 * 타입은 생성 타입으로 대체한다.
 *
 * `pending` 은 **절대 전송하지 않으므로**(정직 명세 §2) 전송 union 에서 제외한다.
 * - `accepted` : 후보 그대로 확정 → 백엔드가 반영 + 감사
 * - `modified` : 사용자가 값을 고쳐 확정 → 백엔드가 반영 + 감사
 * - `rejected` : 거부 → 감사만(프로필/전략 불변)
 */
export type OnboardingSentStatus = "accepted" | "modified" | "rejected";

/**
 * apply 로 보내는 단일 결정 = 생성된 `{field, value}` 에 감사용 `status` 를 더한
 * 실제 백엔드 계약(위 드리프트 보강). 값은 canonical raw 를 그대로 싣는다.
 */
export type OnboardingApplyDecisionPayload = NonNullable<
  OnboardingApplyRequest["decisions"]
>[number] & { status: OnboardingSentStatus };

/** apply 요청 본문(수기 계약 — decisions 가 status 를 실어 나른다). */
export interface OnboardingApplyPayload {
  decisions: OnboardingApplyDecisionPayload[];
}

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
 * 사용자가 검토한 결정을 현재 operator 에 부분 반영한다. accepted/modified 는 반영+감사,
 * rejected 는 감사만(백엔드가 반영 상태를 강제) — pending(미확정)은 전송하지 않는다
 * (§2 정직 명세, 자동 반영 금지). 프론트는 감사 상태를 정직하게 보고만 한다.
 */
export function applyOnboardingSuggestions(
  payload: OnboardingApplyPayload,
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
