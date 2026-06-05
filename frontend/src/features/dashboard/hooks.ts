import { useQuery } from "@tanstack/react-query";
import {
  fetchBids,
  fetchOpportunities,
  fetchPaperBiddingSummary,
  fetchResults,
  queryKeys
} from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * Dashboard list queries scope themselves to the active operator-id passed
 * from the Shell context. `null` keeps the historical behaviour (token owner
 * branch on the backend) so single-user sessions are unaffected. Every key
 * embeds `operatorId` so React Query reads/writes a separate cache slot per
 * company — switching companies in the header invalidates the prefix and
 * triggers a refetch with the new `?operator_id=`.
 */

export function useOpportunitiesQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.dashboard.opportunities(operatorId),
    queryFn: () => fetchOpportunities(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useBidsQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.dashboard.bids(operatorId),
    queryFn: () => fetchBids(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useResultsQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.dashboard.results(operatorId),
    queryFn: () => fetchResults(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function usePaperSummaryQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.dashboard.paperSummary(),
    queryFn: () => fetchPaperBiddingSummary(session?.token),
    enabled: Boolean(session?.token)
  });
}
