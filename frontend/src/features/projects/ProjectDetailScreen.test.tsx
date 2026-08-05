import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  BidDecisionTimelineResponse,
  ProjectDetailResponse,
  ProjectSimilaritySearchResponse
} from "@/shared/types/project";

const emptySummary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-06-04T00:00:00Z",
  today: "2026-06-04",
  operational_status: {
    key: "operator_strategy",
    label: "운영 상태",
    value: "completed",
    unit: "state",
    status: "healthy",
    detail: "정상"
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

const projectA: ProjectDetailResponse = {
  id: 101,
  title: "공항 시스템 구축",
  description: "공항 IT",
  requirements: "",
  budget_estimate: 500_000_000,
  category: "software",
  notice_number: "A-001",
  source_url: null,
  issuing_agency: "조달청",
  demand_agency: "한국공항공사",
  status: "open",
  created_at: "2026-05-01T00:00:00Z",
  bid_base_amount: 550_000_000,
  bid_base_source: "clean-base",
  bid_base_to_estimate_ratio: 1.1
};

const projectBareTimeline: BidDecisionTimelineResponse = {
  operator_id: 1,
  project: {
    id: 101,
    title: "공항 시스템 구축",
    category: "software",
    status: "open",
    budget_estimate: 500_000_000,
    notice_number: "A-001",
    issuing_agency: "조달청",
    demand_agency: "한국공항공사"
  },
  result_count: 0,
  limit_applied: 10,
  latest_decision_record_id: null,
  timeline: []
};

const emptySimilar: ProjectSimilaritySearchResponse = {
  target_project_id: projectA.id,
  target_project_title: projectA.title,
  same_category_only: true,
  min_similarity: 0.15,
  result_count: 0,
  results: []
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
  window.localStorage.setItem("bid-vector-dashboard-token", "token-detail");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("ProjectDetailScreen — project_view telemetry", () => {
  it("마운트 시 project_view 이벤트를 1회 송신한다", async () => {
    window.history.pushState({}, "", "/dashboard/projects/101");

    const eventBodies: unknown[] = [];
    const eventAuthHeaders: (string | undefined)[] = [];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/projects/101") return jsonResponse(projectA);
      if (url.startsWith("/api/v1/projects/101/decision-timeline"))
        return jsonResponse(projectBareTimeline);
      if (url.startsWith("/api/v1/projects/101/similar")) return jsonResponse(emptySimilar);
      if (url === "/api/v1/analytics/event" && init?.method === "POST") {
        eventBodies.push(JSON.parse(String(init.body ?? "{}")));
        eventAuthHeaders.push((init.headers as Record<string, string>)?.Authorization);
        return jsonResponse({}, 200);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByText("공항 시스템 구축")).toBeInTheDocument();

    await waitFor(() => expect(eventBodies.length).toBeGreaterThanOrEqual(1));

    const projectViews = eventBodies.filter(
      (body): body is { event_type: string; event_data: { project_id: number } } =>
        typeof body === "object" &&
        body !== null &&
        (body as { event_type?: string }).event_type === "project_view"
    );
    expect(projectViews.length).toBeGreaterThanOrEqual(1);
    expect(projectViews[0]?.event_data.project_id).toBe(101);
    // 서버가 이 엔드포인트에서 bearer 를 요구한다 — 헤더가 빠지면 401 로 조용히 유실된다.
    expect(eventAuthHeaders[0]).toBe("Bearer token-detail");
  });

  it("유사 공고 갱신 완료까지 폴링한 뒤 결과를 다시 조회한다", async () => {
    window.history.pushState({}, "", "/dashboard/projects/101");
    let similarCalls = 0;
    let statusCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/projects/101") return jsonResponse(projectA);
      if (url.startsWith("/api/v1/projects/101/decision-timeline"))
        return jsonResponse(projectBareTimeline);
      if (
        url === "/api/v1/projects/101/similar/refresh?force=true" &&
        init?.method === "POST"
      ) {
        return jsonResponse(
          {
            project_id: 101,
            operation_id: "similar-refresh-1",
            operation: "refresh_similar_projects",
            status: "accepted",
            message: "유사 공고 갱신을 요청했습니다.",
            poll_url:
              "/api/v1/projects/101/similar/refresh/operations/similar-refresh-1"
          },
          202
        );
      }
      if (url.endsWith("/similar/refresh/operations/similar-refresh-1")) {
        statusCalls += 1;
        return jsonResponse({
          project_id: 101,
          operation_id: "similar-refresh-1",
          operation: "refresh_similar_projects",
          status: "succeeded",
          is_terminal: true,
          succeeded: true,
          message: "유사 공고 갱신이 완료되었습니다.",
          error: null
        });
      }
      if (url.startsWith("/api/v1/projects/101/similar")) {
        similarCalls += 1;
        return jsonResponse(emptySimilar);
      }
      if (url === "/api/v1/analytics/event" && init?.method === "POST") {
        return jsonResponse({}, 200);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "유사 공고 갱신" }));

    await waitFor(() => expect(statusCalls).toBe(1));
    await waitFor(() => expect(similarCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("유사 공고 갱신 완료")).toBeInTheDocument();
    expect(screen.queryByText(/임베딩|bid_vector_ml_inference/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "유사 공고 갱신" })).toBeEnabled();
  });
});

describe("ProjectDetailScreen — 금액 basis 표시", () => {
  function stubDetailFetch(project: ProjectDetailResponse) {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === `/api/v1/projects/${project.id}`) return jsonResponse(project);
      if (url.startsWith(`/api/v1/projects/${project.id}/decision-timeline`))
        return jsonResponse(projectBareTimeline);
      if (url.startsWith(`/api/v1/projects/${project.id}/similar`))
        return jsonResponse(emptySimilar);
      if (url === "/api/v1/analytics/event" && init?.method === "POST")
        return jsonResponse({}, 200);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
  }

  it("추정가격과 투찰 기준금액을 서로 다른 금액으로 분리해 보여준다", async () => {
    window.history.pushState({}, "", "/dashboard/projects/101");
    stubDetailFetch(projectA);

    renderApp();

    expect(await screen.findByText("추정가격(부가세 별도)")).toBeInTheDocument();
    expect(screen.getByText("투찰 기준금액(기초금액/사업금액)")).toBeInTheDocument();
    expect(screen.getByText("₩500,000,000")).toBeInTheDocument();
    expect(screen.getByText("₩550,000,000")).toBeInTheDocument();
    expect(screen.getByText("과세 공고 부가세 포함")).toBeInTheDocument();
    expect(screen.getByText(/공고 기초금액\(신뢰\)/)).toBeInTheDocument();
    expect(screen.getByText(/추정가격 대비 1.100배/)).toBeInTheDocument();
    // 두 금액을 "예산" 한 이름으로 묶던 표기가 남아 있으면 안 된다.
    expect(screen.queryByText("예산")).not.toBeInTheDocument();
  });

  it("기초금액을 확보하지 못한 공고는 투찰 기준금액 행을 만들지 않는다", async () => {
    window.history.pushState({}, "", "/dashboard/projects/101");
    stubDetailFetch({
      ...projectA,
      bid_base_amount: 0,
      bid_base_source: null,
      bid_base_to_estimate_ratio: null
    });

    renderApp();

    expect(await screen.findByText("추정가격(부가세 별도)")).toBeInTheDocument();
    expect(
      screen.queryByText("투찰 기준금액(기초금액/사업금액)")
    ).not.toBeInTheDocument();
  });
});
