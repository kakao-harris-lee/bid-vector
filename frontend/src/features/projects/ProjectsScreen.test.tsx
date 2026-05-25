import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  ProjectResponse,
  ProjectSimilaritySearchResponse
} from "@/shared/types/project";

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
  description: "공항 IT 시스템 구축",
  requirements: "공항 시스템 운영 경험",
  budget_estimate: 500_000_000,
  category: "software",
  notice_number: "A-001",
  source_url: null,
  issuing_agency: "조달청",
  demand_agency: "한국공항공사",
  status: "open",
  created_at: "2026-05-01T00:00:00Z"
};

const projectB: ProjectResponse = {
  id: 102,
  title: "동대문 도로 공사",
  description: "도로 보수",
  requirements: "토목 면허",
  budget_estimate: 80_000_000,
  category: "construction",
  notice_number: "B-002",
  source_url: null,
  issuing_agency: "서울시",
  demand_agency: "동대문구청",
  status: "open",
  created_at: "2026-05-02T00:00:00Z"
};

function jsonResponse(payload: unknown, status = 200, headers: Record<string, string> = {}): Promise<Response> {
  const headerMap = new Headers({ "Content-Type": "application/json", ...headers });
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: {
      get: (name: string) => headerMap.get(name) ?? null
    }
  } as unknown as Response);
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-projects");
  window.history.pushState({}, "", "/dashboard/projects");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("ProjectsScreen", () => {
  it("필터 입력이 디바운스 후 한 번만 백엔드 호출을 발생시킨다", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    const projectCalls: Array<URL> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/dashboard/summary") return jsonResponse(emptySummary);
      if (url.pathname === "/api/v1/projects/") {
        projectCalls.push(url);
        const q = url.searchParams.get("q") ?? "";
        const items = q.includes("공항") ? [projectA] : [projectA, projectB];
        return jsonResponse(items, 200, { "X-Total-Count": String(items.length) });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByLabelText("제목 또는 공고번호 검색")).toBeInTheDocument();
    await waitFor(() =>
      expect(projectCalls.some((url) => !url.searchParams.has("q"))).toBe(true)
    );
    const beforeTypingCount = projectCalls.length;

    const input = screen.getByLabelText("제목 또는 공고번호 검색") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "공" } });
    fireEvent.change(input, { target: { value: "공항" } });
    // No fetch happened immediately after the keystrokes (still within debounce window).
    expect(projectCalls.length).toBe(beforeTypingCount);

    await act(async () => {
      vi.advanceTimersByTime(300);
    });

    await waitFor(() =>
      expect(projectCalls.some((url) => url.searchParams.get("q") === "공항")).toBe(true)
    );

    // Should have produced one additional q=공항 request, not three (one per keystroke).
    const qaCalls = projectCalls.filter((url) => url.searchParams.get("q") === "공항");
    expect(qaCalls.length).toBe(1);

    vi.useRealTimers();
  });

  it("카테고리 필터를 변경하면 query param이 백엔드 호출에 반영된다", async () => {
    const projectCalls: Array<URL> = [];
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/dashboard/summary") return jsonResponse(emptySummary);
      if (url.pathname === "/api/v1/projects/") {
        projectCalls.push(url);
        const category = url.searchParams.get("category");
        const items = category === "construction" ? [projectB] : [projectA, projectB];
        return jsonResponse(items, 200, { "X-Total-Count": String(items.length) });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByLabelText("카테고리")).toBeInTheDocument();
    await waitFor(() => expect(projectCalls.length).toBeGreaterThanOrEqual(1));

    const categoryInput = screen.getByLabelText("카테고리") as HTMLInputElement;
    fireEvent.change(categoryInput, { target: { value: "construction" } });

    await waitFor(() =>
      expect(
        projectCalls.some((url) => url.searchParams.get("category") === "construction")
      ).toBe(true)
    );

    expect(await screen.findByText("동대문 도로 공사")).toBeInTheDocument();
    expect(screen.queryByText("공항 시스템 구축")).not.toBeInTheDocument();
  });

  it("상세 화면의 유사 공고 클릭 시 해당 ID로 라우팅된다", async () => {
    const projectAReload = projectA;
    const similarResponse: ProjectSimilaritySearchResponse = {
      target_project_id: projectA.id,
      target_project_title: projectA.title,
      target_embedding_model: "test",
      search_mode: "python_fallback",
      same_category_only: true,
      min_similarity: 0.15,
      result_count: 1,
      results: [
        {
          project_id: projectB.id,
          title: projectB.title,
          category: projectB.category,
          status: projectB.status,
          budget_estimate: projectB.budget_estimate,
          deadline: null,
          created_at: projectB.created_at,
          similarity_score: 0.82,
          embedding_model: "test"
        }
      ]
    };

    window.history.pushState({}, "", `/dashboard/projects/${projectA.id}`);

    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/dashboard/summary") return jsonResponse(emptySummary);
      if (url.pathname === `/api/v1/projects/${projectA.id}`) return jsonResponse(projectAReload);
      if (url.pathname === `/api/v1/projects/${projectB.id}`) return jsonResponse(projectB);
      if (url.pathname === `/api/v1/projects/${projectA.id}/similar`) return jsonResponse(similarResponse);
      if (url.pathname === `/api/v1/projects/${projectB.id}/similar`) {
        return jsonResponse({ ...similarResponse, target_project_id: projectB.id, results: [] });
      }
      if (url.pathname.startsWith("/api/v1/operations/projects/")) {
        return jsonResponse({
          operator_id: 1,
          project: {
            id: projectA.id,
            title: projectA.title,
            category: projectA.category,
            status: projectA.status,
            budget_estimate: projectA.budget_estimate,
            deadline: null,
            notice_number: projectA.notice_number,
            source_url: null,
            issuing_agency: projectA.issuing_agency,
            demand_agency: projectA.demand_agency
          },
          result_count: 0,
          limit_applied: 10,
          latest_decision_record_id: null,
          timeline: []
        });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    // 상세 화면이 로드되고 유사 공고가 표시될 때까지 대기
    expect(await screen.findByText("공항 시스템 구축")).toBeInTheDocument();
    const similarButton = await screen.findByRole("button", { name: /동대문 도로 공사/ });
    fireEvent.click(similarButton);

    await waitFor(() =>
      expect(window.location.pathname).toBe(`/dashboard/projects/${projectB.id}`)
    );
  });
});
