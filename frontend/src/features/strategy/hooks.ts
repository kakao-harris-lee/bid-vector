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

export function useStrategyQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.strategy.detail(),
    queryFn: () => fetchStrategy(session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useStrategyCandidatesQuery(
  session: AuthSession | null,
  params: StrategyCandidatesQuery = {}
) {
  return useQuery({
    queryKey: queryKeys.strategy.candidates(params.limit, params.highPriorityOnly),
    queryFn: () => fetchStrategyCandidates(params, session?.token),
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
      queryClient.setQueryData(queryKeys.strategy.detail(), data);
      queryClient.invalidateQueries({ queryKey: ["strategy", "candidates"] });
    }
  });
}
