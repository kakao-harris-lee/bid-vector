import { httpErrorMessage } from "./httpErrorMessages";
import { ApiError, getStoredToken } from "./session";
import type {
  BidDecisionTimelineResponse,
  ProjectEmbeddingRefreshResponse,
  ProjectEmbeddingTaskStatusResponse,
  ProjectListResult,
  ProjectResponse,
  ProjectSimilaritySearchResponse
} from "@/shared/types/project";

export interface ProjectListQuery {
  q?: string;
  category?: string;
  status?: string;
  agency?: string;
  budgetMin?: number;
  budgetMax?: number;
  skip?: number;
  limit?: number;
}

function buildProjectListSearch(query: ProjectListQuery): URLSearchParams {
  const search = new URLSearchParams();
  if (query.q?.trim()) search.set("q", query.q.trim());
  if (query.category) search.set("category", query.category);
  if (query.status) search.set("status", query.status);
  if (query.agency?.trim()) search.set("agency", query.agency.trim());
  if (typeof query.budgetMin === "number" && Number.isFinite(query.budgetMin)) {
    search.set("budget_min", String(query.budgetMin));
  }
  if (typeof query.budgetMax === "number" && Number.isFinite(query.budgetMax)) {
    search.set("budget_max", String(query.budgetMax));
  }
  if (typeof query.skip === "number") search.set("skip", String(query.skip));
  if (typeof query.limit === "number") search.set("limit", String(query.limit));
  return search;
}

async function rawFetch<T>(
  path: string,
  options: RequestInit & { token?: string | null } = {}
): Promise<{ data: T; response: Response }> {
  const { token: explicitToken, headers, ...rest } = options;
  const token = explicitToken ?? getStoredToken();
  const finalHeaders: Record<string, string> = { ...(headers as Record<string, string> | undefined) };
  if (token) finalHeaders.Authorization = `Bearer ${token}`;

  const response = await fetch(path, { ...rest, headers: finalHeaders });
  if (!response.ok) {
    if (response.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event("bid-vector:session-expired"));
    }
    throw new ApiError(response.status, httpErrorMessage(response.status));
  }
  if (response.status === 204) return { data: undefined as T, response };
  const data = (await response.json()) as T;
  return { data, response };
}

function wrap<T>(promise: Promise<T>, fallback: string): Promise<T> {
  return promise.catch((err) => {
    if (err instanceof ApiError && err.status !== 401) {
      throw new ApiError(err.status, fallback);
    }
    throw err;
  });
}

export async function fetchProjectList(
  query: ProjectListQuery = {},
  token?: string | null
): Promise<ProjectListResult> {
  const search = buildProjectListSearch(query);
  const qs = search.toString();
  const path = qs ? `/api/v1/projects/?${qs}` : "/api/v1/projects/";
  return wrap(
    (async () => {
      const { data, response } = await rawFetch<ProjectResponse[]>(path, { token });
      const totalHeader = response.headers.get("X-Total-Count");
      const total = totalHeader ? Number.parseInt(totalHeader, 10) : data.length;
      return { items: data, total: Number.isFinite(total) ? total : data.length };
    })(),
    "공고 목록을 불러오지 못했습니다."
  );
}

export function fetchProject(id: number, token?: string | null): Promise<ProjectResponse> {
  return wrap(
    rawFetch<ProjectResponse>(`/api/v1/projects/${id}`, { token }).then((res) => res.data),
    "공고 상세를 불러오지 못했습니다."
  );
}

export interface SimilarProjectsQuery {
  limit?: number;
  minSimilarity?: number;
  sameCategoryOnly?: boolean;
}

export function fetchSimilarProjects(
  id: number,
  params: SimilarProjectsQuery = {},
  token?: string | null
): Promise<ProjectSimilaritySearchResponse> {
  const search = new URLSearchParams();
  if (typeof params.limit === "number") search.set("limit", String(params.limit));
  if (typeof params.minSimilarity === "number") {
    search.set("min_similarity", String(params.minSimilarity));
  }
  if (typeof params.sameCategoryOnly === "boolean") {
    search.set("same_category_only", String(params.sameCategoryOnly));
  }
  const qs = search.toString();
  const path = qs
    ? `/api/v1/projects/${id}/similar?${qs}`
    : `/api/v1/projects/${id}/similar`;
  return wrap(
    rawFetch<ProjectSimilaritySearchResponse>(path, { token }).then((res) => res.data),
    "유사 공고를 불러오지 못했습니다."
  );
}

export function refreshProjectEmbedding(
  id: number,
  options: { force?: boolean } = {},
  token?: string | null
): Promise<ProjectEmbeddingRefreshResponse> {
  const path = options.force
    ? `/api/v1/projects/${id}/embedding/refresh?force=true`
    : `/api/v1/projects/${id}/embedding/refresh`;
  return wrap(
    rawFetch<ProjectEmbeddingRefreshResponse>(path, { method: "POST", token }).then(
      (res) => res.data
    ),
    "임베딩 재계산에 실패했습니다."
  );
}

export function fetchProjectEmbeddingTaskStatus(
  pollUrl: string,
  token?: string | null
): Promise<ProjectEmbeddingTaskStatusResponse> {
  return wrap(
    rawFetch<ProjectEmbeddingTaskStatusResponse>(pollUrl, { token }).then(
      (res) => res.data
    ),
    "임베딩 작업 상태를 확인하지 못했습니다."
  );
}

export function fetchBidDecisionTimeline(
  projectId: number,
  options: { limit?: number } = {},
  token?: string | null
): Promise<BidDecisionTimelineResponse> {
  const search = new URLSearchParams();
  if (typeof options.limit === "number") search.set("limit", String(options.limit));
  const qs = search.toString();
  const path = qs
    ? `/api/v1/operations/projects/${projectId}/bid-decision-timeline?${qs}`
    : `/api/v1/operations/projects/${projectId}/bid-decision-timeline`;
  return wrap(
    rawFetch<BidDecisionTimelineResponse>(path, { token }).then((res) => res.data),
    "결정 타임라인을 불러오지 못했습니다."
  );
}
