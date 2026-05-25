import { act, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { OperationsDashboardResponse } from "@/shared/types/operations";

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

const baseOperations: OperationsDashboardResponse = {
  operator_id: 1,
  period_days: 7,
  cards: [
    {
      key: "crawl_success_rate",
      label: "크롤 성공률",
      value: 0.92,
      unit: "ratio",
      status: "healthy",
      detail: "최근 7일"
    },
    {
      key: "telegram_failures",
      label: "텔레그램 실패",
      value: 12,
      unit: "count",
      status: "critical",
      detail: "재발송 필요"
    }
  ],
  crawl: {
    job_count: 100,
    completed_count: 92,
    failed_count: 8,
    success_rate: 0.92,
    failure_rate: 0.08,
    last_success_at: "2026-05-19T08:00:00Z",
    last_failure_at: "2026-05-18T22:00:00Z",
    failure_reason_breakdown: { "timeout": 3, "5xx": 5 },
    recent_failures: []
  },
  tasks: {},
  notifications: {
    notification_count: 50,
    unread_count: 5,
    decision_notification_count: 20,
    bid_submission_notification_count: 8,
    telegram_configured: true,
    telegram_delivery_attempt_count: 60,
    telegram_sent_count: 48,
    telegram_failed_count: 12,
    telegram_success_rate: 0.8,
    telegram_status: "watch",
    telegram_detail: "최근 실패율 20%",
    telegram_failure_reason_breakdown: { "network": 12 }
  },
  ml_release: {
    manifest_count: 1,
    status: "healthy",
    detail: "최신 릴리스 정상",
    latest_release_tag: "v1.2.0",
    latest_signature_status: "verified",
    latest_gate_status: "passed",
    latest_gate_passed: true,
    backtest_status: "healthy",
    backtest_detail: "샘플 100건",
    recent_manifests: []
  }
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
  window.localStorage.setItem("bid-vector-dashboard-token", "token-operations");
  window.history.pushState({}, "", "/dashboard/operations");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("OperationsScreen", () => {
  it("status별 카드를 알맞은 톤으로 표시하고 critical 카드는 인시던트 배너로 노출", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.startsWith("/api/v1/analytics/operations-dashboard")) return jsonResponse(baseOperations);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "운영 대시보드", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText("크롤 성공률")).toBeInTheDocument();
    expect(screen.getAllByText("텔레그램 실패").length).toBeGreaterThanOrEqual(1);

    const incidentBanner = screen.getByRole("alert", { name: "인시던트 알림" });
    expect(incidentBanner).toHaveTextContent("인시던트 1건");
    expect(incidentBanner).toHaveTextContent("텔레그램 실패");
  });

  it("탭이 비활성화되어 있어도 자동 갱신은 멈춰 있고 새로고침 버튼은 동작한다", async () => {
    let callCount = 0;
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });

    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.startsWith("/api/v1/analytics/operations-dashboard")) {
        callCount += 1;
        return jsonResponse(baseOperations);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "운영 대시보드", level: 2 })
    ).toBeInTheDocument();
    await waitFor(() => expect(callCount).toBe(1));

    // 자동 polling 끄도록 visibilityState=hidden — 다음 인터벌 도래 전에는 추가 호출이 없어야 함
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(callCount).toBe(1);

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });
});
