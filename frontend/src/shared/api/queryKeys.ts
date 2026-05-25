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
  }
} as const;

export type QueryKeyOf<TFn extends (...args: never[]) => readonly unknown[]> = ReturnType<TFn>;
