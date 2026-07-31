import { describe, expect, it } from "vitest";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import {
  SNAPSHOT_IDLE_RECHECK_INTERVAL_MS,
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
  it("running 은 fast 주기로 폴링한다", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false)).toBe(
      SNAPSHOT_POLL_INTERVAL_MS
    );
  });

  it("부트스트랩(idle + computed_at=null)도 fast 주기 — 다음 GET 이 자동 디스패치한다", () => {
    expect(
      snapshotPollInterval(snapshot({ snapshot_status: "idle", computed_at: null }), false)
    ).toBe(SNAPSHOT_POLL_INTERVAL_MS);
  });

  it("응답 미도착(undefined)은 fast 주기로 첫 응답을 기다린다", () => {
    expect(snapshotPollInterval(undefined, false)).toBe(SNAPSHOT_POLL_INTERVAL_MS);
  });

  it("정착-idle 은 느슨한 recheck 주기 — 폴링을 멈추지 않고 서버 stale 자동 디스패치를 재무장한다", () => {
    // idle + computed_at != null 은 '지금 최신'이지만 백엔드 자동 디스패치는 GET 에서만
    // 발화하므로, 완전히 멈추지 않고 느슨히 되물어야 배지가 늙고 갱신이 재무장된다.
    expect(snapshotPollInterval(snapshot(), false)).toBe(SNAPSHOT_IDLE_RECHECK_INTERVAL_MS);
  });

  it("failed 는 false — 쿨다운 동안 자동 재디스패치가 없어 되물어도 소용없다(복구는 명시 새로고침)", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "failed" }), false)).toBe(false);
    expect(
      snapshotPollInterval(snapshot({ snapshot_status: "failed", computed_at: null }), false)
    ).toBe(false);
  });

  it("두 주기 모두 주입으로 덮을 수 있다(테스트 가속)", () => {
    expect(snapshotPollInterval(snapshot({ snapshot_status: "running" }), false, 20)).toBe(20);
    // fast=20, slow=500 을 주입하면 정착-idle 은 slow(500)를 쓴다.
    expect(snapshotPollInterval(snapshot(), false, 20, 500)).toBe(500);
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
