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
 * `budget_estimate`(추정가격)와 `bid_base_amount`(투찰 기준금액)는 다른 금액이다.
 */

import type { FloorShortfallEstimate } from "@/shared/types/floorShortfall";

export interface BidSummaryNoticeMeta {
  project_id: number;
  title: string;
  notice_number?: string | null;
  category?: string | null;
  business_type_label?: string | null;
  /**
   * 추정가격(부가세 별도 표기). **투찰 기준금액이 아니다** — 투찰율은
   * `bid_base_amount` 에 곱해진다. 예정가/실하한가는 개찰 전 미공개.
   */
  budget_estimate: number;
  /** 투찰 기준금액(기초금액/사업금액, 과세 공고는 부가세 포함). */
  bid_base_amount: number;
  /** 기초금액 출처(clean-base / reserve-estimate / base-fallback / budget-estimate-fallback). */
  bid_base_source?: string | null;
  /** 기초금액 ÷ 추정가격. 추정가격이 0 이면 null. */
  bid_base_to_estimate_ratio?: number | null;
  /** 두 금액이 왜 다른지 설명하는 서버 문구(단일 출처). */
  bid_base_note: string;
  demand_agency?: string | null;
  issuing_agency?: string | null;
  deadline?: string | null;
  source_url?: string | null;
  status?: string | null;
}

export interface BidSummaryRecommendation {
  /**
   * 추천 투찰가(원) — 나라장터에 그대로 입력하는 제출값. 기초금액 기준으로 산정되므로
   * 과세 공고에서는 부가세가 포함된 금액이다.
   */
  recommended_amount: number;
  /** 추천 투찰가 / 추정가격 — **참고 지표**. 적격심사가 보는 율이 아니다. */
  recommended_bid_rate?: number | null;
  /**
   * 추천 투찰가 / 기초금액 — 카테고리 **참고** 하한율과 같은 basis 라 그 참고 비교는 이
   * 값으로 한다. 실제 낙찰하한가는 개찰 시 추첨된 예정가격 기준이라 참고 하한 위여도
   * 실격일 수 있고, 그 괴리 위험은 floor_shortfall 이 표시한다.
   */
  recommended_bid_rate_on_base?: number | null;
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
  /**
   * 추천 투찰가가 낙찰하한 미달이 됐을 과거 표본 빈도(실격 확률 아님). 이 필드가 null
   * 이면 산출 경로가 동작하지 않은 것이고, 내부 `shortfall_frequency` 가 null 이면
   * "위험 없음"이 아니라 "판정 불가"다.
   */
  floor_shortfall?: FloorShortfallEstimate | null;
  /** 분야 통계(없거나 미산출 시 null — graceful). */
  field_stat?: BidSummaryFieldStat | null;
  /** 실제 나라장터 투찰서 제출은 운영자가 직접 진행한다는 안내 문구. */
  direct_submission_notice: string;
}
