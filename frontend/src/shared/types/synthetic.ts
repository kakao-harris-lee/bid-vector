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

/** 회사별 결과 (폴링 응답에 포함). */
export interface SyntheticExperimentResultItem {
  operator_slug: string;
  metrics?: Record<string, unknown>;
  settlement_sample?: unknown | null;
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
