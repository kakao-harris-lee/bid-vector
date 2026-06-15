/**
 * 투찰 의사결정 요약 (Bid decision summary) response types.
 *
 * PR7 / item 4-A. Hand-written mirror of `app/schemas/bid_summary.py`. The
 * generated `openapi.d.ts` does not yet carry `BidSummaryResponse` (regenerated
 * via the `sync-types` skill right before merge). Once that lands, these can be
 * re-exported from `components["schemas"]["BidSummaryResponse"]` — the field
 * names already match the backend snake_case contract 1:1, so the swap is a
 * type-only change with no call-site churn.
 *
 * 표시 정직화: `probability_score` 는 **가격 적합도(추정)** 이며 P(낙찰)이 아니다.
 * `category_floor.floor_bid_rate` 는 가드레일 설정값(참고)이지 실하한가가 아니다.
 */

export interface BidSummaryNoticeMeta {
  project_id: number;
  title: string;
  notice_number?: string | null;
  category?: string | null;
  business_type_label?: string | null;
  /** 공고 추정가격(예산). 예정가/실하한가는 개찰 전 미공개. */
  budget_estimate: number;
  demand_agency?: string | null;
  issuing_agency?: string | null;
  deadline?: string | null;
  source_url?: string | null;
  status?: string | null;
}

export interface BidSummaryRecommendation {
  /** 추천 투찰가(원). */
  recommended_amount: number;
  /** 추천 투찰가 / 추정가격. budget_estimate>0 일 때만 산출. */
  recommended_bid_rate?: number | null;
  /** 가격 적합도(추정) — P(낙찰) 아님. */
  probability_score: number;
  /** bid_now / review / skip 중 현재 판단. */
  action: string;
  /** planned / reviewing / submitted / skipped 워크플로 상태. */
  decision_status: string;
  priority_score: number;
  matched_score: number;
  competitiveness_score: number;
  reasoning: string;
  strengths: string[];
  risk_flags: string[];
}

export interface BidSummaryPrediction {
  predicted_price?: number | null;
  predicted_bid_rate?: number | null;
  price_range_min?: number | null;
  price_range_max?: number | null;
  confidence_score?: number | null;
  pricing_mode?: string | null;
  predictor_name?: string | null;
  guardrail_applied: boolean;
  /** 예측이 적용한 낙찰하한 가드레일 투찰률(있으면). */
  floor_bid_rate?: number | null;
  created_at?: string | null;
}

export interface BidSummaryCategoryFloor {
  category?: string | null;
  business_group?: string | null;
  /** 카테고리/그룹 최소 투찰률(참고). 미설정 시 null. */
  floor_bid_rate?: number | null;
  /** budget_estimate * floor_bid_rate 참고 하한가. 예정가 기준 아님. */
  floor_price?: number | null;
  note: string;
}

export interface BidSummaryFieldStat {
  category?: string | null;
  /** 해당 분야 백테스트 표본 수. */
  settled_count: number;
  /** 가격 근접 추정율 (would_have_won_price_only / settled). 실제 낙찰 아님. */
  est_price_close_rate?: number | null;
  /** 적격성 게이트 추정 적격율 (unknown 제외 분모). */
  eligible_favorable_rate?: number | null;
  source_run_id?: number | null;
  source_operator_slug?: string | null;
  note: string;
}

export interface BidSummaryResponse {
  decision_record_id: number;
  operator_id: number;
  generated_at: string;
  notice: BidSummaryNoticeMeta;
  recommendation: BidSummaryRecommendation;
  prediction?: BidSummaryPrediction | null;
  category_floor: BidSummaryCategoryFloor;
  /** 분야 통계(없거나 미산출 시 null — graceful). */
  field_stat?: BidSummaryFieldStat | null;
  /** 실제 나라장터 투찰서 제출은 운영자가 직접 진행한다는 안내 문구. */
  direct_submission_notice: string;
}
