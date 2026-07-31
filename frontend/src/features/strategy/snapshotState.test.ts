import { describe, expect, it } from "vitest";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import {
  SNAPSHOT_POLL_INTERVAL_MS,
  hasComputedSnapshot,
  isSnapshotSettled,
  snapshotPollInterval
} from "./snapshotState";

function snapshot(
  overrides: Partial<OperatorStrategyCandidatesResponse> = {}
): OperatorStrategyCandidatesResponse {
  return {
    operator_id: 1,
    evaluated_project_count: 250,
    returned_candidate_count: 0,
    high_priority_only: false,
    candidates: [],
    computed_at: "2026-07-30T02:00:00Z",
    snapshot_status: "idle",
    stale: false,
    ...overrides
  };
}

describe("isSnapshotSettled", () => {
  it("running 은 미정착 — 폴링을 계속한다", () => {
    expect(isSnapshotSettled(snapshot({ snapshot_status: "running" }))).toBe(false);
  });

  it("idle + computed_at 은 정착", () => {
    expect(isSnapshotSettled(snapshot())).toBe(true);
  });

  it("stale=true 여도 idle 이면 정착 — stale 이 재계산 큐를 보장하지 않는다(주의 1)", () => {
    expect(isSnapshotSettled(snapshot({ stale: true }))).toBe(true);
  });

  it("idle + computed_at=null 은 미정착 — stale=false 라도 '계산된 적 없음'이다(주의 3)", () => {
    expect(isSnapshotSettled(snapshot({ computed_at: null }))).toBe(false);
  });

  it("failed 는 정착 — 쿨다운 동안 재조회해도 바뀌지 않는다(주의 2)", () => {
    expect(isSnapshotSettled(snapshot({ snapshot_status: "failed" }))).toBe(true);
    expect(isSnapshotSettled(snapshot({ snapshot_status: "failed", computed_at: null }))).toBe(true);
  });

  it("응답 미도착(undefined)은 미정착", () => {
    expect(isSnapshotSettled(undefined)).toBe(false);
  });
});

describe("snapshotPollInterval", () => {
  it("미정착이면 주기를 돌려준다", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false)).toBe(
      SNAPSHOT_POLL_INTERVAL_MS
    );
  });

  it("주기는 주입으로 덮을 수 있다(테스트 가속)", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false, 20)).toBe(20);
  });

  it("정착이면 false", () => {
    expect(snapshotPollInterval(snapshot(), false)).toBe(false);
  });

  it("마지막 fetch 가 실패했으면 false — 죽은 백엔드에 영구 재시도하지 않는다", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), true)).toBe(false);
    expect(snapshotPollInterval(undefined, true)).toBe(false);
  });
});

describe("hasComputedSnapshot", () => {
  it("computed_at 이 있으면 통계·목록을 그릴 수 있다", () => {
    expect(hasComputedSnapshot(snapshot())).toBe(true);
    expect(hasComputedSnapshot(snapshot({ snapshot_status: "failed" }))).toBe(true);
  });

  it("computed_at 이 없으면 0건을 사실처럼 그리지 않는다", () => {
    expect(hasComputedSnapshot(snapshot({ computed_at: null }))).toBe(false);
    expect(hasComputedSnapshot(undefined)).toBe(false);
  });
});
