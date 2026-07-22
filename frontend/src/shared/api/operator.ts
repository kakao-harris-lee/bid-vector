import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { components } from "@/shared/types/openapi";

export type OperatorAccountItem = components["schemas"]["OperatorAccountItem"];
export type OperatorAccountListResponse = components["schemas"]["OperatorAccountListResponse"];
export type EligibilityFeedbackRequest = components["schemas"]["EligibilityFeedbackRequest"];
export type EligibilityFeedbackResponse = components["schemas"]["EligibilityFeedbackResponse"];
/** 운영자 식별 판정 — 백엔드 계약(PR #229)에서 파생한 단일 출처 union. */
export type EligibilityVerdict = EligibilityFeedbackRequest["verdict"];

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
