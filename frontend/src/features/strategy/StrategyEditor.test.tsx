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
import type { OperatorAccountListResponse } from "@/shared/api";
import { ACTIVE_OPERATOR_STORAGE_KEY } from "@/app/operatorContext";

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
  candidates: [],
  // PR-B 이후 GET 은 스냅샷 순수 읽기다. 계산된 스냅샷(idle + computed_at)이라야
  // 카드가 통계·목록을 그리고 폴링을 멈춘다(부트스트랩=computed_at null 은
  // 진행 UI 로 빠진다 — features/strategy/snapshotState.ts).
  computed_at: "2026-07-30T02:00:00Z",
  snapshot_status: "idle",
  stale: false
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

const accountsCatalogue: OperatorAccountListResponse = {
  current_operator_id: 1,
  current_operator_username: "operator",
  is_privileged: true,
  operator_count: 2,
  operators: [
    {
      operator_id: 1,
      username: "operator",
      full_name: "본사 운영자",
      company: "본사",
      business_type: "용역",
      is_canonical: true,
      is_synthetic: false,
      is_active: true,
      profile_configured: true
    },
    {
      operator_id: 11,
      username: "synthetic-aggressive",
      full_name: "공격형",
      company: "Synthetic A",
      business_type: "용역",
      is_canonical: false,
      is_synthetic: true,
      is_active: true,
      profile_configured: true
    }
  ]
};

function buildFetchMock({
  strategy = baseStrategy,
  candidates = baseCandidates,
  runs = baseRuns,
  impersonatedStrategy,
  accounts = accountsCatalogue,
  overrides = []
}: {
  strategy?: OperatorStrategyResponse;
  candidates?: OperatorStrategyCandidatesResponse;
  runs?: OperatorStrategyRunListResponse;
  impersonatedStrategy?: OperatorStrategyResponse;
  accounts?: OperatorAccountListResponse;
  overrides?: RouteOverride[];
} = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const override of overrides) {
      if (override.matcher(url, init)) return override.handler(init);
    }
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
    if (
      url.startsWith("/api/v1/operator/strategy?operator_id=") &&
      impersonatedStrategy
    ) {
      return jsonResponse(impersonatedStrategy);
    }
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
  window.localStorage.clear();
  window.localStorage.setItem("bid-vector-dashboard-token", "token-strategy");
  window.history.pushState({}, "", "/dashboard/strategy");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("StrategyEditor", () => {
  it("추천 판단 기준과 가격 적합도 caveat를 현재 전략값으로 보여준다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "추천 판단 기준" })
    ).toBeInTheDocument();
    expect(screen.getByText("대상 조건")).toBeInTheDocument();
    expect(screen.getByText("1개")).toBeInTheDocument();
    expect(screen.getByText("공고 적합도 60.0% 이상")).toBeInTheDocument();
    expect(screen.getByText("가격 적합도(추정) 55.0% 이상")).toBeInTheDocument();
    expect(screen.getByText("투찰 70.0% / 검토 50.0%")).toBeInTheDocument();
    expect(
      screen.getByText(/가격 적합도\(추정\)는 P\(낙찰\)이 아니라/)
    ).toBeInTheDocument();
    expect(screen.getByText(/추천가, 예측 가격대, 하한율 참고값/)).toBeInTheDocument();
  });

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

  it("/dashboard/strategy 에서는 BottomNav 어떤 탭도 active로 강조되지 않는다", async () => {
    vi.stubGlobal("fetch", buildFetchMock());

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();

    const nav = screen.getByRole("navigation", { name: "대시보드 탭" });
    const activeButtons = Array.from(nav.querySelectorAll('[aria-current="page"]'));
    expect(activeButtons).toHaveLength(0);
  });

  it("ThresholdControl: number input 을 비운 채 blur해도 이전 값이 유지된다", async () => {
    vi.stubGlobal("fetch", buildFetchMock());

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "전략 편집", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(findNumberByLabel("즉시 투찰 임계값").value).toBe("0.7");
    });

    const spin = findNumberByLabel("즉시 투찰 임계값");
    fireEvent.change(spin, { target: { value: "" } });
    fireEvent.blur(spin);

    // After blur, value must NOT have snapped to min (0).
    expect(findNumberByLabel("즉시 투찰 임계값").value).toBe("0.7");
  });

  describe("사용자 surface active operator boundary", () => {
    const syntheticStrategy: OperatorStrategyResponse = {
      ...baseStrategy,
      operator_id: 11
    };

    it("저장된 active operator id가 있어도 사용자 strategy 편집은 본인 URL만 호출한다", async () => {
      window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
      const fetchMock = buildFetchMock({ impersonatedStrategy: syntheticStrategy });
      vi.stubGlobal("fetch", fetchMock);

      renderApp();

      await screen.findByRole("heading", { name: "전략 편집", level: 2 });

      // User surface는 Shell에서 activeOperatorId를 null로 마스킹한다.
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/strategy", expect.anything());
      });
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url) === "/api/v1/operator/strategy?operator_id=11"
        )
      ).toBe(false);

      // 사용자 surface에서는 본인 회사 편집 흐름으로 유지된다.
      expect(screen.queryByTestId("strategy-readonly-notice")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "저장" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "되돌리기" })).toBeInTheDocument();

      await waitFor(() => {
        expect(findNumberByLabel("최소 매칭 점수")).toBeEnabled();
      });
    });
  });
});
