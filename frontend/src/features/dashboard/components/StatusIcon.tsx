import { Activity, AlertTriangle, CheckCircle2, Clock3 } from "lucide-react";
import type { DashboardStatus } from "@/shared/types";

export function StatusIcon({ status }: { status: DashboardStatus }) {
  if (status === "healthy") return <CheckCircle2 className="status-icon healthy" size={20} />;
  if (status === "critical") return <AlertTriangle className="status-icon critical" size={20} />;
  if (status === "watch") return <Clock3 className="status-icon watch" size={20} />;
  return <Activity className="status-icon info" size={20} />;
}
