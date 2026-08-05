/**
 * 하한 미달 빈도(과거 표본) 응답 타입 — `app/schemas/prediction.py::FloorShortfallEstimate`
 * 의 hand-written 미러. 투찰 요약·투찰서 초안 두 응답이 같은 객체를 실어 보내므로
 * 타입은 여기 한 곳에만 둔다(도메인 타입 단일 출처).
 *
 * 정직 명세(§2): 이 값은 **실격 확률이 아니다**. 추천가는 기초금액 기준이고 실격 하한은
 * 추첨된 예정가격 기준이라, 같은 추천가도 사정률 추첨 결과에 따라 하한 위/아래로 갈린다.
 * 여기 담기는 것은 과거 개찰 표본에서 그 경계를 넘긴 **표본 비율**뿐이다.
 *
 * `shortfall_frequency === null` 은 "위험 없음"이 아니라 **"판정 불가"** 다
 * (`unmeasurable_reason` 참조). 0 과 같은 표시로 합치지 않는다.
 */
export interface FloorShortfallEstimate {
  /** 이 추천율이 낙찰하한 미달이 됐을 과거 표본 비율(0-1). null 이면 판정 불가. */
  shortfall_frequency?: number | null;
  /** 임계 사정률을 초과한 표본 수(빈도의 분자). */
  shortfall_sample_count: number;
  /** 빈도 산출에 실제로 쓰인 사정률 표본 수(분모). */
  sample_count: number;
  /** 빈도를 발표하기 위해 요구한 최소 표본 수(이 미만이면 판정 불가). */
  minimum_sample_count: number;
  /** 임계 사정률 = 추천 투찰율 ÷ 낙찰하한율. 사정률이 이 값을 넘으면 하한 미달. */
  critical_assessment_rate?: number | null;
  /** 표본을 고른 기준(오염 필터·카테고리·기준일) 요약. */
  scope: string;
  /** 판정 불가 사유. 측정된 경우 null. */
  unmeasurable_reason?: string | null;
}
