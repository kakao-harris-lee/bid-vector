import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  SyntheticBacktestRunResponse,
  SyntheticOperatorListResponse,
  SyntheticSeedResponse
} from "@/shared/types/synthetic";

const emptySummary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-05-19T00:00:00Z",
  today: "2026-05-19",
  operational_status: {
    key: "x",
    label: "운영 상태",
    value: "completed",
    unit: "state",
    status: "healthy",
    detail: ""
  },
  metrics: [],
  work_items: [],
  sections: [
    { key: "opportunities", label: "입찰", count: 0, status: "healthy", href: "/dashboard/opportunities" },
    { key: "bids", label: "투찰", count: 0, status: "healthy", href: "/dashboard/bids" },
    { key: "results", label: "결과", count: 0, status: "healthy", href: "/dashboard/results" }
  ],
  recent_opportunities: [],
  recent_bids: [],
  recent_results: [],
  realtime_href: "/api/v1/realtime/events"
};

const privilegedAccounts = {
  current_operator_id: 1,
  current_operator_username: "operator",
  is_privileged: true,
  operator_count: 1,
  operators: [
    {
      operator_id: 1,
      username: "operator",
      full_name: "본사 운영자",
      company: "본사",
      business_type: null,
      is_canonical: true,
      is_synthetic: false,
      is_active: true,
      profile_configured: true
    }
  ]
};

const seedResponse: SyntheticSeedResponse = {
  seeded_count: 2,
  purged_count: 0,
  operators: [
    {
      user_id: 1,
      username: "synthetic-aggressive",
      slug: "aggressive",
      display_name: "공격적 운영자",
      company: "A Corp",
      business_type: "software",
      annual_revenue: 1_000_000_000,
      capacity_score: 0.9,
      bid_now_threshold: 0.6,
      review_threshold: 0.4,
      is_custom: false
    },
    {
      user_id: 2,
      username: "synthetic-conservative",
      slug: "conservative",
      display_name: "보수적 운영자",
      company: "B Corp",
      business_type: "software",
      annual_revenue: 500_000_000,
      capacity_score: 0.6,
      bid_now_threshold: 0.85,
      review_threshold: 0.6,
      is_custom: false
    }
  ]
};

const listResponse: SyntheticOperatorListResponse = {
  operator_count: seedResponse.operators.length,
  operators: seedResponse.operators
};

const runResponse: SyntheticBacktestRunResponse = {
  operator_count: 2,
  category: null,
  start_at: null,
  end_at: null,
  limit: 100,
  scenario: "base",
  results: [
    {
      ...seedResponse.operators[0],
      candidate_count: 20,
      paper_bid_count: 18,
      settled_count: 15,
      would_have_won_count: 9,
      win_rate_on_settled: 0.6,
      bid_submission_rate: 0.9,
      average_absolute_bid_rate_error: 0.04,
      settlement_sample_count: 0,
      settlement_items: []
    },
    {
      ...seedResponse.operators[1],
      candidate_count: 10,
      paper_bid_count: 6,
      settled_count: 5,
      would_have_won_count: 4,
      win_rate_on_settled: 0.8,
      bid_submission_rate: 0.6,
      average_absolute_bid_rate_error: 0.02,
      settlement_sample_count: 0,
      settlement_items: []
    }
  ]
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-synthetic");
  window.history.pushState({}, "", "/admin/synthetic-backtest");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("SyntheticBacktestScreen", () => {
  it("시드 → 실행 → 비교표 렌더링까지 화면 이탈 없이 동작", async () => {
    let listState = { operator_count: 0, operators: [] as typeof listResponse.operators };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url === "/api/v1/synthetic/operators") return jsonResponse(listState);
      if (url === "/api/v1/synthetic/operators/seed" && init?.method === "POST") {
        listState = listResponse;
        return jsonResponse(seedResponse);
      }
      if (url === "/api/v1/synthetic/backtests/run" && init?.method === "POST") {
        return jsonResponse(runResponse);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    // Experiment Lab wraps the backtest screen under the "비교 / 시드" tab.
    fireEvent.click(await screen.findByRole("tab", { name: "비교 / 시드" }));

    expect(
      await screen.findByRole("heading", { name: "가상 운영자 백테스트", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText(/아직 시드되지 않았습니다/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "시드" }));
    expect(await screen.findByText("공격적 운영자")).toBeInTheDocument();
    expect(await screen.findByText("시드 완료")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "실행" }));
    expect(await screen.findByText(/비교 결과/)).toBeInTheDocument();
  });

  it("정렬 변경 시 첫 번째 행이 바뀐다 (win_rate vs bid_submission_rate)", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url === "/api/v1/synthetic/operators") return jsonResponse(listResponse);
      if (url === "/api/v1/synthetic/backtests/run" && init?.method === "POST") {
        return jsonResponse(runResponse);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    // Experiment Lab wraps the backtest screen under the "비교 / 시드" tab.
    fireEvent.click(await screen.findByRole("tab", { name: "비교 / 시드" }));

    expect(
      await screen.findByRole("heading", { name: "가상 운영자 백테스트", level: 2 })
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "실행" }));
    await screen.findByText(/비교 결과/);

    // Default sort: win_rate_on_settled desc → conservative (0.8) is first
    const rowsDefault = screen.getAllByRole("row");
    expect(rowsDefault[1]).toHaveTextContent("보수적 운영자");

    // Change sort to bid_submission_rate → aggressive (0.9) becomes first
    fireEvent.change(screen.getByLabelText("정렬 기준"), {
      target: { value: "bid_submission_rate" }
    });

    await waitFor(() => {
      const rowsResorted = screen.getAllByRole("row");
      expect(rowsResorted[1]).toHaveTextContent("공격적 운영자");
    });
  });
});
