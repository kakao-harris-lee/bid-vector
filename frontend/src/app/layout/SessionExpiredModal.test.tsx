import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";

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

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "stale-token");
  window.localStorage.setItem("bid-vector-dashboard-username", "operator");
  window.history.pushState({}, "", "/dashboard");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
  vi.stubGlobal(
    "WebSocket",
    class MockWebSocket {
      readyState = 0;
      addEventListener() {}
      close() {}
    } as unknown as typeof WebSocket
  );
});

describe("SessionExpiredModal", () => {
  it("401 응답 시 자동으로 세션 만료 모달이 노출되고 재로그인이 가능하다", async () => {
    let attempt = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) {
        attempt += 1;
        if (attempt === 1) {
          // First call: 401 → triggers modal
          return jsonResponse({}, 401);
        }
        return jsonResponse(emptySummary);
      }
      if (url.endsWith("/api/v1/auth/session") && init?.method === "POST") {
        return jsonResponse({
          access_token: "fresh-token",
          token_type: "bearer",
          username: "operator"
        });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("dialog", { name: "세션이 만료되었습니다" })
    ).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("비밀번호"), { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "다시 로그인" }));

    await waitFor(() => {
      expect(window.localStorage.getItem("bid-vector-dashboard-token")).toBe("fresh-token");
    });
    expect(await screen.findByText("다시 로그인 완료")).toBeInTheDocument();
  });
});
