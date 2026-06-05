import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchProfile,
  fetchStrategy,
  queryKeys,
  runProfileBacktest,
  updateProfile,
  updateStrategy
} from "@/shared/api";
import type {
  OperatorProfileResponse,
  OperatorProfileUpdatePayload,
  PaperBiddingRunExecutionResponse,
  PaperBiddingRunRequestPayload
} from "@/shared/types/profile";
import type {
  OperatorStrategyResponse,
  OperatorStrategyUpdatePayload
} from "@/shared/types/strategy";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * Profile detail query.
 *
 * When `operatorId` is `null`/`undefined` the request hits `/operator/profile`
 * without `?operator_id=` so the backend falls back to the token owner. With a
 * concrete id, privileged callers can read another company's 5-axis detail
 * (GET = cross-operator on PR #74). Edits stay self-only — see the mutation.
 */
export function useProfileQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.profile.detail(operatorId),
    queryFn: () => fetchProfile(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useProfileStrategyQuery(
  session: AuthSession | null,
  operatorId: number | null = null
) {
  return useQuery({
    queryKey: queryKeys.strategy.detail(operatorId),
    queryFn: () => fetchStrategy(session?.token, operatorId),
    enabled: Boolean(session?.token)
  });
}

export function useUpdateProfileMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OperatorProfileResponse, Error, OperatorProfileUpdatePayload>({
    mutationFn: (payload) => updateProfile(payload, session?.token),
    onSuccess: (data) => {
      // Seed the canonical (operatorId=null) cache and broadcast-invalidate
      // every operator-scoped variant so impersonation views see the fresh row.
      queryClient.setQueryData(queryKeys.profile.detail(null), data);
      queryClient.invalidateQueries({ queryKey: ["profile"] });
    }
  });
}

export function useUpdateProfileStrategyMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OperatorStrategyResponse, Error, OperatorStrategyUpdatePayload>({
    mutationFn: (payload) => updateStrategy(payload, session?.token),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.strategy.detail(null), data);
      queryClient.invalidateQueries({ queryKey: ["strategy"] });
    }
  });
}

export function useProfileBacktestMutation(session: AuthSession | null) {
  return useMutation<
    PaperBiddingRunExecutionResponse,
    Error,
    PaperBiddingRunRequestPayload
  >({
    mutationFn: (payload) => runProfileBacktest(payload, session?.token)
  });
}
