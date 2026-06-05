import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchStrategy,
  fetchStrategyCandidates,
  fetchStrategyRuns,
  queryKeys,
  updateStrategy,
  type StrategyCandidatesQuery
} from "@/shared/api";
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
