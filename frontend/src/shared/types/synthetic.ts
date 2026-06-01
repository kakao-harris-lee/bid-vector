export interface SyntheticOperatorItem {
  user_id: number;
  username: string;
  slug: string;
  display_name: string;
  company?: string | null;
  business_type?: string | null;
  annual_revenue: number;
  capacity_score: number;
  bid_now_threshold: number;
  review_threshold: number;
  /**
   * 커스텀(웹에서 생성)이면 true. 프리셋/canonical operator는 false.
   * slug가 `custom-`으로 시작하는 회사가 커스텀이다.
   */
  is_custom: boolean;
}

export interface SyntheticOperatorListResponse {
  operator_count: number;
  operators: SyntheticOperatorItem[];
}

export interface SyntheticSeedResponse {
  seeded_count: number;
  purged_count: number;
  operators: SyntheticOperatorItem[];
}

export interface SyntheticBacktestSettlementItem {
  project_id?: number | null;
  project_title: string;
  category?: string | null;
  paper_bid_id?: number | null;
  decision_action?: string | null;
  bid_amount?: number | null;
  winning_amount?: number | null;
  absolute_bid_rate_error?: number | null;
  would_have_won: boolean;
  settled_at?: string | null;
}

export interface SyntheticBacktestOperatorResult extends SyntheticOperatorItem {
  candidate_count: number;
  paper_bid_count: number;
  settled_count: number;
  would_have_won_count: number;
  win_rate_on_settled?: number | null;
  bid_submission_rate?: number | null;
  average_absolute_bid_rate_error?: number | null;
  settlement_sample_count: number;
  settlement_items: SyntheticBacktestSettlementItem[];
}

export interface SyntheticBacktestRunResponse {
  operator_count: number;
  category?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  limit: number;
  scenario: string;
  results: SyntheticBacktestOperatorResult[];
}

export interface SyntheticBacktestRunRequest {
  start_at?: string;
  end_at?: string;
  category?: string;
  limit?: number;
  scenario?: string;
  slugs?: string[];
}

export interface SyntheticBacktestTaskResponse {
  task_id: string;
  task_name: string;
  queue: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  detail: string;
  poll_url: string;
}

export interface SyntheticBacktestTaskStatusResponse {
  task_id: string;
  task_name: string;
  queue: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  raw_status: string;
  ready: boolean;
  successful: boolean;
  detail: string;
  error?: string | null;
  result?: SyntheticBacktestRunResponse | null;
}

// --- Experiment Lab (Phase 1) -------------------------------------------------
// 보조 타입: 생성 파일 `openapi.d.ts`의 SyntheticExperiment* 스키마와 동일한 형태를
// 화면에서 다루기 쉽게 재선언한다(생성 파일은 수기 수정 금지).

export type SyntheticRunStatus = "queued" | "running" | "completed" | "failed";

/** 실험 정의 실행 파라미터 (persisted as JSON). */
export interface SyntheticExperimentParams {
  start_at?: string | null;
  end_at?: string | null;
  category?: string | null;
  limit: number;
  scenario: string;
  cutoff_hours?: number | null;
  history_limit?: number | null;
  settle_actions: boolean;
}

/** 실험 생성 요청 본문. */
export interface SyntheticExperimentCreateRequest {
  name: string;
  description?: string | null;
  params: SyntheticExperimentParams;
  operator_slugs?: string[] | null;
}

/** 실험 상세에 임베드되는 경량 런 요약. */
export interface SyntheticExperimentRunSummary {
  id: number;
  experiment_id: number;
  status: string;
  task_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  summary?: Record<string, unknown> | null;
  created_at?: string | null;
}

/** 실험 정의 + 런 이력. */
export interface SyntheticExperimentResponse {
  id: number;
  name: string;
  description?: string | null;
  params: SyntheticExperimentParams;
  operator_slugs?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  runs?: SyntheticExperimentRunSummary[];
}

