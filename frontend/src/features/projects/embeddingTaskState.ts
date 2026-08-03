import type { ProjectEmbeddingTaskStatusResponse } from "@/shared/types/project";

export const EMBEDDING_TASK_POLL_INTERVAL_MS = 1_500;

export function embeddingTaskPollInterval(
  status?: ProjectEmbeddingTaskStatusResponse["status"]
): number | false {
  return status === "completed" || status === "failed" || status === "cancelled"
    ? false
    : EMBEDDING_TASK_POLL_INTERVAL_MS;
}
