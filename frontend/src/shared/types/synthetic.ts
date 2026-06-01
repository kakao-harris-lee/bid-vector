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
