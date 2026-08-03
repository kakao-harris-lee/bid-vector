import { describe, expect, it } from "vitest";
import {
  SIMILAR_PROJECTS_REFRESH_POLL_INTERVAL_MS,
  similarProjectsRefreshPollInterval
} from "./similarProjectsRefreshState";

describe("similarProjectsRefreshPollInterval", () => {
  it.each([undefined, "accepted", "in_progress"] as const)(
    "%s 상태는 완료될 때까지 폴링한다",
    (status) => {
      expect(similarProjectsRefreshPollInterval(status)).toBe(
        SIMILAR_PROJECTS_REFRESH_POLL_INTERVAL_MS
      );
    }
  );

  it.each(["succeeded", "failed", "cancelled"] as const)(
    "%s 상태에서 폴링을 중단한다",
    (status) => {
      expect(similarProjectsRefreshPollInterval(status)).toBe(false);
    }
  );
});
