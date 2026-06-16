import { apiRequest } from "./client";
import { ApiError } from "./session";
import type { AccuracyReportResponse } from "@/shared/types/accuracyReport";

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export interface AccuracyReportQuery {
  days?: number;
  limit?: number;
  trendBucketDays?: number;
}

/**
 * 추천 vs 실제 낙찰가 정확도 리포트 조회.
 *
 * 정산 완료(실제 낙찰가 보유) 건만 집계하는 실측 비교다 — would_have_won /
 * price-only 추정 지표와 달리 추천가(recommended_amount) ↔ 실제 낙찰가
 * (winning_amount) 의 실측 오차다. operator_id 는 단일 운영자(canonical)
 * 기본 경로를 따른다(별도 스코프 미지원).
 */
export function fetchAccuracyReport(
  params: AccuracyReportQuery = {},
  token?: string | null
): Promise<AccuracyReportResponse> {
  const search = new URLSearchParams();
  if (typeof params.days === "number") search.set("days", String(params.days));
  if (typeof params.limit === "number") search.set("limit", String(params.limit));
  if (typeof params.trendBucketDays === "number") {
    search.set("trend_bucket_days", String(params.trendBucketDays));
  }
  const qs = search.toString();
  const path = qs
    ? `/api/v1/analytics/accuracy-report?${qs}`
    : "/api/v1/analytics/accuracy-report";
  return wrap(
    apiRequest<AccuracyReportResponse>(path, { token }),
    "정확도 리포트를 불러오지 못했습니다."
  );
}
