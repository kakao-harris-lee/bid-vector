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

export interface SyntheticBacktestOperatorResult extends SyntheticOperatorItem {
  candidate_count: number;
  paper_bid_count: number;
  settled_count: number;
  would_have_won_count: number;
  win_rate_on_settled?: number | null;
  bid_submission_rate?: number | null;
  average_absolute_bid_rate_error?: number | null;
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
