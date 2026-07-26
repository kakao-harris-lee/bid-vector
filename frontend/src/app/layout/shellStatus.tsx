import { Activity, AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import type { DashboardStatus } from "@/shared/types";

export function StatusIcon({ status }: { status: DashboardStatus }) {
  if (status === "healthy") return <CheckCircle2 className="text-[var(--color-success)]" size={20} />;
  if (status === "critical") return <AlertTriangle className="text-[var(--color-danger)]" size={20} />;
  if (status === "watch") return <Clock3 className="text-[var(--color-warn)]" size={20} />;
  return <Activity className="text-[var(--color-info)]" size={20} />;
}

export function toneFromStatus(
  status: DashboardStatus | "critical" | "watch" | "info"
): "info" | "healthy" | "watch" | "critical" {
  if (status === "healthy") return "healthy";
  if (status === "critical") return "critical";
  if (status === "watch") return "watch";
  return "info";
}
