import { useQuery } from "@tanstack/react-query";
import {
  fetchBids,
  fetchOpportunities,
  fetchPaperBiddingSummary,
  fetchResults,
  queryKeys
} from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";

export function useOpportunitiesQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.dashboard.opportunities(),
    queryFn: () => fetchOpportunities(session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useBidsQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.dashboard.bids(),
    queryFn: () => fetchBids(session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useResultsQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.dashboard.results(),
    queryFn: () => fetchResults(session?.token),
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
