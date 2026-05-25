export type DecisionAction = "bid_now" | "review" | "skip";
export type DecisionStatus = "planned" | "reviewing" | "submitted" | "skipped";

export interface DecisionFunnelBreakdownItem {
  key: string;
  label?: string | null;
  decision_count: number;
  submitted_count: number;
  active_pending_count?: number | null;
  submission_rate?: number | null;
}

export interface DecisionFunnelTrendItem {
  bucket_start: string;
  bucket_end: string;
  decision_count: number;
  submitted_count: number;
  submission_rate?: number | null;
}

export interface DecisionFunnelRecentSubmissionItem {
  decision_record_id: number;
  project_id: number;
  project_title: string;
  category?: string | null;
  action: DecisionAction;
  decision_status: DecisionStatus;
  priority_score: number;
  recommended_amount: number;
  updated_at: string;
}

export interface DecisionFunnelComparisonSummary {
  decision_count_delta: number;
  submitted_count_delta: number;
  submission_rate_delta?: number | null;
}

export interface DecisionFunnelResponse {
  operator_id: number;
  period_days: number;
  decision_count: number;
  project_count: number;
  active_pending_count: number;
  submitted_count: number;
  skipped_count: number;
  entry_bid_now_count: number;
  entry_review_count: number;
  entry_skip_count: number;
  direct_submitted_count: number;
  submitted_after_bid_now_count: number;
  submitted_after_review_count: number;
  submitted_after_skip_count: number;
  overall_submission_rate?: number | null;
  workflow_submission_rate?: number | null;
  bid_now_submission_rate?: number | null;
  review_submission_rate?: number | null;
  average_hours_to_submit?: number | null;
  current_period_start: string;
  current_period_end: string;
  comparison: DecisionFunnelComparisonSummary;
  trend_bucket_days: number;
  breakdown_limit_applied: number;
  trend: DecisionFunnelTrendItem[];
  category_breakdown: DecisionFunnelBreakdownItem[];
  workload_source_breakdown: DecisionFunnelBreakdownItem[];
  agency_breakdown: DecisionFunnelBreakdownItem[];
  recent_submissions: DecisionFunnelRecentSubmissionItem[];
}

export interface DecisionRecommendationItem {
  key: string;
  severity: "info" | "watch" | "action";
  title: string;
  summary: string;
  suggested_adjustment?: string | null;
  supporting_metrics: Record<string, unknown>;
  priority_score: number;
  parameter_recommendation: Record<string, unknown>;
}

export interface DecisionRecommendationResponse {
  operator_id: number;
  period_days: number;
  decision_count: number;
  submitted_count: number;
  active_pending_count: number;
  overall_submission_rate?: number | null;
  workflow_submission_rate?: number | null;
  bid_now_submission_rate?: number | null;
  review_submission_rate?: number | null;
  recommendation_count: number;
  recommendation_limit_applied: number;
  experiment_count: number;
  headline: string;
  comparison: DecisionFunnelComparisonSummary;
  recommendations: DecisionRecommendationItem[];
}

export interface BidDecisionRecord {
  id: number;
  project_id: number;
  operator_id: number;
  action: DecisionAction;
  decision_status: DecisionStatus;
  priority_score: number;
  recommended_amount: number;
  probability_score: number;
  matched_score: number;
  reasoning: string;
  created_at: string;
  updated_at: string;
}
