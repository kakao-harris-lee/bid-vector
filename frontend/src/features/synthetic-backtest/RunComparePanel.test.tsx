import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { RunComparePanel } from "./RunComparePanel";
import type {
  SyntheticExperimentCompareResponse,
  SyntheticExperimentResponse
} from "@/shared/types/synthetic";

const experiments: SyntheticExperimentResponse[] = [
  {
    id: 2,
    name: "공격 vs 보수",
    description: null,
    params: { limit: 100, scenario: "base", settle_actions: false },
    operator_slugs: [],
    created_at: "2026-05-30T00:00:00Z",
    runs: [
      { id: 11, experiment_id: 2, status: "completed", summary: { scenario: "base", limit: 100 } },
      { id: 10, experiment_id: 2, status: "completed", summary: { scenario: "base", limit: 50 } },
      // 미완료 런은 옵션에서 제외되어야 한다.
      { id: 12, experiment_id: 2, status: "running", summary: null }
    ]
  }
];

const compareResponse: SyntheticExperimentCompareResponse = {
  run_a: { id: 10, experiment_id: 2, summary: { scenario: "base", limit: 50 } },
  run_b: { id: 11, experiment_id: 2, summary: { scenario: "base", limit: 100 } },
  operators: [
    {
      operator_slug: "aggressive",
      a: {
        win_rate_on_settled: 0.4,
        settled_count: 10,
        bid_submission_rate: 0.8,
        average_absolute_bid_rate_error: 0.05
      },
      b: {
        win_rate_on_settled: 0.6,
        settled_count: 14,
        bid_submission_rate: 0.7,
        average_absolute_bid_rate_error: 0.03
      },
      delta: {
        win_rate_on_settled: 0.2,
        bid_submission_rate: -0.1,
        average_absolute_bid_rate_error: -0.02
      }
    },
    {
      operator_slug: "conservative",
      a: {
        win_rate_on_settled: null,
        settled_count: 0,
        bid_submission_rate: 0.5,
        average_absolute_bid_rate_error: null
      },
      b: {
        win_rate_on_settled: 0.3,
        settled_count: 5,
        bid_submission_rate: 0.55,
        average_absolute_bid_rate_error: 0.04
      },
      // win_rate delta는 한쪽 null → null (비교불가).
      delta: {
        win_rate_on_settled: null,
        bid_submission_rate: 0.05,
        average_absolute_bid_rate_error: null
      }
    }
  ],
  only_in_a: ["legacy-co"],
  only_in_b: ["new-co"]
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/experiments/compare")) {
        return jsonResponse(compareResponse);
      }
      return jsonResponse(experiments);
    })
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

/** 완료 런 옵션(3개: placeholder + 11 + 10)이 채워질 때까지 기다린다. */
async function waitForOptions(): Promise<void> {
  const selectA = (await screen.findByLabelText("런 A 선택")) as HTMLSelectElement;
  await waitFor(() =>
    expect(within(selectA).getAllByRole("option")).toHaveLength(3)
  );
}

describe("RunComparePanel", () => {
  it("lists only completed runs as selectable options", async () => {
    stubFetch();
    renderWithProviders(<RunComparePanel token="token-x" />);

    const selectA = (await screen.findByLabelText("런 A 선택")) as HTMLSelectElement;
    // 실험 데이터가 도착해 완료 런 옵션이 채워질 때까지 기다린다.
    await waitFor(() =>
      expect(within(selectA).getAllByRole("option")).toHaveLength(3)
    );
    const optionValues = within(selectA)
      .getAllByRole("option")
      .map((option) => (option as HTMLOptionElement).value);

    // "선택…" placeholder + 완료 런 2개(11, 10). running(12)은 제외.
    expect(optionValues).toEqual(["", "11", "10"]);
  });

  it("renders A/B/Δ rows, 비교불가 for null delta, and only-in lists", async () => {
    stubFetch();
    renderWithProviders(<RunComparePanel token="token-x" />);

    await waitForOptions();
    fireEvent.change(screen.getByLabelText("런 A 선택"), {
      target: { value: "10" }
    });
    fireEvent.change(screen.getByLabelText("런 B 선택"), {
      target: { value: "11" }
    });

    // compare 결과의 회사 행이 렌더된다.
    const aggressiveRow = await screen.findByLabelText("aggressive 비교 행");
    // A win_rate 40.0%, B 60.0%, Δ +20.0%p
    expect(within(aggressiveRow).getByText("40.0%")).toBeInTheDocument();
    expect(within(aggressiveRow).getByText("60.0%")).toBeInTheDocument();
    expect(within(aggressiveRow).getByText("+20.0%p")).toBeInTheDocument();

    // conservative: win_rate delta가 null → "비교불가" 뱃지.
    const conservativeRow = screen.getByLabelText("conservative 비교 행");
    expect(within(conservativeRow).getAllByText("비교불가").length).toBeGreaterThanOrEqual(1);

    // only_in 목록.
    expect(screen.getByText("legacy-co")).toBeInTheDocument();
    expect(screen.getByText("new-co")).toBeInTheDocument();

    // 가격 기준 추정 caveat 유지.
    expect(screen.getByText(/가격 기준 추정/)).toBeInTheDocument();
  });

  it("exposes a CSV download link for the selected run", async () => {
    stubFetch();
    renderWithProviders(<RunComparePanel token="token-x" />);

    await waitForOptions();
    fireEvent.change(screen.getByLabelText("런 A 선택"), {
      target: { value: "11" }
    });

    const csvLink = screen.getByRole("link", { name: "A 런 CSV 다운로드" });
    expect(csvLink).toHaveAttribute(
      "href",
      "/api/v1/synthetic/experiments/2/runs/11/export.csv"
    );
    expect(csvLink).toHaveAttribute("download");
  });

  it("does not query compare until both runs are selected", async () => {
    stubFetch();
    renderWithProviders(<RunComparePanel token="token-x" />);

    await waitForOptions();
    // 한쪽만 선택했을 때는 비교 행이 없다.
    fireEvent.change(screen.getByLabelText("런 A 선택"), { target: { value: "10" } });
    await waitFor(() =>
      expect(screen.queryByLabelText("aggressive 비교 행")).toBeNull()
    );
  });
});
