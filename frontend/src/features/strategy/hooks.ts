import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchStrategy,
  fetchStrategyCandidates,
  fetchStrategyRuns,
  queryKeys,
  refreshStrategyCandidates,
  submitEligibilityFeedback,
  updateStrategy,
  type EligibilityFeedbackRequest,
  type EligibilityFeedbackResponse,
  type StrategyCandidatesQuery,
  type StrategyCandidatesRefreshQuery
} from "@/shared/api";
import { ApiError } from "@/shared/api/session";
import { toastApi } from "@/shared/components/ui";
import type {
  OperatorStrategyCandidatesRefreshResponse,
  OperatorStrategyResponse,
  OperatorStrategyUpdatePayload
} from "@/shared/types/strategy";
import type { AuthSession } from "@/app/layout/AuthGate";
import { SNAPSHOT_POLL_INTERVAL_MS, snapshotPollInterval } from "./snapshotState";

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

/**
 * 후보 스냅샷 조회 + terminal 게이트 폴링 (설계 2026-07-30 §7).
 *
 * PR-B 이후 이 GET 은 스냅샷 순수 읽기이고 재계산은 ops 큐 task 다. 그래서 폴링
 * 조건은 **응답 메타(서버 status)뿐**이다 — 로컬 "폴링 중" 플래그를 두지 않는다.
 * 이 카드의 키는 `["strategy", "candidates", ...]` 이므로 전략 저장·realtime
 * `strategy.monitor.*`·온보딩 apply 의 `["strategy"]` 전면 invalidate 가 쿼리를
 * 리셋하지만, 리셋 후에도 판정 근거가 같은 서버 응답이라 동작이 결정적이다.
 */
export function useStrategyCandidatesQuery(
  session: AuthSession | null,
  params: StrategyCandidatesQuery = {},
  operatorId: number | null = null,
  options: { pollIntervalMs?: number } = {}
) {
  const pollIntervalMs = options.pollIntervalMs ?? SNAPSHOT_POLL_INTERVAL_MS;
  return useQuery({
    queryKey: queryKeys.strategy.candidates(
      params.limit,
      params.highPriorityOnly,
      operatorId
    ),
    queryFn: () => fetchStrategyCandidates(params, session?.token, operatorId),
    enabled: Boolean(session?.token),
    refetchInterval: (query) =>
      snapshotPollInterval(
        query.state.data,
        query.state.status === "error",
        pollIntervalMs
      ),
    // 숨은 탭에서는 인터벌을 쉬게 하되(react-query 네이티브) 복귀 시 자동
    // 재개된다. `document.visibilityState` 직접 게이트는 전역
    // `refetchOnWindowFocus: false` 와 맞물려 복귀 후 폴링이 고착될 수 있어 쓰지
    // 않는다.
    refetchIntervalInBackground: false
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
 * 미리보기 스냅샷 재계산 명시 디스패치 (설계 §7 — 구 `query.refetch()` 대체).
 *
 * 202 는 "큐에 넣었다"이지 "끝났다"가 아니므로, 성공 응답의 `detail`(디스패치 /
 * 이미 실행 중 재사용 / 큐잉 실패가 서버 문구로 구분된다)을 그대로 보여주고 후보
 * 쿼리를 invalidate 한다. 그 뒤의 폴링은 다음 GET 이 돌려주는 `snapshot_status`
 * 가 켠다 — 로컬 플래그를 두지 않는다. 401 침묵은 세션 만료 모달 소관
 * (`useEligibilityFeedbackMutation` 패턴).
 */
export function useRefreshStrategyCandidatesMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<
    OperatorStrategyCandidatesRefreshResponse,
    Error,
    StrategyCandidatesRefreshQuery
  >({
    mutationFn: (params) => refreshStrategyCandidates(params, session?.token),
    onSuccess: (data) => {
      toastApi.info({ title: "미리보기 갱신 요청", description: data.detail });
      void queryClient.invalidateQueries({ queryKey: queryKeys.strategy.candidatesAll() });
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) return;
      toastApi.danger({
        title: "미리보기 갱신 실패",
        description: error instanceof Error ? error.message : "잠시 후 다시 시도해 주세요."
      });
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
