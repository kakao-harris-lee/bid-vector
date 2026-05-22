import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { DashboardSummaryResponse } from "./types";

const emptySummary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-05-19T00:00:00Z",
  today: "2026-05-19",
  operational_status: {
    key: "operator_strategy",
    label: "운영 상태",
    value: "completed",
    unit: "state",
    status: "healthy",
    detail: "정상"
  },
  metrics: [
    {
      key: "due_opportunities",
      label: "오늘 마감",
      value: 0,
      unit: "count",
      status: "healthy",
      detail: "없음"
    }
  ],
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

function jsonResponse(payload: unknown, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.pushState({}, "", "/dashboard");
  vi.restoreAllMocks();
});

describe("App", () => {
  it("logs in and renders the dashboard shell", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/session")) {
        return jsonResponse({ access_token: "token-123", token_type: "bearer", operator_id: 1, username: "operator" });
      }
      if (url.endsWith("/api/v1/dashboard/summary")) {
        return jsonResponse(emptySummary);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await userEvent.type(screen.getByLabelText("아이디"), "operator");
    await userEvent.type(screen.getByLabelText("비밀번호"), "password123");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "대시보드 탭" })).toBeInTheDocument();
    expect(window.localStorage.getItem("bid-vector-dashboard-token")).toBe("token-123");
  });

  it("shows a login failure message", async () => {
    vi.stubGlobal("fetch", vi.fn(() => jsonResponse({ detail: "Invalid credentials" }, 401)));

    render(<App />);
    await userEvent.type(screen.getByLabelText("아이디"), "operator");
    await userEvent.type(screen.getByLabelText("비밀번호"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "로그인" }));

    expect(await screen.findByText("로그인에 실패했습니다.")).toBeInTheDocument();
  });

  it("resets a forgotten password and opens the dashboard", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/auth/password-reset")) {
        return jsonResponse({ access_token: "reset-token", token_type: "bearer", operator_id: 1, username: "operator" });
      }
      if (url.endsWith("/api/v1/dashboard/summary")) {
        return jsonResponse(emptySummary);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "비밀번호 초기화" }));
    await userEvent.type(screen.getByLabelText("아이디"), "operator");
    await userEvent.type(screen.getByLabelText("초기화 토큰"), "server-reset-token");
    await userEvent.type(screen.getByLabelText("새 비밀번호"), "new-password-123");
    await userEvent.type(screen.getByLabelText("새 비밀번호 확인"), "new-password-123");
    await userEvent.click(screen.getByRole("button", { name: "비밀번호 초기화" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/password-reset",
        expect.objectContaining({
          body: JSON.stringify({
            username: "operator",
            reset_token: "server-reset-token",
            new_password: "new-password-123"
          })
        })
      )
    );
    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(window.localStorage.getItem("bid-vector-dashboard-token")).toBe("reset-token");
  });

  it("renders bottom tabs and loads the bid list route", async () => {
    window.localStorage.setItem("bid-vector-dashboard-token", "token-123");
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) {
        return jsonResponse(emptySummary);
      }
      if (url.endsWith("/api/v1/dashboard/bids")) {
        return jsonResponse({ operator_id: 1, generated_at: emptySummary.generated_at, returned_count: 0, limit: 50, items: [] });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "투찰" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/dashboard/bids", expect.anything()));
    expect(screen.getByRole("button", { name: "입찰" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "결과" })).toBeInTheDocument();
  });

  it("shows paper bidding settlement timing on the results route", async () => {
    window.localStorage.setItem("bid-vector-dashboard-token", "token-123");
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) {
        return jsonResponse(emptySummary);
      }
      if (url.endsWith("/api/v1/dashboard/results")) {
        return jsonResponse({ operator_id: 1, generated_at: emptySummary.generated_at, returned_count: 0, limit: 50, items: [] });
      }
      if (url.endsWith("/api/v1/backtests/paper-bidding/summary")) {
        return jsonResponse({
          operator_id: 1,
          run_count: 1,
          completed_count: 1,
          latest_summary: {},
          latest_run: {
            id: 7,
            operator_id: 1,
            status: "completed",
            mode: "forward_paper",
            scenario: "base",
            category_filter: null,
            strategy_version: "forward-paper",
            model_version: "current",
            target_start_at: null,
            target_end_at: null,
            data_cutoff_policy: "deadline_minus_0h",
            started_at: "2026-05-21T08:05:15Z",
            completed_at: "2026-05-21T08:05:16Z",
            candidate_count: 50,
            paper_bid_count: 50,
            settled_count: 0,
            settlement_overview: {
              status: "before_deadline",
              label: "마감 전",
              detail: "50건은 아직 마감 전입니다. 마감 후 결과가 수집되면 승패가 확정됩니다.",
              settlement_basis: "TenderResult.winning_amount > 0 matched by project_id",
              paper_bid_count: 50,
              settled_count: 0,
              unsettled_count: 50,
              ready_to_settle_count: 0,
              waiting_result_count: 0,
              before_deadline_count: 50,
              missing_deadline_count: 0,
              next_confirmable_at: "2026-05-23T03:00:00Z",
              next_deadline_at: "2026-05-23T03:00:00Z",
              oldest_waiting_deadline_at: null,
              latest_settled_at: null
            },
            summary: {
              average_absolute_bid_rate_error: null,
              within_0_3pct_count: 0
            }
          }
        });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "결과" }));

    expect(await screen.findByText("50건 검증")).toBeInTheDocument();
    expect(screen.getByText("0/50")).toBeInTheDocument();
    expect(screen.getAllByText("정산 없음").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("승패 확정")).toBeInTheDocument();
    expect(screen.getAllByText("마감 전").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/다음 마감/)).toBeInTheDocument();
  });
});
