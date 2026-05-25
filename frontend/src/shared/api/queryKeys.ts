export const queryKeys = {
  dashboard: {
    summary: () => ["dashboard", "summary"] as const,
    opportunities: () => ["dashboard", "opportunities"] as const,
    bids: () => ["dashboard", "bids"] as const,
    results: () => ["dashboard", "results"] as const,
    paperSummary: () => ["dashboard", "paper-summary"] as const
  }
} as const;

export type QueryKeyOf<TFn extends (...args: never[]) => readonly unknown[]> = ReturnType<TFn>;
