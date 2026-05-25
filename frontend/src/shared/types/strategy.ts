export type StrategyAction = "bid_now" | "review" | "skip";

export interface OperatorStrategyResponse {
  operator_id: number;
  focus_categories: string[];
  focus_regions: string[];
  exclude_regions: string[];
  required_keywords: string[];
  exclude_keywords: string[];
  min_budget_estimate: number;
  max_budget_estimate: number;
  minimum_match_score: number;
  minimum_probability_score: number;
  bid_now_threshold: number;
  review_threshold: number;
  auto_workload_penalty_multiplier: number;
  category_priority_overrides: Record<string, number>;
  notify_only_high_priority: boolean;
  max_recommended_candidates: number;
  strategy_configured: boolean;
}

export interface OperatorStrategyUpdatePayload {
  focus_categories?: string[];
  focus_regions?: string[];
  exclude_regions?: string[];
  required_keywords?: string[];
  exclude_keywords?: string[];
  min_budget_estimate?: number;
  max_budget_estimate?: number;
  minimum_match_score?: number;
  minimum_probability_score?: number;
  bid_now_threshold?: number;
  review_threshold?: number;
  auto_workload_penalty_multiplier?: number;
  category_priority_overrides?: Record<string, number>;
  notify_only_high_priority?: boolean;
  max_recommended_candidates?: number;
}

export interface OperatorStrategyCandidateItem {
  project_id: number;
  title: string;
  category?: string | null;
  budget_estimate: number;
  deadline?: string | null;
  matched_score: number;
  probability_score: number;
  priority_score: number;
  action: StrategyAction;
  recommended_amount: number;
  analysis_summary: string;
  strategy_reasons: string[];
}

export interface OperatorStrategyCandidatesResponse {
  operator_id: number;
  evaluated_project_count: number;
  returned_candidate_count: number;
  high_priority_only: boolean;
  candidates: OperatorStrategyCandidateItem[];
}

export type StrategyRunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface OperatorStrategyRunItem {
  id: number;
  operator_id: number;
  task_id?: string | null;
  trigger_source: string;
  status: StrategyRunStatus;
  high_priority_only: boolean;
  limit_applied: number;
  evaluated_project_count: number;
  selected_candidate_count: number;
  persisted_candidate_count: number;
  notification_count: number;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface OperatorStrategyRunListResponse {
  operator_id: number;
  result_count: number;
  runs: OperatorStrategyRunItem[];
}
