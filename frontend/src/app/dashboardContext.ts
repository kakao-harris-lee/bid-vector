import { useOutletContext } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import type { DashboardSummaryResponse, RouteKey } from "@/shared/types";
import type { AuthSession } from "./layout/AuthGate";

export interface ShellOutletContext {
  summary: UseQueryResult<DashboardSummaryResponse, Error>;
  session: AuthSession | null;
  route: RouteKey;
  reloadKey: number;
}

export function useShellContext(): ShellOutletContext {
  return useOutletContext<ShellOutletContext>();
}
