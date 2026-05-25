import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchBidDecisionTimeline,
  fetchProject,
  fetchProjectList,
  fetchSimilarProjects,
  queryKeys,
  refreshProjectEmbedding,
  type ProjectListQuery,
  type SimilarProjectsQuery
} from "@/shared/api";
import type {
  BidDecisionTimelineResponse,
  ProjectEmbeddingRefreshResponse,
  ProjectListResult,
  ProjectResponse,
  ProjectSimilaritySearchResponse
} from "@/shared/types/project";
import type { AuthSession } from "@/app/layout/AuthGate";

export function useProjectsQuery(session: AuthSession | null, query: ProjectListQuery) {
  return useQuery<ProjectListResult, Error>({
    queryKey: queryKeys.projects.list(query),
    queryFn: () => fetchProjectList(query, session?.token),
    enabled: Boolean(session?.token),
    placeholderData: (previous) => previous
  });
}

export function useProjectQuery(session: AuthSession | null, id: number | null) {
  return useQuery<ProjectResponse, Error>({
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

export function useRefreshEmbeddingMutation(session: AuthSession | null) {
  const queryClient = useQueryClient();
  return useMutation<ProjectEmbeddingRefreshResponse, Error, { id: number; force?: boolean }>({
    mutationFn: ({ id, force }) => refreshProjectEmbedding(id, { force }, session?.token),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["projects", "similar", variables.id] });
      queryClient.invalidateQueries({ queryKey: ["projects", "detail", variables.id] });
    }
  });
}
