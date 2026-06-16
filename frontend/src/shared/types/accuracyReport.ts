import type { components } from "./openapi.d";

// 추천 vs 실제 낙찰가 정확도 리포트 (GET /api/v1/analytics/accuracy-report).
// 생성된 OpenAPI 스키마에서 alias 해서 백엔드 계약과 자동 동기화한다 —
// openapi.d.ts 는 수기 편집 금지(sync-types 스킬로만 재생성).
export type AccuracyReportResponse = components["schemas"]["AccuracyReportResponse"];
export type AccuracyReportSummary = components["schemas"]["AccuracyReportSummary"];
export type AccuracyReportErrorBin = components["schemas"]["AccuracyReportErrorBin"];
export type AccuracyReportCategory = components["schemas"]["AccuracyReportCategory"];
export type AccuracyReportTrendBucket = components["schemas"]["AccuracyReportTrendBucket"];
// 상세 drill-down 항목은 PredictionFeedback 과 동일 스키마를 공유한다.
export type AccuracyReportItem = components["schemas"]["PredictionFeedbackItem"];
