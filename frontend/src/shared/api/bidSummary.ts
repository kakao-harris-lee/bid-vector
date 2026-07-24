import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";
import type { components } from "@/shared/types/openapi.d";

/**
 * 투찰 보고서 메일 전달 결과 (DRY-RUN 기본) — 원문 수신자/본문 시크릿은 노출하지
 * 않고 마스킹된 수신자만 돌려준다. Generated OpenAPI 타입을 그대로 재노출한다.
 */
export type BidReportEmailDeliveryResponse =
  components["schemas"]["BidReportEmailDeliveryResponse"];

/** 투찰 보고서 메일 전달 요청(모두 선택) — 본문 없이 POST 해도 된다. */
export type BidReportEmailSendRequest =
  components["schemas"]["BidReportEmailSendRequest"];

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

/**
 * GET /api/v1/operations/bid-decisions/{decisionRecordId}/summary
 *
 * Aggregates one persisted `BidDecisionRecord` into a decision-support summary
 * the operator consults while writing the 나라장터 투찰서 **by hand**. 404 when
 * the record id is unknown / belongs to a different operator / the linked
 * project is missing — translated to a Korean toast-ready message.
 */
export function fetchBidSummary(
  decisionRecordId: number,
  token?: string | null
): Promise<BidSummaryResponse> {
  return wrap(
    apiRequest<BidSummaryResponse>(
      `/api/v1/operations/bid-decisions/${decisionRecordId}/summary`,
      { token }
    ),
    "투찰 요약을 불러오지 못했습니다."
  );
}

/**
 * POST /api/v1/operations/bid-decisions/{decisionRecordId}/report-email
 *
 * 운영자가 눌러야만 실행되는 **수동** 액션. 백엔드는 기본 DRY-RUN 이므로 실제
 * 메일은 발송되지 않고 렌더링/로깅만 하며(`dry_run=true`), 라이브 송신은 설정
 * opt-in 이다. `recipient` 는 선택 override — 미지정 시 운영자 계정 이메일을
 * 사용하고, 응답에는 항상 마스킹된 수신자만 노출된다. 본문은 선택이라 값이
 * 없으면 body 없이 POST 한다.
 */
export function sendBidReportEmail(
  decisionRecordId: number,
  token?: string | null,
  recipient?: string | null
): Promise<BidReportEmailDeliveryResponse> {
  const body: BidReportEmailSendRequest | undefined =
    recipient == null ? undefined : { recipient };
  return wrap(
    apiRequest<BidReportEmailDeliveryResponse>(
      `/api/v1/operations/bid-decisions/${decisionRecordId}/report-email`,
      { method: "POST", token, body }
    ),
    "메일 전달 요청에 실패했습니다."
  );
}
