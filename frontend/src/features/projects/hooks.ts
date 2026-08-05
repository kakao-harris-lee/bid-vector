import { useMutation, useQuery } from "@tanstack/react-query";
import {
  fetchBidDecisionTimeline,
  fetchProject,
  fetchProjectList,
  fetchSimilarProjectsRefreshStatus,
  fetchSimilarProjects,
  queryKeys,
  refreshSimilarProjects,
  type ProjectListQuery,
  type SimilarProjectsQuery
} from "@/shared/api";
import type {
  BidDecisionTimelineResponse,
  ProjectDetailResponse,
  ProjectListResult,
  ProjectSimilaritySearchResponse,
  SimilarProjectsRefreshOperationResponse,
  SimilarProjectsRefreshOperationStatusResponse
} from "@/shared/types/project";
import type { AuthSession } from "@/app/layout/AuthGate";
import { similarProjectsRefreshPollInterval } from "./similarProjectsRefreshState";

export function useProjectsQuery(session: AuthSession | null, query: ProjectListQuery) {
  return useQuery<ProjectListResult, Error>({
    queryKey: queryKeys.projects.list(query),
    queryFn: () => fetchProjectList(query, session?.token),
    enabled: Boolean(session?.token),
    placeholderData: (previous) => previous
  });
}

export function useProjectQuery(session: AuthSession | null, id: number | null) {
  return useQuery<ProjectDetailResponse, Error>({
    queryKey: id !== null ? queryKeys.projects.detail(id) : ["projects", "detail", "none"],
    queryFn: () => fetchProject(id as number, session?.token),
    enabled: Boolean(session?.token) && id !== null
  });
}

export function useSimilarProjectsQuery(
  session: AuthSession | null,
  id: number | null,
  params: SimilarProjectsQuery = {}
) {
  return useQuery<ProjectSimilaritySearchResponse, Error>({
    queryKey:
      id !== null
        ? queryKeys.projects.similar(id, params)
        : ["projects", "similar", "none"],
    queryFn: () => fetchSimilarProjects(id as number, params, session?.token),
    enabled: Boolean(session?.token) && id !== null
  });
}

export function useTimelineQuery(
  session: AuthSession | null,
  id: number | null,
  limit = 10
) {
  return useQuery<BidDecisionTimelineResponse, Error>({
    queryKey:
      id !== null
        ? queryKeys.projects.timeline(id, limit)
        : ["projects", "timeline", "none"],
    queryFn: () => fetchBidDecisionTimeline(id as number, { limit }, session?.token),
    enabled: Boolean(session?.token) && id !== null
  });
}

export function useRefreshSimilarProjectsMutation(session: AuthSession | null) {
  return useMutation<
    SimilarProjectsRefreshOperationResponse,
    Error,
    { id: number; force?: boolean }
  >({
    mutationFn: ({ id, force }) =>
      refreshSimilarProjects(id, { force }, session?.token)
  });
}

export function useSimilarProjectsRefreshStatusQuery(
  session: AuthSession | null,
  operation: SimilarProjectsRefreshOperationResponse | null
) {
  return useQuery<SimilarProjectsRefreshOperationStatusResponse, Error>({
    queryKey: operation
      ? queryKeys.projects.similarRefreshOperation(operation.operation_id)
      : ["projects", "similar-refresh", "none"],
    queryFn: () =>
      fetchSimilarProjectsRefreshStatus(operation?.poll_url ?? "", session?.token),
    enabled: Boolean(session?.token) && operation !== null,
    refetchInterval: (query) => similarProjectsRefreshPollInterval(query.state.data?.status)
  });
}
