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
  search_mode: "postgres_vector" | "python_fallback";
  same_category_only: boolean;
  min_similarity: number;
  result_count: number;
  results: SimilarProjectItem[];
}

export interface ProjectEmbeddingRefreshResponse {
  project_id: number;
  title: string;
  category?: string | null;
  embedding_model?: string | null;
  semantic_text_length: number;
  embedding_dimensions: number;
  embedding_updated_at?: string | null;
  vector_storage_enabled: boolean;
  vector_persisted: boolean;
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
