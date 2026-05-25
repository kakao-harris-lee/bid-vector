import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type {
  OperatorStrategyCandidatesResponse,
  OperatorStrategyResponse,
  OperatorStrategyRunListResponse
} from "@/shared/types/strategy";
import type { DashboardSummaryResponse } from "@/shared/types";

const baseStrategy: OperatorStrategyResponse = {
  operator_id: 1,
  focus_categories: ["용역"],
  focus_regions: [],
  exclude_regions: [],
  required_keywords: [],
  exclude_keywords: [],
  min_budget_estimate: 0,
  max_budget_estimate: 0,
  minimum_match_score: 0.6,
  minimum_probability_score: 0.55,
  bid_now_threshold: 0.7,
  review_threshold: 0.5,
  auto_workload_penalty_multiplier: 1,
  category_priority_overrides: {},
  notify_only_high_priority: true,
  max_recommended_candidates: 10,
  strategy_configured: true
};

const baseCandidates: OperatorStrategyCandidatesResponse = {
  operator_id: 1,
  evaluated_project_count: 12,
  returned_candidate_count: 3,
  high_priority_only: false,
  candidates: []
};

const baseRuns: OperatorStrategyRunListResponse = {
  operator_id: 1,
  result_count: 0,
  runs: []
};

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

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

interface RouteOverride {
  matcher: (url: string, init?: RequestInit) => boolean;
  handler: (init?: RequestInit) => Promise<Response>;
}

function buildFetchMock({
  strategy = baseStrategy,
  candidates = baseCandidates,
  runs = baseRuns,
  overrides = []
}: {
  strategy?: OperatorStrategyResponse;
  candidates?: OperatorStrategyCandidatesResponse;
  runs?: OperatorStrategyRunListResponse;
  overrides?: RouteOverride[];
} = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const override of overrides) {
      if (override.matcher(url, init)) return override.handler(init);
    }
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url.endsWith("/api/v1/operator/strategy")) return jsonResponse(strategy);
    if (url.startsWith("/api/v1/operator/strategy/candidates")) return jsonResponse(candidates);
    if (url.startsWith("/api/v1/operator/strategy/monitor/runs")) return jsonResponse(runs);
    return jsonResponse({}, 404);
  });
}

function findPutCall(fetchMock: ReturnType<typeof buildFetchMock>) {
  return fetchMock.mock.calls.find(([url, init]) => {
    return (
      String(url).endsWith("/api/v1/operator/strategy") &&
      (init as RequestInit | undefined)?.method === "PUT"
    );
  });
}

function findNumberByLabel(label: string): HTMLInputElement {
  const nodes = screen.getAllByLabelText(label);
  const number = nodes.find(
    (el) => (el as HTMLInputElement).type === "number"
  ) as HTMLInputElement | undefined;
  if (!number) throw new Error(`number input with label '${label}' not found`);
  return number;
}

beforeEach(() => {
  window.localStorage.setItem("bid-vector-dashboard-token", "token-strategy");
  window.history.pushState({}, "", "/dashboard/strategy");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("StrategyEditor", () => {
  it("저장 시 review_threshold > bid_now_threshold이면 클라이언트 검증으로 차단", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/strategy", expect.anything())
    );

    // Wait until the form has reset to loaded values (initial 0.5 from baseStrategy).
    await waitFor(() => {
      expect(findNumberByLabel("검토 임계값").value).toBe("0.5");
    });

    const reviewSpin = findNumberByLabel("검토 임계값");
    fireEvent.change(reviewSpin, { target: { value: "0.9" } });
    fireEvent.blur(reviewSpin);

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(
      await screen.findByText("검토 임계값은 즉시 투찰 임계값보다 클 수 없습니다.")
    ).toBeInTheDocument();

    expect(findPutCall(fetchMock)).toBeUndefined();
  });

  it("정상 저장 시 PUT 호출 + candidates 쿼리 재요청", async () => {
    const candidatesAfter: OperatorStrategyCandidatesResponse = {
      ...baseCandidates,
      evaluated_project_count: 30,
      returned_candidate_count: 7
    };
    let candidatesCallCount = 0;
    const candidatesOverride: RouteOverride = {
      matcher: (url) => url.startsWith("/api/v1/operator/strategy/candidates"),
      handler: () => {
        candidatesCallCount += 1;
        return jsonResponse(candidatesCallCount === 1 ? baseCandidates : candidatesAfter);
      }
    };
    const putOverride: RouteOverride = {
      matcher: (url, init) => url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT",
      handler: () =>
        jsonResponse({
          ...baseStrategy,
          minimum_match_score: 0.75
        })
    };
    const fetchMock = buildFetchMock({ overrides: [putOverride, candidatesOverride] });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(findNumberByLabel("최소 매칭 점수").value).toBe("0.6");
    });

    const matchSpin = findNumberByLabel("최소 매칭 점수");
    fireEvent.change(matchSpin, { target: { value: "0.75" } });
    fireEvent.blur(matchSpin);

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(findPutCall(fetchMock)).toBeDefined());
    expect(await screen.findByText("전략 저장 완료")).toBeInTheDocument();

    await waitFor(() => expect(candidatesCallCount).toBeGreaterThanOrEqual(2));
    await waitFor(() => {
      expect(screen.getByText("7건")).toBeInTheDocument();
    });
  });

  it("저장이 서버 측에서 실패하면 danger toast가 나타난다", async () => {
    const putOverride: RouteOverride = {
      matcher: (url, init) => url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT",
      handler: () => jsonResponse({ detail: "validation failed" }, 400)
    };
    const fetchMock = buildFetchMock({ overrides: [putOverride] });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(findNumberByLabel("최소 매칭 점수").value).toBe("0.6");
    });

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => expect(findPutCall(fetchMock)).toBeDefined());
    expect(await screen.findByText("전략 저장 실패")).toBeInTheDocument();
    expect(screen.getByText("전략 저장에 실패했습니다.")).toBeInTheDocument();
  });
});
