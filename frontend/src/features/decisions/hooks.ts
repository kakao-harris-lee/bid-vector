import { useQuery } from "@tanstack/react-query";
import {
  fetchAccuracyReport,
  fetchDecisionFunnel,
  fetchDecisionRecommendations,
  fetchDecisionSamples,
  fetchOperationsKpi,
  queryKeys,
  type AccuracyReportQuery,
  type DecisionFunnelQuery,
  type DecisionSamplesQuery,
  type OperationsKpiQuery
} from "@/shared/api";
import type {
  DecisionFunnelResponse,
  DecisionRecommendationResponse
} from "@/shared/types/decisions";
import type { AccuracyReportResponse } from "@/shared/types/accuracyReport";
import type { DecisionSamplesResponse } from "@/shared/types/decisionSamples";
import type { OperationsKpiResponse } from "@/shared/types/operations";
import type { AuthSession } from "@/app/layout/AuthGate";

export function useDecisionFunnelQuery(session: AuthSession | null, query: DecisionFunnelQuery) {
  return useQuery<DecisionFunnelResponse, Error>({
    queryKey: queryKeys.decisions.funnel(query),
    queryFn: () => fetchDecisionFunnel(query, session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useDecisionRecommendationsQuery(
  session: AuthSession | null,
  query: DecisionFunnelQuery
) {
  return useQuery<DecisionRecommendationResponse, Error>({
    queryKey: queryKeys.decisions.recommendations(query),
    queryFn: () => fetchDecisionRecommendations(query, session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useAccuracyReportQuery(
  session: AuthSession | null,
  params: AccuracyReportQuery = {}
) {
  return useQuery<AccuracyReportResponse, Error>({
    queryKey: queryKeys.analytics.accuracyReport(params),
    queryFn: () => fetchAccuracyReport(params, session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useDecisionSamplesQuery(
  session: AuthSession | null,
  params: DecisionSamplesQuery = {}
) {
  return useQuery<DecisionSamplesResponse, Error>({
    queryKey: queryKeys.operations.decisionSamples(params),
    queryFn: () => fetchDecisionSamples(params, session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useOperationsKpiQuery(
  session: AuthSession | null,
  query: OperationsKpiQuery = {},
  operatorId: number | null = null
) {
  return useQuery<OperationsKpiResponse, Error>({
    queryKey: queryKeys.operations.kpi(query, operatorId),
    queryFn: () => fetchOperationsKpi(query, session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

// NOTE: The legacy `useUpdateDecisionStatusMutation` (PATCH /status) was removed
// when the DecisionsScreen unified onto the same submit/review/skip action API
// the main dashboard already uses (POST /actions, see
// `useApplyBidDecisionActionMutation` in `features/dashboard/hooks.ts`). The
// underlying `updateBidDecisionStatus` API function is intentionally retained in
// `shared/api/decisions.ts` because the PATCH /status endpoint still exists on
// the backend.
