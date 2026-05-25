import type { DashboardStatus } from "@/shared/types";

export function StatusBadge({
  status,
  label
}: {
  status: DashboardStatus | "critical" | "watch" | "info";
  label: string;
}) {
  return <span className={`status-badge ${status}`}>{label}</span>;
}
