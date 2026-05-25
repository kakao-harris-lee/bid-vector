export type ExperimentRunStatus = "planned" | "running" | "completed" | "rolled_back" | "failed";
export type ExperimentOutcome =
  | "insufficient_data"
  | "watch"
  | "success"
  | "rollback"
  | "inconclusive";
export type ExperimentReviewBucket =
  | "ready_to_apply"
  | "blocked"
  | "failed"
  | "needs_evaluation"
  | "collecting_data"
  | "partially_applied"
  | "scheduled"
  | "applied"
  | "unsupported";
export type ExperimentApplicationStatus =
  | "not_supported"
  | "not_ready"
  | "ready"
  | "partially_applied"
  | "applied"
  | "blocked";

export interface DecisionThresholdAdjustmentItem {
  parameter: "bid_now_threshold" | "review_threshold";
  label: string;
  direction: "increase" | "decrease";
  previous_value: number;
  suggested_value: number;
  delta: number;
  rationale: string;
}

export interface DecisionStrategyThresholdSnapshot {
  bid_now_threshold: number;
  review_threshold: number;
}

export interface DecisionExperimentThresholdApplyResponse {
  operator_id: number;
  run_id: number;
  experiment_key: string;
  recommendation_key: string;
  applied: boolean;
  dry_run: boolean;
  latest_outcome?: ExperimentOutcome | null;
  threshold_updates: DecisionThresholdAdjustmentItem[];
  strategy_thresholds: DecisionStrategyThresholdSnapshot;
  detail: string;
}

export interface DecisionExperimentRunSummary {
  id: number;
  operator_id: number;
  experiment_key: string;
  recommendation_key: string;
  status: ExperimentRunStatus;
  outcome?: ExperimentOutcome | null;
  priority_rank: number;
  title: string;
  hypothesis: string;
  suggested_change: string;
  target_metric: string;
  success_criteria: string;
  notes?: string | null;
  started_at: string;
  ended_at?: string | null;
  last_evaluated_at?: string | null;
  created_at: string;
  updated_at: string;
  supported_apply_types: Array<"thresholds" | "strategy">;
  applied_apply_types: Array<"thresholds" | "strategy">;
  application_status: ExperimentApplicationStatus;
  application_detail: string;
  review_bucket: ExperimentReviewBucket;
  review_priority: number;
  review_reason: string;
}

export interface DecisionExperimentRunListResponse {
  operator_id: number;
  result_count: number;
  total_match_count: number;
  sort: string;
  active_count: number;
  completed_count: number;
  rolled_back_count: number;
  failed_count: number;
  success_count: number;
  pending_count: number;
  ready_to_apply_count: number;
  applied_count: number;
  runs: DecisionExperimentRunSummary[];
}
