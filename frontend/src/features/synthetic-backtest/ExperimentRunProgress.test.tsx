import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { ExperimentRunProgress } from "./ExperimentRunProgress";

const queuedRun = {
  id: 9,
  experiment_id: 2,
  status: "queued",
  summary: null,
  results: []
};

const completedRun = {
  id: 9,
  experiment_id: 2,
  status: "completed",
  summary: { operator_count: 1 },
  results: [
    {
      operator_slug: "aggressive",
      metrics: {
        win_rate_on_settled: 0.5,
        settled_count: 12,
        bid_submission_rate: 0.8
      }
    }
  ]
};

const failedRun = {
  id: 9,
  experiment_id: 2,
  status: "failed",
  error: "백테스트 엔진 오류",
  results: []
};

function jsonResponse(payload: unknown): Promise<Response> {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("ExperimentRunProgress", () => {
  it("polls while queued then renders results on completion", async () => {
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        call += 1;
        return jsonResponse(call === 1 ? queuedRun : completedRun);
      })
    );

    renderWithProviders(
      <ExperimentRunProgress experimentId={2} runId={9} token="token-x" pollIntervalMs={20} />
    );

    // queued: 진행 표시
    expect(
      await screen.findByText(/실험을 실행하고 있습니다|준비하고 있습니다/)
    ).toBeInTheDocument();

    // completed: 결과 표 + caveat
    expect(await screen.findByText("aggressive")).toBeInTheDocument();
    expect(screen.getByText(/가격 기준 추정 낙찰/)).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders an error alert when the run fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => jsonResponse(failedRun))
    );

    renderWithProviders(
      <ExperimentRunProgress experimentId={2} runId={9} token="token-x" pollIntervalMs={20} />
    );

    expect(await screen.findByText("실행 실패")).toBeInTheDocument();
    expect(screen.getByText("백테스트 엔진 오류")).toBeInTheDocument();
  });
});
