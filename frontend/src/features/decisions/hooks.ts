import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchDecisionFunnel,
  fetchDecisionRecommendations,
  fetchOperationsKpi,
  queryKeys,
  updateBidDecisionStatus,
  type DecisionFunnelQuery,
  type OperationsKpiQuery
} from "@/shared/api";
import type {
  BidDecisionRecord,
  DecisionFunnelResponse,
  DecisionRecommendationResponse,
  DecisionStatus
} from "@/shared/types/decisions";
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

export function useUpdateDecisionStatusMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<
    BidDecisionRecord,
    Error,
    { decisionRecordId: number; decisionStatus: DecisionStatus },
    { previousFunnels: Array<[unknown, DecisionFunnelResponse | undefined]> }
  >({
    mutationFn: ({ decisionRecordId, decisionStatus }) =>
      updateBidDecisionStatus(decisionRecordId, decisionStatus, session?.token),
    onMutate: async ({ decisionRecordId, decisionStatus }) => {
      // Optimistic update: flip decision_status on any cached funnel snapshot
      await queryClient.cancelQueries({ queryKey: ["decisions", "funnel"] });
      const snapshots = queryClient.getQueriesData<DecisionFunnelResponse>({
        queryKey: ["decisions", "funnel"]
      });
      for (const [key, data] of snapshots) {
        if (!data) continue;
        queryClient.setQueryData<DecisionFunnelResponse>(key, {
          ...data,
          recent_submissions: data.recent_submissions.map((item) =>
            item.decision_record_id === decisionRecordId
              ? { ...item, decision_status: decisionStatus }
              : item
          )
        });
      }
      return { previousFunnels: snapshots };
    },
    onError: (_err, _variables, context) => {
      if (!context) return;
      for (const [key, data] of context.previousFunnels) {
        queryClient.setQueryData(key as readonly unknown[], data);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["decisions", "funnel"] });
      void queryClient.invalidateQueries({ queryKey: ["decisions", "recommendations"] });
    }
  });
}
