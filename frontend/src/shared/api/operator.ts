import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { components } from "@/shared/types/openapi";

export type OperatorAccountItem = components["schemas"]["OperatorAccountItem"];
export type OperatorAccountListResponse = components["schemas"]["OperatorAccountListResponse"];
export type EligibilityFeedbackRequest = components["schemas"]["EligibilityFeedbackRequest"];
export type EligibilityFeedbackResponse = components["schemas"]["EligibilityFeedbackResponse"];
/** 운영자 식별 판정 — 백엔드 계약(PR #229)에서 파생한 단일 출처 union. */
export type EligibilityVerdict = EligibilityFeedbackRequest["verdict"];
/** 사업자번호 상태조회/진위확인 요청·응답 — 생성 스키마(#248) 단일 출처(수기 중복 금지). */
export type BusinessVerificationRequest = components["schemas"]["BusinessVerificationRequest"];
export type BusinessVerificationResponse = components["schemas"]["BusinessVerificationResponse"];

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * Fetch the operator-account catalogue visible to the current bearer token.
 *
 * - Privileged callers (canonical operator / admin) receive canonical + every
 *   ``synthetic-*`` row so the company-switcher dropdown can render the full
 *   list.
 * - Non-privileged callers receive only their own row; the dropdown collapses
 *   to a single self-pick and is typically hidden.
 */
export function fetchOperatorAccounts(
  token?: string | null
): Promise<OperatorAccountListResponse> {
  return wrap(
    apiRequest<OperatorAccountListResponse>("/api/v1/operator/accounts", { token }),
    "회사 목록을 불러오지 못했습니다."
  );
}

/**
 * Capture the operator's eligibility judgement (적합/부적합/보류) for a notice.
 * The backend upserts an operator-source label (idempotent per project), so
 * re-submitting a different verdict simply overwrites the previous one.
 */
export function submitEligibilityFeedback(
  payload: EligibilityFeedbackRequest,
  token?: string | null
): Promise<EligibilityFeedbackResponse> {
  return wrap(
    apiRequest<EligibilityFeedbackResponse>("/api/v1/operator/eligibility-feedback", {
      method: "POST",
      body: payload,
      token
    }),
    "식별 피드백 저장에 실패했습니다."
  );
}

/**
 * 사업자번호 상태조회/진위확인(#248). ``start_date`` + ``representative_name`` 이 모두
 * 있으면 진위확인, 아니면 상태조회로 백엔드가 분기한다. 원문 번호/서비스키는 응답에
 * 담기지 않고(masked 만), 서비스키 미구성이면 status ``unknown`` 으로 graceful 하게 응답한다.
 * **원문 번호는 요청 본문으로만 전송하고 로그/캐시/쿼리키에 남기지 않는다(§2 정직 명세).**
 */
export function verifyBusinessNumber(
  payload: BusinessVerificationRequest,
  token?: string | null
): Promise<BusinessVerificationResponse> {
  return wrap(
    apiRequest<BusinessVerificationResponse>("/api/v1/operator/business-verification", {
      method: "POST",
      body: payload,
      token
    }),
    "사업자번호 확인에 실패했습니다."
  );
}
