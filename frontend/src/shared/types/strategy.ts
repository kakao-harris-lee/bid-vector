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

/**
 * preview 스냅샷 상태 (백엔드 `OperatorPreviewSnapshot.status`).
 *
 * `idle` = 마지막 재계산이 성공했다(또는 아직 시작 전), `running` = ops 큐 task
 * 가 재계산 중, `failed` = 마지막 재계산이 실패했다. **`failed` 여도
 * `candidates`/`computed_at` 은 직전 성공분이 그대로 살아 있다**(설계 §6.2).
 */
export type SnapshotStatus = "idle" | "running" | "failed";

export interface OperatorStrategyCandidatesResponse {
  operator_id: number;
  /**
   * 스냅샷을 계산할 때 실제로 분석한 공고 수. **요청 limit 과 무관**하고 스냅샷의
   * 고정 분석 예산(PREVIEW_SCAN_CEILING=250)의 산물이다 — 표시 라벨도 그렇게
   * 붙인다(설계 §6.1: limit 은 키 차원이 아니라 서빙 슬라이스).
   */
  evaluated_project_count: number;
  returned_candidate_count: number;
  high_priority_only: boolean;
  candidates: OperatorStrategyCandidateItem[];
  /** 마지막 **성공** 계산 시각(ISO). `null` = 계산된 적 없음(부트스트랩). */
  computed_at?: string | null;
  snapshot_status: SnapshotStatus;
  /**
   * 저장된 계산이 낡았는가(시간 경과 **또는** 전략 편집 후 미재계산).
   *
   * 주의: `true` 가 "재계산이 큐에 있다"를 보장하지 않는다 — 실패 쿨다운(60s)
   * 동안은 stale 을 보고하면서 자동 디스패치가 억제된다. 또 `computed_at === null`
   * 은 `stale: false` 로 온다("낡음"이 아니라 "부트스트랩"). 신선도 분기는
   * `computed_at`/`snapshot_status` 로 하고 `stale` 단독으로 하지 않는다.
   */
  stale: boolean;
}

/**
 * `POST /operator/strategy/candidates/refresh` 202 응답.
 *
 * 별도 task-status 엔드포인트는 없다 — `poll_url` 이 후보 GET 자신을 가리키고
 * 폴링은 그 재조회로 한다(설계 §6.2). `detail` 은 디스패치/스킵 사유를 한국어로
 * 담으므로 그대로 사용자에게 보여준다.
 */
export interface OperatorStrategyCandidatesRefreshResponse {
  task_id?: string | null;
  operator_id: number;
  current_operator_id: number;
  current_operator_username: string;
  high_priority_only: boolean;
  snapshot_status: SnapshotStatus;
  detail: string;
  poll_url: string;
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
