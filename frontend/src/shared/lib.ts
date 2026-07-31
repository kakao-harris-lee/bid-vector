import type {
  DashboardBidItem,
  DashboardMetric,
  DashboardOpportunityItem,
  DashboardStatus,
  DashboardWorkItem,
  PaperBiddingSettlementOverview
} from "@/shared/types";

export type ClassValue = string | false | null | undefined;
export type OpportunityFilter = "all" | "overdue" | "due_today" | "paper" | "decision";
export type OpportunitySort = "deadline" | "priority" | "amount";

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}

export function formatPercent(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatCurrency(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency",
    currency: "KRW",
    maximumFractionDigits: 0
  }).format(value);
}

export function formatCurrencyCompact(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return new Intl.NumberFormat("ko-KR", {
    notation: "compact",
    maximumFractionDigits: 1
  }).format(value);
}

export function formatDate(value: string | Date | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).format(date);
}

export function formatDateTime(value: string | Date | null | undefined): string {
  const date = parseDate(value);
  if (!date) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

/**
 * "3분 전" 형태의 상대 시각. 스냅샷 신선도 배지("N분 전 기준")가 쓴다.
 *
 * 하루를 넘기면 상대 표기가 정보를 잃으므로 KST 절대 시각(`formatDateTime`)으로
 * 폴백한다 — 포맷터를 새로 만들지 않고 기존 단일 출처를 재사용한다(§4.5-6).
 * 서버·클라이언트 시계 차이로 미래 시각이 오면 "-3분 전" 같은 거짓 표기 대신
 * "방금"으로 눕힌다. `now` 는 테스트 주입용(§4.7-3).
 */
export function formatRelativeTime(
  value: string | Date | null | undefined,
  now: Date = new Date()
): string {
  const date = parseDate(value);
  if (!date) return "-";
  const elapsedMs = now.getTime() - date.getTime();
  if (elapsedMs < 0) return "방금";
  const minutes = Math.floor(elapsedMs / 60_000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return formatDateTime(date);
}

export function formatHours(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  if (value < 0) return "마감 지남";
  if (value < 1) return `${Math.round(value * 60)}분`;
  if (value < 24) return `${Math.round(value)}시간`;
  return `${Math.floor(value / 24)}일`;
}

export function formatMetricValue(metric: DashboardMetric): string {
  const value = metric.value;
  if (typeof value === "string") return value;
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  const unit = String(metric.unit || "").toLowerCase();
  if (unit === "ratio" || unit === "percent" || unit === "%") {
    return formatPercent(value);
  }
  if (unit === "currency" || unit === "krw" || unit === "amount") {
    return formatCurrencyCompact(value);
  }
  return value.toLocaleString("ko-KR");
}

export function numberFromSummary(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function labelOpportunityStatus(item: DashboardOpportunityItem): string {
  if (item.decision_status === "submitted") return "투찰";
  if (item.decision_status === "reviewing") return "검토";
  if (item.decision_status === "skipped") return "보류";
  if (item.action === "bid_now") return "추천";
  if (item.action === "review") return "검토";
  return "제외";
}

export function statusFromOpportunity(item: DashboardOpportunityItem): DashboardStatus {
  if ((item.deadline_hours_remaining ?? 1) < 0) return "critical";
  if (item.decision_status === "submitted") return "healthy";
  if (item.action === "bid_now") return "watch";
  if (item.action === "review") return "info";
  return "info";
}

export function labelBidStatus(status: DashboardBidItem["status"]): string {
  const labels: Record<DashboardBidItem["status"], string> = {
    submitted: "제출",
    reviewed: "검토 완료",
    accepted: "수락",
    rejected: "탈락"
  };
  return labels[status] ?? status;
}

export function statusFromBid(status: DashboardBidItem["status"]): DashboardStatus {
  const map: Record<DashboardBidItem["status"], DashboardStatus> = {
    submitted: "info",
    reviewed: "watch",
    accepted: "healthy",
    rejected: "critical"
  };
  return map[status];
}

export { labelDecisionStatus } from "@/shared/constants/decisionLabels";

export function labelOutcome(status: string): string {
  const labels: Record<string, string> = {
    won: "낙찰",
    lost: "유찰",
    unknown: "확인 중"
  };
  return labels[status] ?? status;
}

export function statusFromOutcome(status: string): DashboardStatus {
  const map: Record<string, DashboardStatus> = {
    won: "healthy",
    lost: "watch"
  };
  return map[status] ?? "info";
}

export function labelWorkItemStatus(item: DashboardWorkItem): string {
  if (item.status) return item.status;
  const severityLabels: Record<DashboardWorkItem["severity"], string> = {
    critical: "긴급",
    watch: "주의",
    info: "정보"
  };
  return severityLabels[item.severity] ?? "정보";
}

export function statusFromSettlement(status: string): DashboardStatus {
  const map: Record<string, DashboardStatus> = {
    settled: "healthy",
    completed: "healthy",
    healthy: "healthy",
    ready: "healthy",
    failed: "critical",
    critical: "critical",
    error: "critical",
    pending: "watch",
    watch: "watch",
    waiting: "watch"
  };
  return map[status.toLowerCase()] ?? "info";
}

export function formatSettlementMilestone(
  overview: PaperBiddingSettlementOverview
): string {
  if (overview.latest_settled_at) {
    return `최근 정산 ${formatDateTime(overview.latest_settled_at)}`;
  }
  if (overview.next_deadline_at) {
    return `다음 마감 ${formatDateTime(overview.next_deadline_at)}`;
  }
  if (overview.next_confirmable_at) {
    return `다음 확인 ${formatDateTime(overview.next_confirmable_at)}`;
  }
  if (overview.oldest_waiting_deadline_at) {
    return `대기 마감 ${formatDateTime(overview.oldest_waiting_deadline_at)}`;
  }
  return "정산 일정 없음";
}

export function filterOpportunities(
  items: DashboardOpportunityItem[],
  filter: OpportunityFilter
): DashboardOpportunityItem[] {
  if (filter === "all") return items;
  if (filter === "paper") return items.filter((item) => item.source === "paper_bid");
  if (filter === "decision") return items.filter((item) => item.source === "decision");
  if (filter === "overdue") {
    return items.filter((item) => (item.deadline_hours_remaining ?? 1) < 0);
  }
  return items.filter((item) => {
    const hours = item.deadline_hours_remaining;
    return typeof hours === "number" && hours >= 0 && hours <= 24;
  });
}

export function sortOpportunities(
  items: DashboardOpportunityItem[],
  sort: OpportunitySort
): DashboardOpportunityItem[] {
  return [...items].sort((a, b) => {
    if (sort === "priority") return b.priority_score - a.priority_score;
    if (sort === "amount") return b.recommended_amount - a.recommended_amount;
    return deadlineValue(a) - deadlineValue(b);
  });
}

function deadlineValue(item: DashboardOpportunityItem): number {
  if (typeof item.deadline_hours_remaining === "number") {
    return item.deadline_hours_remaining;
  }
  const deadline = parseDate(item.project.deadline);
  return deadline ? deadline.getTime() : Number.MAX_SAFE_INTEGER;
}

function parseDate(value: string | Date | null | undefined): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
