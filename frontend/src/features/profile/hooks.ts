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

export function useProfileQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.profile.detail(),
    queryFn: () => fetchProfile(session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useProfileStrategyQuery(session: AuthSession | null) {
  return useQuery({
    queryKey: queryKeys.strategy.detail(),
    queryFn: () => fetchStrategy(session?.token),
    enabled: Boolean(session?.token)
  });
}

export function useUpdateProfileMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OperatorProfileResponse, Error, OperatorProfileUpdatePayload>({
    mutationFn: (payload) => updateProfile(payload, session?.token),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.profile.detail(), data);
    }
  });
}

export function useUpdateProfileStrategyMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OperatorStrategyResponse, Error, OperatorStrategyUpdatePayload>({
    mutationFn: (payload) => updateStrategy(payload, session?.token),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.strategy.detail(), data);
      queryClient.invalidateQueries({ queryKey: ["strategy", "candidates"] });
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
