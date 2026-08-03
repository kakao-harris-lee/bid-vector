import type { SimilarProjectsRefreshStatus } from "@/shared/types/project";

export const SIMILAR_PROJECTS_REFRESH_POLL_INTERVAL_MS = 1_500;

export function similarProjectsRefreshPollInterval(
  status?: SimilarProjectsRefreshStatus
): number | false {
  return status === "succeeded" || status === "failed" || status === "cancelled"
    ? false
    : SIMILAR_PROJECTS_REFRESH_POLL_INTERVAL_MS;
}
