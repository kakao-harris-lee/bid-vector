import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  BidDecisionTimelineResponse,
  ProjectResponse,
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

const projectA: ProjectResponse = {
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
  created_at: "2026-05-01T00:00:00Z"
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
  target_embedding_model: "test",
  search_mode: "python_fallback",
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

  it("임베딩 작업 완료까지 폴링한 뒤 유사 공고를 다시 조회한다", async () => {
    window.history.pushState({}, "", "/dashboard/projects/101");
    let similarCalls = 0;
    let statusCalls = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/projects/101") return jsonResponse(projectA);
      if (url.startsWith("/api/v1/projects/101/decision-timeline"))
        return jsonResponse(projectBareTimeline);
      if (url.startsWith("/api/v1/projects/101/similar")) {
        similarCalls += 1;
        return jsonResponse(emptySimilar);
      }
      if (
        url === "/api/v1/projects/101/embedding/refresh?force=true" &&
        init?.method === "POST"
      ) {
        return jsonResponse(
          {
            project_id: 101,
            task_id: "embedding-task-1",
            task_name: "jobs.rebuild_project_embeddings",
            queue: "bid_vector_ml_inference",
            status: "queued",
            detail: "queued",
            poll_url: "/api/v1/projects/embeddings/rebuild/tasks/embedding-task-1"
          },
          202
        );
      }
      if (url.endsWith("/embeddings/rebuild/tasks/embedding-task-1")) {
        statusCalls += 1;
        return jsonResponse({
          task_id: "embedding-task-1",
          task_name: "jobs.rebuild_project_embeddings",
          status: "completed",
          raw_status: "SUCCESS",
          ready: true,
          successful: true,
          detail: "completed",
          error: null
        });
      }
      if (url === "/api/v1/analytics/event" && init?.method === "POST") {
        return jsonResponse({}, 200);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();
    fireEvent.click(await screen.findByRole("button", { name: "임베딩 재계산" }));

    await waitFor(() => expect(statusCalls).toBe(1));
    await waitFor(() => expect(similarCalls).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText("임베딩 재계산 완료")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "임베딩 재계산" })).toBeEnabled();
  });
});
