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
/** 감사 이력 한 행(읽기 전용). */
export type OnboardingSuggestionHistoryItem =
  components["schemas"]["OnboardingSuggestionHistoryItem"];
/** 감사 이력 페이지(items + total + limit/offset + operator envelope). */
export type OnboardingSuggestionHistoryResponse =
  components["schemas"]["OnboardingSuggestionHistoryResponse"];

/**
 * 온보딩 결정/감사 상태 — 생성된 `DecisionStatus` 스키마 단일 출처
 * (accepted|modified|rejected|pending). 문자열 리터럴을 다시 적지 않고 생성 타입을
 * 그대로 재노출해 허용값 드리프트를 막는다(§4.6). apply 요청과 감사 로그가 공유한다.
 */
export type OnboardingDecisionStatus = components["schemas"]["DecisionStatus"];

/**
 * apply 로 **전송 가능한** 감사 상태 = 생성된 `OnboardingDecisionStatus` 에서 `pending`
 * (미확정)만 제외한 union. `pending` 은 정직 명세 §2 에 따라 절대 전송하지 않으므로
 * (자동 반영 금지) 전송 계약에서 배제한다. 리터럴을 재선언하지 않고 `Exclude` 로
 * 파생해, 백엔드가 허용값을 넓혀도 pending 배제 규칙은 그대로 유지된다.
 * - `accepted` : 후보 그대로 확정 → 백엔드가 반영 + 감사
 * - `modified` : 사용자가 값을 고쳐 확정 → 백엔드가 반영 + 감사
 * - `rejected` : 거부 → 감사만(프로필/전략 불변)
 */
export type OnboardingSentStatus = Exclude<OnboardingDecisionStatus, "pending">;

/**
 * apply 로 보내는 단일 결정 = 생성된 `OnboardingApplyDecision`, 단 `status` 는 전송
 * 불가한 `pending` 을 뺀 `OnboardingSentStatus` 로 좁혀(정직 명세 §2 를 컴파일 타임에
 * 강제) 재노출한다. `source`/`confidence`/`reason` 은 GET 후보 provenance(감사용,
 * 선택). 값은 canonical raw 를 그대로 싣는다.
 */
export type OnboardingApplyDecisionPayload = Omit<
  components["schemas"]["OnboardingApplyDecision"],
  "status"
> & { status: OnboardingSentStatus };

/** apply 요청 본문 — decisions 가 (pending 제외) status 를 실어 나른다. */
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

export interface OnboardingHistoryQuery {
  /** 필드명 정확 일치 필터(선택). */
  field?: string | null;
  /** 결정 상태 필터(선택). */
  status?: OnboardingDecisionStatus | null;
  /** 페이지 크기(백엔드 1~200 상한). */
  limit?: number;
  /** 페이지 오프셋. */
  offset?: number;
}

/**
 * 운영자의 온보딩 결정 감사 이력을 최신순(created_at DESC)으로 조회한다(읽기 전용,
 * append-only 로그). `fetchOnboardingSuggestions` 와 동일한 operator-scope 규약을
 * 따른다(operator_id 미지정 시 토큰 소유자로 폴백).
 */
export function fetchOnboardingHistory(
  query: OnboardingHistoryQuery,
  token?: string | null,
  operatorId?: number | null
): Promise<OnboardingSuggestionHistoryResponse> {
  const search = new URLSearchParams();
  if (query.field && query.field.trim()) search.set("field", query.field.trim());
  if (query.status) search.set("status", query.status);
  if (typeof query.limit === "number") search.set("limit", String(query.limit));
  if (typeof query.offset === "number") search.set("offset", String(query.offset));
  withOperator(search, operatorId);
  const qs = search.toString();
  const path = qs
    ? `/api/v1/operator/onboarding-suggestions/history?${qs}`
    : "/api/v1/operator/onboarding-suggestions/history";
  return wrap(
    apiRequest<OnboardingSuggestionHistoryResponse>(path, { token }),
    "온보딩 감사 이력을 불러오지 못했습니다."
  );
}
