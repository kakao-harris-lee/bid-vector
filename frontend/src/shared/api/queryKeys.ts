import type { ProjectListQuery, SimilarProjectsQuery } from "./projects";
import type { DecisionFunnelQuery } from "./decisions";
import type { ExperimentListQuery } from "./experiments";
import type { OperationsKpiQuery } from "./operations";

/**
 * Active operator-id is appended as a separate key segment so React Query's
 * fuzzy invalidation (`["dashboard", "summary"]`) still matches every cached
 * variant. `null` means "fall back to the token owner" — the URL omits the
 * `operator_id=` query param entirely.
 */
type OperatorScope = number | null;

export const queryKeys = {
  operator: {
    accounts: () => ["operator", "accounts"] as const
  },
  dashboard: {
    summary: (operatorId: OperatorScope = null) =>
      ["dashboard", "summary", { operatorId }] as const,
    opportunities: (operatorId: OperatorScope = null) =>
      ["dashboard", "opportunities", { operatorId }] as const,
    bids: (operatorId: OperatorScope = null) =>
      ["dashboard", "bids", { operatorId }] as const,
    results: (operatorId: OperatorScope = null) =>
      ["dashboard", "results", { operatorId }] as const,
    paperSummary: () => ["dashboard", "paper-summary"] as const
  },
  profile: {
    detail: (operatorId: OperatorScope = null) =>
      ["profile", "detail", { operatorId }] as const
  },
  strategy: {
    detail: (operatorId: OperatorScope = null) =>
      ["strategy", "detail", { operatorId }] as const,
    candidates: (
      limit?: number,
      highPriorityOnly?: boolean,
      operatorId: OperatorScope = null
    ) =>
      [
        "strategy",
        "candidates",
        { limit: limit ?? null, highPriorityOnly: highPriorityOnly ?? null },
        { operatorId }
      ] as const,
    runs: (limit: number) => ["strategy", "runs", { limit }] as const
  },
  projects: {
    list: (query: ProjectListQuery) => ["projects", "list", normalizeProjectListKey(query)] as const,
    detail: (id: number) => ["projects", "detail", id] as const,
    similar: (id: number, params: SimilarProjectsQuery) =>
      ["projects", "similar", id, normalizeSimilarKey(params)] as const,
    timeline: (id: number, limit: number) => ["projects", "timeline", id, { limit }] as const
  },
  decisions: {
    funnel: (query: DecisionFunnelQuery) => ["decisions", "funnel", normalizeFunnelKey(query)] as const,
    recommendations: (query: DecisionFunnelQuery) =>
      ["decisions", "recommendations", normalizeFunnelKey(query)] as const,
    summary: (decisionRecordId: number) =>
      ["decisions", "summary", decisionRecordId] as const
  },
  experiments: {
    list: (query: ExperimentListQuery) => ["experiments", "list", normalizeExperimentKey(query)] as const
  },
  operations: {
    dashboard: (operatorId: OperatorScope = null) =>
      ["operations", "dashboard", { operatorId }] as const,
    kpi: (query: OperationsKpiQuery, operatorId: OperatorScope = null) =>
      ["operations", "kpi", normalizeOperationsKpiKey(query), { operatorId }] as const
  }
} as const;

function normalizeProjectListKey(query: ProjectListQuery): Record<string, unknown> {
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

function normalizeFunnelKey(query: DecisionFunnelQuery): Record<string, unknown> {
  return {
    days: query.days ?? null,
    limit: query.limit ?? null,
    breakdownLimit: query.breakdownLimit ?? null,
    trendBucketDays: query.trendBucketDays ?? null
  };
}

function normalizeOperationsKpiKey(query: OperationsKpiQuery): Record<string, unknown> {
  return {
    days: query.days ?? null,
    missedLimit: query.missedLimit ?? null
  };
}

function normalizeExperimentKey(query: ExperimentListQuery): Record<string, unknown> {
  return {
    limit: query.limit ?? null,
    status: query.status ?? null,
    outcome: query.outcome ?? null,
    applicationStatus: query.applicationStatus ?? null,
    sort: query.sort ?? null
  };
}

export type QueryKeyOf<TFn extends (...args: never[]) => readonly unknown[]> = ReturnType<TFn>;
