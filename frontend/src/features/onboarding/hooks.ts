import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyOnboardingSuggestions,
  fetchOnboardingSuggestions,
  queryKeys,
  type OnboardingApplyRequest,
  type OnboardingApplyResponse,
  type OnboardingSuggestionsQuery,
  type OnboardingSuggestionsResponse
} from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";

/**
 * 온보딩 후보 조회(react-query). seed 가 확정(제출)되기 전에는 `enabled=false` 로
 * 두어 자동 호출을 막는다 — 사용자가 seed 를 확정해야만 후보를 불러온다.
 * `features/strategy` 훅 패턴을 미러한다.
 */
export function useOnboardingSuggestions(
  session: AuthSession | null,
  query: OnboardingSuggestionsQuery | null,
  options: { enabled?: boolean } = {}
) {
  const enabled =
    Boolean(session?.token) &&
    query !== null &&
    query.keywords.length > 0 &&
    (options.enabled ?? true);
  return useQuery<OnboardingSuggestionsResponse>({
    queryKey: queryKeys.onboarding.suggestions(
      query?.keywords ?? [],
      query?.region ?? null,
      query?.minBudget ?? null,
      query?.maxBudget ?? null
    ),
    queryFn: () => fetchOnboardingSuggestions(query!, session?.token),
    enabled
  });
}

/**
 * 확정 후보 반영(mutation). 성공 시 프로필/전략/후보 미리보기 캐시를 무효화해
 * 확정 직후 공고 미리보기가 새 조건으로 갱신되게 한다(설계 §UI 4단계).
 * `strategy.candidates` 키는 `["strategy", ...]` 하위라 `["strategy"]` 무효화로 덮인다.
 */
export function useApplyOnboardingMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<OnboardingApplyResponse, Error, OnboardingApplyRequest>({
    mutationFn: (payload) => applyOnboardingSuggestions(payload, session?.token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["strategy"] });
    }
  });
}