// --- Experiment Lab breakdown (Phase 2) --------------------------------------
// 생성 파일 `openapi.d.ts`의 SyntheticExperimentBreakdown 스키마와 동형.
// 화면(리더보드/분해 시각화)에서 다루기 쉽게 보조 타입으로 재선언한다.

/** 예산구간 키 (KRW 기준). */
export type SyntheticBudgetBand =
  | "lt_1eok"
  | "1eok_5eok"
  | "5eok_10eok"
  | "10eok_50eok"
  | "gte_50eok";

/** 카테고리별 정산 집계. win_rate는 가격 기준 추정(would_have_won_count / settled_count). */
export interface SyntheticCategoryBreakdownItem {
  category: string;
  settled_count: number;
  would_have_won_count: number;
  /** settled_count=0이면 null. */
  win_rate?: number | null;
  avg_abs_bid_rate_error?: number | null;
}

/** 예산구간별 정산 집계. win_rate는 카테고리와 동일한 가격 기준 추정. */
export interface SyntheticBudgetBandBreakdownItem {
  /** SyntheticBudgetBand 중 하나(forward-compat 위해 string 허용). */
  budget_band: SyntheticBudgetBand | string;
  settled_count: number;
  would_have_won_count: number;
  win_rate?: number | null;
  avg_abs_bid_rate_error?: number | null;
}

/** 회사별 정산 분해(카테고리 + 예산구간). 레거시/빈 결과는 빈 배열. */
export interface SyntheticExperimentBreakdown {
  by_category?: SyntheticCategoryBreakdownItem[];
  by_budget_band?: SyntheticBudgetBandBreakdownItem[];
}

/** 리더보드에서 다루는 회사별 지표(metrics 일부를 명시적으로 타입화). */
export interface SyntheticExperimentMetrics {
  operator_slug?: string;
  candidate_count?: number | null;
  paper_bid_count?: number | null;
  settled_count?: number | null;
  would_have_won_count?: number | null;
  win_rate_on_settled?: number | null;
  bid_submission_rate?: number | null;
  average_absolute_bid_rate_error?: number | null;
  [key: string]: unknown;
}

/** 회사별 결과 (폴링 응답에 포함). */
export interface SyntheticExperimentResultItem {
  operator_slug: string;
  metrics?: SyntheticExperimentMetrics;
  settlement_sample?: unknown | null;
  breakdown?: SyntheticExperimentBreakdown;
}

/** 폴링 응답: 런 상태 + 완료 시 회사별 결과. */
export interface SyntheticExperimentRunResponse {
  id: number;
  experiment_id: number;
  status: string;
  task_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
  summary?: Record<string, unknown> | null;
  created_at?: string | null;
  results?: SyntheticExperimentResultItem[];
}

// --- Experiment Lab compare + export (Phase 4) -------------------------------
// 생성 파일 `openapi.d.ts`의 SyntheticExperimentCompare* 스키마와 동형.
// 화면(A/B 비교표)에서 다루기 쉽게 보조 타입으로 재선언한다(생성 파일은 수기 수정 금지).

/**
 * 비교 응답에 임베드되는 런 식별 + 요약. summary는 scenario/limit 등을 담는
 * 자유 형식 JSON(생성 타입은 빈 레코드라 화면용으로 느슨하게 재선언).
 */
export interface SyntheticExperimentCompareRunHeader {
  id: number;
  experiment_id: number;
  summary?: Record<string, unknown> | null;
}

/**
 * 한쪽(run A 또는 B)의 회사별 지표 슬라이스.
 * win_rate_on_settled는 가격 기준 추정(실제 낙찰 아님). 모든 필드 null 가능.
 */
export interface SyntheticExperimentCompareSide {
  win_rate_on_settled?: number | null;
  settled_count?: number | null;
  bid_submission_rate?: number | null;
  average_absolute_bid_rate_error?: number | null;
}

/** 부호 있는 delta(b-a, 양수=B 높음). 한쪽이 null이면 해당 delta도 null. */
export interface SyntheticExperimentCompareDelta {
  win_rate_on_settled?: number | null;
  bid_submission_rate?: number | null;
  average_absolute_bid_rate_error?: number | null;
}

