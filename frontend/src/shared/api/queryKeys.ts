import type { ProjectListQuery, SimilarProjectsQuery } from "./projects";

export const queryKeys = {
  dashboard: {
    summary: () => ["dashboard", "summary"] as const,
    opportunities: () => ["dashboard", "opportunities"] as const,
    bids: () => ["dashboard", "bids"] as const,
    results: () => ["dashboard", "results"] as const,
    paperSummary: () => ["dashboard", "paper-summary"] as const
  },
  strategy: {
    detail: () => ["strategy", "detail"] as const,
    candidates: (limit?: number, highPriorityOnly?: boolean) =>
      ["strategy", "candidates", { limit: limit ?? null, highPriorityOnly: highPriorityOnly ?? null }] as const,
    runs: (limit: number) => ["strategy", "runs", { limit }] as const
  },
  projects: {
    list: (query: ProjectListQuery) => ["projects", "list", normalizeListKey(query)] as const,
    detail: (id: number) => ["projects", "detail", id] as const,
    similar: (id: number, params: SimilarProjectsQuery) =>
      ["projects", "similar", id, normalizeSimilarKey(params)] as const,
    timeline: (id: number, limit: number) => ["projects", "timeline", id, { limit }] as const
  }
} as const;

function normalizeListKey(query: ProjectListQuery): Record<string, unknown> {
  return {
    q: query.q?.trim() || null,
    category: query.category || null,
    status: query.status || null,
    agency: query.agency?.trim() || null,
    budgetMin: typeof query.budgetMin === "number" ? query.budgetMin : null,
    budgetMax: typeof query.budgetMax === "number" ? query.budgetMax : null,
    skip: query.skip ?? 0,
    limit: query.limit ?? 20
  };
}

function normalizeSimilarKey(params: SimilarProjectsQuery): Record<string, unknown> {
  return {
    limit: params.limit ?? null,
    minSimilarity: params.minSimilarity ?? null,
    sameCategoryOnly: params.sameCategoryOnly ?? null
  };
}

export type QueryKeyOf<TFn extends (...args: never[]) => readonly unknown[]> = ReturnType<TFn>;
