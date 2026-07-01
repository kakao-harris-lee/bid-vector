/**
 * 투찰서 초안 (Bid-form draft) export response types.
 *
 * PR8 / item 4-B. Hand-written mirror of `app/schemas/bid_form_draft.py`. The
 * generated `openapi.d.ts` does not yet carry `BidFormDraftResponse`
 * (regenerated via the `sync-types` skill right before merge). Once that lands,
 * these can be re-exported from `components["schemas"]["BidFormDraftResponse"]`
 * — the field names already match the backend snake_case contract 1:1, so the
 * swap is a type-only change with no call-site churn.
 *
 * 표시 정직화: `eligibility_estimate` 는 카테고리 낙찰하한율(참고) 대비 추천가
 * 위치에서 도출한 **추정 라벨**이며 실제 적격/낙찰 여부가 아니다. 이 초안은 자동
 * 제출 산출물이 아니다 — 운영자가 나라장터(KONEPS)에 직접 입력·제출한다.
 */

export interface BidFormDraftField {
  /** 안정적 식별 키(프론트 매핑용, 영문 snake_case). */
  key: string;
  /** 나라장터 입력 필드 라벨(한국어). */
  field_label: string;
  /** 표시용 값(포맷 적용된 문자열). 미정 시 빈 문자열. */
  value: string;
  /** 원시 수치(금액/비율). 비수치 항목은 null. */
  raw_value?: number | null;
  /** 항목별 정직 caveat(있으면). */
  note?: string | null;
}

export interface BidFormDraftResponse {
  decision_record_id: number;
  operator_id: number;
  /** 이 초안을 작성(집계)한 시각(UTC). */
  generated_at: string;

  notice_number?: string | null;
  title: string;
  demand_agency?: string | null;
  /** 기초금액(추정가격). 예정가/실하한가는 개찰 전 미공개. */
  budget_estimate: number;
  /** 추천 투찰금액(원). */
  recommended_amount: number;
  /** 추천 투찰률 = 추천 투찰가 / 추정가격. budget>0 일 때만. */
  recommended_bid_rate?: number | null;
  category?: string | null;
  business_type_label?: string | null;
  /** 투찰 마감일시. */
  deadline?: string | null;

  /** '적격 추정' / '하한 근접' / '하한 미만(주의)' / '판단 불가'. 실제 적격/낙찰 아님. */
  eligibility_estimate: string;
  eligibility_note: string;

  /**
   * 복수예비가격 추첨번호(2개) 무작위 편의 픽. 공고번호 seed 로 재현 가능.
   * 분석/유리가 아니며 개별 선택은 낙찰 결과에 영향 없음. 공고번호 부재 시 빈 배열.
   */
  lottery_numbers: number[];
  lottery_numbers_note: string;

  /** 나라장터 입력 항목 매핑(라벨+값) 리스트. 운영자가 그대로 입력. */
  fields: BidFormDraftField[];

  /** 자동 제출이 아니며 운영자가 직접 제출한다는 정직 안내 문구. */
  direct_submission_notice: string;
}
