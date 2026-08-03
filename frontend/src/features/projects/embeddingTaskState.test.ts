import { describe, expect, it } from "vitest";
import {
  EMBEDDING_TASK_POLL_INTERVAL_MS,
  embeddingTaskPollInterval
} from "./embeddingTaskState";

describe("embeddingTaskPollInterval", () => {
  it.each([undefined, "queued", "running"] as const)(
    "%s 상태는 완료될 때까지 폴링한다",
    (status) => {
      expect(embeddingTaskPollInterval(status)).toBe(EMBEDDING_TASK_POLL_INTERVAL_MS);
    }
  );

  it.each(["completed", "failed", "cancelled"] as const)(
    "%s 상태에서 폴링을 중단한다",
    (status) => {
      expect(embeddingTaskPollInterval(status)).toBe(false);
    }
  );
});
