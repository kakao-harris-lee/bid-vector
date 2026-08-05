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

/**
 * 공고 상세 — 목록 응답에 투찰 기준금액(기초금액)을 더한 형태
 * (`app/schemas/project.py::ProjectDetailResponse`).
 *
 * `budget_estimate`(추정가격, 부가세 별도 표기)와 `bid_base_amount`(투찰율이 실제로
 * 곱해지는 기초금액/사업금액)는 **다른 금액**이다. 목록에는 기초금액이 없고 상세에만
 * 있다 — 운영자가 금액 basis 를 확인해야 하는 자리가 상세 화면이기 때문이다.
 */
export interface ProjectDetailResponse extends ProjectResponse {
  /** 투찰 기준금액(기초금액/사업금액, 과세 공고는 부가세 포함). */
  bid_base_amount: number;
  /** 기초금액 출처(clean-base / reserve-estimate / base-fallback / budget-estimate-fallback). */
  bid_base_source?: string | null;
  /** 기초금액 ÷ 추정가격. 추정가격이 0 이면 null. */
  bid_base_to_estimate_ratio?: number | null;
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
}

export interface ProjectSimilaritySearchResponse {
  target_project_id: number;
  target_project_title: string;
  same_category_only: boolean;
  min_similarity: number;
  result_count: number;
  results: SimilarProjectItem[];
}

export type SimilarProjectsRefreshStatus =
  | "accepted"
  | "in_progress"
  | "succeeded"
  | "failed"
  | "cancelled";

export interface SimilarProjectsRefreshOperationResponse {
  project_id: number;
  operation_id: string;
  operation: "refresh_similar_projects";
  status: SimilarProjectsRefreshStatus;
  message: string;
  poll_url: string;
}

export interface SimilarProjectsRefreshOperationStatusResponse {
  project_id: number;
  operation_id: string;
  operation: "refresh_similar_projects";
  status: SimilarProjectsRefreshStatus;
  is_terminal: boolean;
  succeeded: boolean;
  message: string;
  error?: string | null;
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
