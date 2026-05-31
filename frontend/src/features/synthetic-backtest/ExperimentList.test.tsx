import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { ExperimentList } from "./ExperimentList";
import type { SyntheticExperimentResponse } from "@/shared/types/synthetic";

const experiments: SyntheticExperimentResponse[] = [
  {
    id: 2,
    name: "최근 실험",
    description: null,
    params: { limit: 100, scenario: "base", settle_actions: false },
    operator_slugs: [],
    created_at: "2026-05-30T00:00:00Z",
    runs: [
      {
        id: 9,
        experiment_id: 2,
        status: "completed",
        summary: { average_win_rate_on_settled: 0.42 }
      }
    ]
  },
  {
    id: 1,
    name: "이전 실험",
    description: null,
    params: { limit: 50, scenario: "base", settle_actions: false },
    operator_slugs: [],
    created_at: "2026-05-29T00:00:00Z",
    runs: []
  }
];

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal(
    "fetch",
    vi.fn(() => jsonResponse(experiments))
  );
});

describe("ExperimentList", () => {
  it("renders experiment items with status and win-rate badges", async () => {
    renderWithProviders(<ExperimentList token="token-x" />);

    expect(await screen.findByText("최근 실험")).toBeInTheDocument();
    expect(screen.getByText("이전 실험")).toBeInTheDocument();
    expect(screen.getByText("완료")).toBeInTheDocument();
    expect(screen.getByText("미실행")).toBeInTheDocument();
    expect(screen.getByText(/추정 승률/)).toBeInTheDocument();
  });

  it("invokes onSelect when an item is clicked", async () => {
    const onSelect = vi.fn();
    renderWithProviders(<ExperimentList token="token-x" onSelect={onSelect} />);

    fireEvent.click(await screen.findByText("최근 실험"));
    await waitFor(() => expect(onSelect).toHaveBeenCalledWith(experiments[0]));
  });
});
