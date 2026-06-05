import { useOutletContext } from "react-router-dom";
import type { UseQueryResult } from "@tanstack/react-query";
import type { DashboardSummaryResponse, RouteKey } from "@/shared/types";
import type { AuthSession } from "./layout/AuthGate";
import type { ActiveOperator } from "./operatorContext";

export interface ShellOutletContext {
  summary: UseQueryResult<DashboardSummaryResponse, Error>;
  session: AuthSession | null;
  route: RouteKey;
  reloadKey: number;
  /**
   * Active operator descriptor. Screens read `activeOperatorId` to scope their
   * queries and pass it to `?operator_id=`; `null` means "fall back to the
   * token owner". See `operatorContext.ts` for full semantics.
   */
  activeOperator: ActiveOperator;
}

export function useShellContext(): ShellOutletContext {
  return useOutletContext<ShellOutletContext>();
}
