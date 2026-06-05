export type DashboardStatus = "healthy" | "watch" | "critical" | "info";
export type WorkItemType = "opportunity_due" | "bid_pending_result" | "result_review";

export interface DashboardProjectBrief {
  project_id: number;
  title: string;
  category?: string | null;
  notice_number?: string | null;
  issuing_agency?: string | null;
  demand_agency?: string | null;
  budget_estimate: number;
  deadline?: string | null;
  status: string;
}

export interface DashboardMetric {
  key: string;
  label: string;
  value: number | string | null;
  unit: string;
  status: DashboardStatus;
  detail: string;
}

export interface DashboardWorkItem {
  key: string;
  item_type: WorkItemType;
  severity: "info" | "watch" | "critical";
  title: string;
  subtitle: string;
  project_id?: number | null;
  due_at?: string | null;
  status: string;
  href: string;
}

export interface DashboardOpportunityItem {
  source: "decision" | "paper_bid";
  source_label: string;
  decision_record_id?: number | null;
  paper_bid_id?: number | null;
  project: DashboardProjectBrief;
  action: "bid_now" | "review" | "skip";
  decision_status: "planned" | "reviewing" | "submitted" | "skipped";
  recommended_amount: number;
  probability_score: number;
  matched_score: number;
  priority_score: number;
  urgency_score: number;
  deadline_hours_remaining?: number | null;
  reasoning: string;
  /** 추구 가능 근거 (왜 가능한가). 레거시 항목은 누락될 수 있어 기본 []. */
  strengths?: string[];
  /** 리스크 신호 (왜 위험한가). 레거시 항목은 누락될 수 있어 기본 []. */
  risk_flags?: string[];
  updated_at: string;
  detail_href: string;
}

export interface DashboardBidItem {
  bid_id: number;
  project: DashboardProjectBrief;
  decision_record_id?: number | null;
  decision_status?: "planned" | "reviewing" | "submitted" | "skipped" | null;
  bid_amount: number;
  recommended_amount?: number | null;
  proposed_timeline: number;
  status: "submitted" | "reviewed" | "accepted" | "rejected";
  score?: number | null;
  submitted_at: string;
  updated_at: string;
  detail_href: string;
}

export interface DashboardResultItem {
  tender_result_id: number;
  project: DashboardProjectBrief;
  winning_company?: string | null;
  winning_amount: number;
  winning_rate: number;
  result_status: string;
  award_outcome: "won" | "lost" | "unknown";
  announced_at?: string | null;
  latest_prediction_id?: number | null;
  predicted_price?: number | null;
  prediction_delta_amount?: number | null;
  prediction_error_rate?: number | null;
  latest_decision_record_id?: number | null;
  recommended_amount?: number | null;
  recommendation_delta_amount?: number | null;
  recommendation_error_rate?: number | null;
  detail_href: string;
}

export interface DashboardSectionSummary {
  key: "opportunities" | "bids" | "results";
  label: string;
  count: number;
  status: DashboardStatus;
  href: string;
}

export interface DashboardSummaryResponse {
  operator_id: number;
  /**
   * Operator the response is actually scoped to. Same as ``operator_id`` when
   * the caller is reading their own data; differs when a privileged user
   * impersonates another company via ``?operator_id=``. Optional for backward
   * compatibility with cached / hand-rolled fixtures.
   */
  current_operator_id?: number;
  current_operator_username?: string;
  generated_at: string;
  today: string;
  operational_status: DashboardMetric;
  metrics: DashboardMetric[];
  work_items: DashboardWorkItem[];
  sections: DashboardSectionSummary[];
  recent_opportunities: DashboardOpportunityItem[];
  recent_bids: DashboardBidItem[];
  recent_results: DashboardResultItem[];
  realtime_href: string;
}

export interface DashboardListResponse<T> {
  operator_id: number;
  current_operator_id?: number;
  current_operator_username?: string;
  generated_at: string;
  returned_count: number;
  limit: number;
  items: T[];
}

export interface PaperBiddingSettlementOverview {
  status: string;
  label: string;
  detail: string;
  settlement_basis: string;
  paper_bid_count: number;
  settled_count: number;
  unsettled_count: number;
  ready_to_settle_count: number;
  waiting_result_count: number;
  before_deadline_count: number;
  missing_deadline_count: number;
  next_confirmable_at?: string | null;
  next_deadline_at?: string | null;
  oldest_waiting_deadline_at?: string | null;
  latest_settled_at?: string | null;
}

export interface PaperBiddingRunListItem {
  id: number;
  operator_id: number;
  status: string;
  mode: "historical_backtest" | "forward_paper" | string;
  scenario: string;
  category_filter?: string | null;
  strategy_version: string;
  model_version: string;
  target_start_at?: string | null;
  target_end_at?: string | null;
  data_cutoff_policy: string;
  started_at?: string | null;
  completed_at?: string | null;
  candidate_count: number;
  paper_bid_count: number;
  settled_count: number;
  settlement_overview?: PaperBiddingSettlementOverview;
  summary: Record<string, number | string | null | undefined>;
}

export interface PaperBiddingSummaryResponse {
  operator_id: number;
  latest_run?: PaperBiddingRunListItem | null;
  run_count: number;
  completed_count: number;
  latest_summary: Record<string, number | string | null | undefined>;
}

export type RouteKey = "home" | "opportunities" | "bids" | "results";
export type ListItem = DashboardOpportunityItem | DashboardBidItem | DashboardResultItem;
