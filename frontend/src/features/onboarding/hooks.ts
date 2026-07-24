import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  applyOnboardingSuggestions,
  fetchOnboardingHistory,
  fetchOnboardingSuggestions,
  queryKeys,
  verifyBusinessNumber,
  type BusinessVerificationRequest,
  type BusinessVerificationResponse,
  type OnboardingApplyPayload,
  type OnboardingApplyResponse,
  type OnboardingHistoryQuery,
  type OnboardingSuggestionHistoryResponse,
  type OnboardingSuggestionsQuery,
  type OnboardingSuggestionsResponse
} from "@/shared/api";
import type { AuthSession } from "@/app/layout/AuthGate";

const HISTORY_PAGE_SIZE = 20;

/**
 * 사업자번호 상태조회/진위확인(mutation, #248). `useSendBidReportEmailMutation` 하우스
 * 스타일을 미러한다 — **queryKey 없음**이라 사용자가 입력한 원문 번호는 캐시/쿼리키에
 * 남지 않고, 변수(payload)는 mutation 수명 동안에만 존재한다(§2 정직 명세). 응답은
 * masked 번호만 담으므로 표시는 응답값으로만 한다(원문 재노출 금지).
 */
export function useVerifyBusinessNumberMutation(session: AuthSession | null) {
  return useMutation<BusinessVerificationResponse, Error, BusinessVerificationRequest>({
    mutationFn: (payload) => verifyBusinessNumber(payload, session?.token)
  });
}

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
  return useMutation<OnboardingApplyResponse, Error, OnboardingApplyPayload>({
    mutationFn: (payload) => applyOnboardingSuggestions(payload, session?.token),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profile"] });
      queryClient.invalidateQueries({ queryKey: ["strategy"] });
    }
  });
}

/**
 * 온보딩 결정 감사 이력 조회(react-query). 세션 토큰이 있으면 자동 조회하며,
 * `useOnboardingSuggestions` 와 동일한 패턴을 미러한다. 페이지 전환 시 이전 페이지를
 * placeholder 로 유지해 깜빡임을 줄인다(`useProjectsQuery` 패턴).
 */
export function useOnboardingHistory(
  session: AuthSession | null,
  query: OnboardingHistoryQuery
) {
  return useQuery<OnboardingSuggestionHistoryResponse>({
    queryKey: queryKeys.onboarding.history(
      query.field ?? null,
      query.status ?? null,
      query.limit ?? HISTORY_PAGE_SIZE,
      query.offset ?? 0
    ),
    queryFn: () => fetchOnboardingHistory(query, session?.token),
    enabled: Boolean(session?.token),
    placeholderData: (previous) => previous
  });
}
