export type OperationsCardStatus = "healthy" | "watch" | "critical" | "info";

export interface OperationsDashboardCard {
  key: string;
  label: string;
  value: number;
  unit: "ratio" | "count";
  status: OperationsCardStatus;
  detail: string;
}

export interface CrawlFailureItem {
  crawl_job_id: number;
  source: string;
  target_date?: string | null;
  status: string;
  error_message?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface CrawlOperationsSummary {
  job_count: number;
  completed_count: number;
  failed_count: number;
  success_rate: number;
  failure_rate: number;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  failure_reason_breakdown: Record<string, number>;
  recent_failures: CrawlFailureItem[];
}

export interface NotificationOperationsSummary {
  notification_count: number;
  unread_count: number;
  decision_notification_count: number;
  bid_submission_notification_count: number;
  telegram_configured: boolean;
  telegram_delivery_attempt_count: number;
  telegram_sent_count: number;
  telegram_failed_count: number;
  telegram_success_rate: number;
  telegram_status: OperationsCardStatus;
  telegram_detail: string;
  telegram_failure_reason_breakdown: Record<string, number>;
}

export interface MLReleaseManifestSummaryItem {
  manifest_path: string;
  release_tag: string;
  validated_on?: string | null;
  signature_status: "verified" | "missing" | "invalid";
  gate_status: string;
  gate_passed?: boolean | null;
  detail: string;
}

export interface MLReleaseOperationsSummary {
  manifest_count: number;
  status: OperationsCardStatus;
  detail: string;
  latest_release_tag?: string | null;
  latest_signature_status: "verified" | "missing" | "invalid";
  latest_gate_status: string;
  latest_gate_passed?: boolean | null;
  backtest_status: OperationsCardStatus;
  backtest_detail: string;
  recent_manifests: MLReleaseManifestSummaryItem[];
}

export interface TaskOperationsSummary {
  // Loosely typed because Task* schema has many fields; we only show top-level diagnostics.
  [key: string]: unknown;
}

export interface OperationsDashboardResponse {
  operator_id: number;
  period_days: number;
  crawl: CrawlOperationsSummary;
  tasks: TaskOperationsSummary;
  notifications: NotificationOperationsSummary;
  ml_release: MLReleaseOperationsSummary;
  cards: OperationsDashboardCard[];
}
