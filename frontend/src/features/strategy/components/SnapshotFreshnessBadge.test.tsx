import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { OperatorStrategyCandidatesResponse } from "@/shared/types/strategy";
import { SnapshotFreshnessBadge } from "./SnapshotFreshnessBadge";

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

afterEach(() => {
  vi.useRealTimers();
});

describe("SnapshotFreshnessBadge 자가 갱신(Finding 1b)", () => {
  it("폴링이 멈춰도 시간이 지나면 상대 라벨이 tick 만으로 스스로 늙는다", () => {
    vi.useFakeTimers({ now: new Date("2026-07-30T02:00:00Z") });
    render(
      <SnapshotFreshnessBadge
        snapshot={snapshot({ computed_at: "2026-07-30T02:00:00Z" })}
        tickMs={1_000}
      />
    );
    // 계산 직후 = 방금.
    expect(screen.getByText("방금 기준")).toBeInTheDocument();

    // 재조회(폴링) 없이 tick 만으로 라벨이 갱신된다 — 정지 상태의 거짓 최신 방지.
    act(() => {
      vi.advanceTimersByTime(3 * 60_000);
    });
    expect(screen.getByText("3분 전 기준")).toBeInTheDocument();
    expect(screen.queryByText("방금 기준")).toBeNull();
  });

  it("24시간을 넘기면 KST 절대 시각으로 폴백한다('기준' 접미는 유지)", () => {
    vi.useFakeTimers({ now: new Date("2026-07-30T02:00:00Z") });
    render(
      <SnapshotFreshnessBadge
        snapshot={snapshot({ computed_at: "2026-07-30T02:00:00Z" })}
        tickMs={1_000}
      />
    );
    act(() => {
      vi.advanceTimersByTime(25 * 60 * 60_000);
    });
    expect(screen.getByText(/기준$/)).toBeInTheDocument();
    expect(screen.queryByText("방금 기준")).toBeNull();
    expect(screen.queryByText(/분 전 기준$/)).toBeNull();
  });

  it("stale=true 는 '· 갱신 필요' 를 덧붙인다", () => {
    vi.useFakeTimers({ now: new Date("2026-07-30T02:40:00Z") });
    render(
      <SnapshotFreshnessBadge
        snapshot={snapshot({ stale: true, computed_at: "2026-07-30T02:00:00Z" })}
      />
    );
    expect(screen.getByText("40분 전 기준 · 갱신 필요")).toBeInTheDocument();
  });

  it("computed_at=null 은 '첫 계산 대기' — 상대 시각을 그리지 않는다", () => {
    render(<SnapshotFreshnessBadge snapshot={snapshot({ computed_at: null })} />);
    expect(screen.getByText("첫 계산 대기")).toBeInTheDocument();
    expect(screen.queryByText(/기준$/)).toBeNull();
  });

  it("snapshot 미도착이면 아무것도 그리지 않는다", () => {
    const { container } = render(<SnapshotFreshnessBadge />);
    expect(container).toBeEmptyDOMElement();
  });
});
