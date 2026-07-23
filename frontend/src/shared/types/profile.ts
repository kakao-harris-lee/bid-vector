export interface OperatorProfileResponse {
  operator_id: number;
  username: string;
  email: string;
  full_name?: string | null;
  company?: string | null;
  is_active: boolean;
  created_at: string;
  business_type: string;
  license_codes: string[];
  region_codes: string[];
  // cohort 정체성(기술부문/협회 가입) — license_codes/region_codes 와 동일한
  // 다중값 문자열. classifier eligibility 게이트(#237 협회·#238 기술부문)가 소비.
  tech_fields: string[];
  association_memberships: string[];
  annual_revenue: number;
  capacity_score: number;
  construction_capacity_amount: number;
  awarded_contract_limit: number;
  total_awards: number;
  profile_configured: boolean;
}

export interface OperatorProfileUpdatePayload {
  business_type?: string;
  license_codes?: string[];
  region_codes?: string[];
  // None(미전송)이면 불변, 넘어오면 갱신 — 백엔드 OperatorProfileUpdate 와 정합.
  tech_fields?: string[];
  association_memberships?: string[];
  annual_revenue?: number;
  capacity_score?: number;
  construction_capacity_amount?: number;
  awarded_contract_limit?: number;
  total_awards?: number;
}

export type PaperBiddingScenario = "conservative" | "base" | "aggressive";

export interface PaperBiddingRunRequestPayload {
  category?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  limit?: number;
  scenario?: PaperBiddingScenario;
  cutoff_hours_before_deadline?: number;
  history_limit?: number;
  persist?: boolean;
}

/**
 * Candidate item produced by the paper-bidding backtest. The backend returns a
 * loose `dict`, so only the fields the profile screen renders are typed; extras
 * are tolerated via the index signature.
 */
export interface PaperBiddingCandidateItem {
  project_id: number;
  project_title?: string | null;
  category?: string | null;
  budget_estimate?: number;
  action: string;
  matched_score?: number;
  probability_score?: number;
  priority_score?: number;
  reasoning?: string | null;
  [key: string]: unknown;
}

export interface PaperBiddingRunSummary {
  candidate_count?: number;
  paper_bid_count?: number;
  review_count?: number;
  skip_count?: number;
  action_counts?: Record<string, number>;
  [key: string]: unknown;
}

export interface PaperBiddingRunExecutionResponse {
  run_id?: number | null;
  request: Record<string, unknown>;
  summary: PaperBiddingRunSummary;
  items: PaperBiddingCandidateItem[];
  settlements: Array<Record<string, unknown>>;
}
