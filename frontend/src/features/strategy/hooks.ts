import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchStrategy,
  fetchStrategyCandidates,
  fetchStrategyRuns,
  queryKeys,
  submitEligibilityFeedback,
  updateStrategy,
  type EligibilityFeedbackRequest,
  type EligibilityFeedbackResponse,
  type StrategyCandidatesQuery
} from "@/shared/api";
import { ApiError } from "@/shared/api/session";
import { toastApi } from "@/shared/components/ui";
import type {
  OperatorStrategyResponse,
  OperatorStrategyUpdatePayload
} from "@/shared/types/strategy";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * Strategy detail query — `null` operatorId hits the token-owner branch (no
 * query param). Privileged callers may pass another company's id to read its
 * watch rules; edits remain self-only (PR #74).
 */
export function useStrategyQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.strategy.detail(operatorId),
    queryFn: () => fetchStrategy(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useStrategyCandidatesQuery(
  session: AuthSession | null,
  params: StrategyCandidatesQuery = {},
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.strategy.candidates(
      params.limit,
      params.highPriorityOnly,
      operatorId
    ),
    queryFn: () => fetchStrategyCandidates(params, session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useStrategyRunsQuery(session: AuthSession | null, limit = 5) {
  return useQuery({
    queryKey: queryKeys.strategy.runs(limit),
    queryFn: () => fetchStrategyRuns(limit, session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useUpdateStrategyMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OperatorStrategyResponse, Error, OperatorStrategyUpdatePayload>({
    mutationFn: (payload) => updateStrategy(payload, session?.token),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.strategy.detail(null), data);
      // Broadcast — every cached variant (including impersonation reads).
      queryClient.invalidateQueries({ queryKey: ["strategy"] });
    }
  });
}

/**
 * Records the operator's daily eligibility judgement for a recommended notice
 * (적합/부적합/보류). The backend upserts an operator-source label, so we don't
 * invalidate the candidate list — the recommendation stays put while the caller
 * highlights the chosen verdict locally. Errors surface as Korean toasts;
 * silent 401s are handled by the session-expired modal, mirroring
 * `useApplyBidDecisionActionMutation`.
 */
export function useEligibilityFeedbackMutation(session: AuthSession | null) {
  return useMutation<EligibilityFeedbackResponse, Error, EligibilityFeedbackRequest>({
    mutationFn: (payload) => submitEligibilityFeedback(payload, session?.token),
    onSuccess: (data) => {
      toastApi.success({ title: `'${data.verdict}' 피드백을 저장했습니다` });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) return;
      toastApi.danger({
        title: "식별 피드백 실패",
        description: error instanceof Error ? error.message : "잠시 후 다시 시도해 주세요."
      });
    }
  });
}
