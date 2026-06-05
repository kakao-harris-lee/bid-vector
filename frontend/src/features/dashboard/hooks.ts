import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyBidDecisionAction,
  fetchBids,
  fetchOpportunities,
  fetchPaperBiddingSummary,
  fetchResults,
  queryKeys,
  type ApplyBidDecisionActionArgs,
  type BidDecisionRecordResponse
} from "@/shared/api";
import { ApiError } from "@/shared/api/session";
import { toastApi } from "@/shared/components/ui";
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

const ACTION_SUCCESS_TITLE: Record<BidDecisionRecordResponse["action"], string> = {
  bid_now: "투찰 결정으로 전환했습니다",
  review: "검토 상태로 전환했습니다",
  skip: "보류 처리했습니다"
};

/**
 * Inline action mutation used by `OpportunityRow` and `DetailDrawer`.
 *
 * On success we toast and invalidate every operator-scoped dashboard prefix so
 * the cards re-fetch under the active `operator_id=`. We deliberately skip
 * optimistic updates — submit/review/skip can shift across multiple list
 * sections (opportunities ↔ bids ↔ results) and computing the post-state
 * client-side is harder to get right than just refetching. Errors surface as
 * Korean toasts using the messages already translated by `applyBidDecisionAction`.
 */
export function useApplyBidDecisionActionMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<
    BidDecisionRecordResponse,
    Error,
    Omit<ApplyBidDecisionActionArgs, "token">
  >({
    mutationFn: ({ decisionRecordId, action, operatorId }) =>
      applyBidDecisionAction({
        decisionRecordId,
        action,
        operatorId: operatorId ?? null,
        token: session?.token
      }),
    onSuccess: (record) => {
      toastApi.success({
        title: ACTION_SUCCESS_TITLE[record.action] ?? "결정 액션을 적용했습니다"
      });
      // Operator-scoped dashboard prefixes — every list key starts with these
      // tuples regardless of the appended `{ operatorId }` segment, so prefix
      // matching invalidates every cached operator slot at once.
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "summary"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "opportunities"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "bids"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "results"] });
      // Decision funnels / operations KPI also include this record — refresh.
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["operations"] });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        // session-expired modal already raised by client.ts — stay quiet.
        return;
      }
      toastApi.danger({
        title: "결정 액션 실패",
        description: error instanceof Error ? error.message : "잠시 후 다시 시도해 주세요."
      });
    }
  });
}
