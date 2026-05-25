import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { NotificationItem } from "@/shared/types/notifications";

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

const notifications: NotificationItem[] = [
  {
    id: 11,
    title: "결정 알림",
    message: "공고 #5에 대한 결정 필요",
    type: "bid_decision",
    is_read: false,
    created_at: "2026-05-19T07:00:00Z"
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
  window.localStorage.setItem("bid-vector-dashboard-token", "token-realtime");
  window.history.pushState({}, "", "/dashboard");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
  // Block real WebSocket constructions in tests
  vi.stubGlobal(
    "WebSocket",
    class MockWebSocket {
      readyState = 0;
      addEventListener() {}
      close() {}
    } as unknown as typeof WebSocket
  );
});

describe("NotificationDrawer", () => {
  it("알림 아이콘 클릭 시 서랍이 열리고 읽지 않은 알림이 표시된다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.startsWith("/api/v1/operator/notifications")) return jsonResponse(notifications);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "알림" }));

    expect(await screen.findByRole("dialog", { name: "알림 서랍" })).toBeInTheDocument();
    expect(await screen.findByText("결정 알림")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "읽음 처리" })).toBeInTheDocument();
  });

  it("읽음 처리 버튼 클릭 시 PUT 호출이 발생한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.startsWith("/api/v1/operator/notifications") && (!init || init.method !== "PUT")) {
        return jsonResponse(notifications);
      }
      if (url === "/api/v1/operator/notifications/11/read" && init?.method === "PUT") {
        return jsonResponse({ ...notifications[0], is_read: true });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    fireEvent.click(await screen.findByRole("button", { name: "알림" }));
    await screen.findByText("결정 알림");
    fireEvent.click(screen.getByRole("button", { name: "읽음 처리" }));

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.find(([url, init]) => {
          return (
            String(url) === "/api/v1/operator/notifications/11/read" &&
            (init as RequestInit | undefined)?.method === "PUT"
          );
        })
      ).toBeDefined();
    });
  });
});