/** 두 런 모두에 존재하는 한 회사의 A/B 지표 + delta. */
export interface SyntheticExperimentCompareOperator {
  operator_slug: string;
  a: SyntheticExperimentCompareSide;
  b: SyntheticExperimentCompareSide;
  delta: SyntheticExperimentCompareDelta;
}

/**
 * 두 완료 런의 A/B 비교(operator_slug 조인). operators는 교집합(정렬됨),
 * only_in_a/only_in_b는 한쪽에만 있는 slug. 두 런은 서로 다른 실험일 수 있다.
 */
export interface SyntheticExperimentCompareResponse {
  run_a: SyntheticExperimentCompareRunHeader;
  run_b: SyntheticExperimentCompareRunHeader;
  operators?: SyntheticExperimentCompareOperator[];
  only_in_a?: string[];
  only_in_b?: string[];
}

// --- Custom virtual companies (Phase 3) --------------------------------------
// 생성 파일 `openapi.d.ts`의 CustomOperator{Create,Update,CloneRequest,Detail,
// DeleteResponse} 스키마와 동형. 화면(빌더/관리)에서 다루기 쉽게 보조 타입으로
// 재선언한다(생성 파일은 수기 수정 금지).

/**
 * 회사 메타 + 전략 파라미터(생성/복제 공통 본문 필드).
 * 텍스트 리스트는 string[]로 전송한다. 모두 optional.
 */
export interface CustomOperatorFields {
  company_name?: string | null;
  business_type?: string | null;
  license_codes?: string[] | null;
  region_codes?: string[] | null;
  annual_revenue?: number | null;
  capacity_score?: number | null;
  focus_categories?: string[] | null;
  focus_regions?: string[] | null;
  exclude_regions?: string[] | null;
  required_keywords?: string[] | null;
  exclude_keywords?: string[] | null;
  min_budget_estimate?: number | null;
  max_budget_estimate?: number | null;
  minimum_match_score?: number | null;
  minimum_probability_score?: number | null;
  bid_now_threshold?: number | null;
  review_threshold?: number | null;
  max_recommended_candidates?: number | null;
}

/** 커스텀 회사 생성 요청 본문 (`name` 필수, slug 선택). */
export interface CustomOperatorCreateRequest extends CustomOperatorFields {
  name: string;
  slug?: string | null;
}

/** 커스텀 회사 복제 요청 본문 (override; source는 경로 slug). */
export interface CustomOperatorCloneRequest extends CustomOperatorFields {
  name?: string | null;
  slug?: string | null;
}

/** 커스텀 회사 부분 갱신 요청 본문 (전 필드 optional). */
export interface CustomOperatorUpdateRequest extends CustomOperatorFields {
  name?: string | null;
}

/**
 * 커스텀 회사 상세 (create/update/clone 응답).
 * `SyntheticOperatorItem`(is_custom 포함)의 슈퍼셋 + 전략 전 필드(리스트는 string[]).
 * 폼이 추가 fetch 없이 편집을 렌더할 수 있다.
 */
export interface CustomOperatorDetail {
  user_id: number;
  username: string;
  slug: string;
  is_custom: boolean;
  display_name: string;
  company?: string | null;
  business_type?: string | null;
  annual_revenue: number;
  capacity_score: number;
  license_codes?: string[];
  region_codes?: string[];
  focus_categories?: string[];
  focus_regions?: string[];
  exclude_regions?: string[];
  required_keywords?: string[];
  exclude_keywords?: string[];
  min_budget_estimate: number;
  max_budget_estimate: number;
  minimum_match_score: number;
  minimum_probability_score: number;
  bid_now_threshold: number;
  review_threshold: number;
  max_recommended_candidates: number;
}

/** 커스텀 회사 삭제 확인 응답. */
export interface CustomOperatorDeleteResponse {
  deleted: boolean;
  slug: string;
  username: string;
}
