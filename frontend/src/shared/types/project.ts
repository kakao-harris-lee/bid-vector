export type ProjectStatus =
  | "open"
  | "re_notice"
  | "closed"
  | "awarded"
  | "failed"
  | "cancelled"
  | string;

export interface ProjectResponse {
  id: number;
  title: string;
  description: string;
  requirements: string;
  budget_estimate: number;
  category: string;
  notice_number?: string | null;
  source_url?: string | null;
  issuing_agency?: string | null;
  demand_agency?: string | null;
  status: ProjectStatus;
  created_at: string;
}

export interface SimilarProjectItem {
  project_id: number;
  title: string;
  category?: string | null;
  status: ProjectStatus;
  budget_estimate: number;
  deadline?: string | null;
  created_at: string;
  similarity_score: number;
  embedding_model?: string | null;
}

export interface ProjectSimilaritySearchResponse {
  target_project_id: number;
  target_project_title: string;
  target_embedding_model?: string | null;
  target_embedding_status?: "ready" | "pending" | "stale";
  target_embedding_updated_at?: string | null;
  target_embedding_refresh_required?: boolean;
  search_mode: "read_model" | "postgres_vector" | "python_fallback";
  same_category_only: boolean;
  min_similarity: number;
  result_count: number;
  results: SimilarProjectItem[];
}

export interface ProjectEmbeddingRefreshResponse {
  project_id: number;
  task_id: string;
  task_name: string;
  queue: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  detail: string;
  poll_url: string;
}

export type BidDecisionAction = "bid_now" | "review" | "skip";
export type BidDecisionStatus = "planned" | "reviewing" | "submitted" | "skipped";

export interface BidDecisionProjectSnapshot {
  id: number;
  title: string;
  category?: string | null;
  status: string;
  budget_estimate: number;
  deadline?: string | null;
  notice_number?: string | null;
  source_url?: string | null;
  issuing_agency?: string | null;
  demand_agency?: string | null;
}

export interface BidDecisionScoreBreakdown {
  probability_signal: number;
  matched_signal: number;
  urgency_signal: number;
  competitiveness_signal: number;
  budget_capture_signal: number;
  expected_margin_signal: number;
  execution_complexity_signal: number;
  active_load_ratio: number;
  workload_score_used: number;
  opportunity_score: number;
  auto_workload_penalty_multiplier: number;
  load_penalty: number;
  execution_complexity_penalty: number;
  total_penalty: number;
}

export interface BidDecisionRecordResponse {
  id: number;
  project_id: number;
  operator_id: number;
  action: BidDecisionAction;
  decision_status: BidDecisionStatus;
  priority_score: number;
  recommended_amount: number;
  probability_score: number;
  matched_score: number;
  reasoning: string;
  /** 추구 가능 근거 (왜 가능한가). 레거시 레코드는 누락될 수 있어 기본 []. */
  strengths?: string[];
  /** 리스크 신호 (왜 위험한가). 레거시 레코드는 누락될 수 있어 기본 []. */
  risk_flags?: string[];
  score_breakdown?: BidDecisionScoreBreakdown;
  created_at: string;
  updated_at: string;
}

export interface BidDecisionTimelineResponse {
  operator_id: number;
  project: BidDecisionProjectSnapshot;
  result_count: number;
  limit_applied: number;
  latest_decision_record_id?: number | null;
  timeline: BidDecisionRecordResponse[];
}

export interface ProjectListResult {
  items: ProjectResponse[];
  total: number;
}
