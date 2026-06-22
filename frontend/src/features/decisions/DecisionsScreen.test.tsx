import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  DecisionFunnelResponse,
  DecisionRecommendationResponse
} from "@/shared/types/decisions";
import type { OperationsKpiResponse } from "@/shared/types/operations";

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

const funnel: DecisionFunnelResponse = {
  operator_id: 1,
  period_days: 30,
  decision_count: 10,
  project_count: 9,
  active_pending_count: 3,
  submitted_count: 5,
  skipped_count: 2,
  entry_bid_now_count: 6,
  entry_review_count: 3,
  entry_skip_count: 1,
  direct_submitted_count: 2,
  submitted_after_bid_now_count: 2,
  submitted_after_review_count: 1,
  submitted_after_skip_count: 0,
  overall_submission_rate: 0.5,
  workflow_submission_rate: 0.55,
  bid_now_submission_rate: 0.5,
  review_submission_rate: 0.33,
  average_hours_to_submit: 12.5,
  current_period_start: "2026-04-19T00:00:00Z",
  current_period_end: "2026-05-19T00:00:00Z",
  comparison: { decision_count_delta: 2, submitted_count_delta: 1, submission_rate_delta: 0.05 },
  trend_bucket_days: 7,
  breakdown_limit_applied: 5,
  trend: [],
  category_breakdown: [
    { key: "software", label: "software", decision_count: 6, submitted_count: 3, submission_rate: 0.5 },
    { key: "construction", label: "construction", decision_count: 4, submitted_count: 2, submission_rate: 0.5 }
  ],
  workload_source_breakdown: [
    { key: "provided", label: "수동", decision_count: 6, submitted_count: 3, submission_rate: 0.5 }
  ],
  agency_breakdown: [
    { key: "조달청", label: "조달청", decision_count: 5, submitted_count: 3, submission_rate: 0.6 }
  ],
  recent_submissions: [
    {
      decision_record_id: 101,
      project_id: 11,
      project_title: "전이 대상 공고",
      category: "software",
      action: "review",
      decision_status: "reviewing",
      priority_score: 0.7,
      recommended_amount: 50_000_000,
      updated_at: "2026-05-18T00:00:00Z",
      strengths: ["면허 일치", "지역 적합"],
      risk_flags: ["가격 변동성"]
    }
  ]
};

const recommendations: DecisionRecommendationResponse = {
  operator_id: 1,
  period_days: 30,
  decision_count: 10,
  submitted_count: 5,
  active_pending_count: 3,
  overall_submission_rate: 0.5,
  workflow_submission_rate: 0.55,
  bid_now_submission_rate: 0.5,
  review_submission_rate: 0.33,
  recommendation_count: 0,
  recommendation_limit_applied: 5,
  experiment_count: 0,
  headline: "지난 30일 추천 요약",
  comparison: { decision_count_delta: 0, submitted_count_delta: 0, submission_rate_delta: null },
  recommendations: []
};

const operationsKpi: OperationsKpiResponse = {
  operator_id: 1,
  current_operator_id: 1,
  current_operator_username: "operator",
  period_days: 30,
  manual_override: { decision_count: 10, modified_count: 2, modification_rate: 0.2 },
  conversion: {
    decision_count: 10,
    submitted_count: 5,
    overall_submission_rate: 0.5,
    bid_now_submission_rate: 0.5,
    review_submission_rate: 0.33,
    average_hours_to_submit: 12.5
  },
  prediction_accuracy: {
    result_count: 4,
    prediction_sample_count: 4,
    recommendation_sample_count: 4,
    average_prediction_error_rate: 0.03,
    average_recommendation_error_rate: 0.05,
    prediction_within_1_percent_count: 1,
    prediction_within_3_percent_count: 3,
    recommendation_within_1_percent_count: 1,
    recommendation_within_3_percent_count: 2
  },
  missed_opportunities: {
    missed_count: 1,
    items: [
      {
        decision_record_id: 201,
        project_id: 31,
        project_title: "놓친 공고",
        deadline: "2026-05-15T00:00:00Z",
        initial_action: "bid_now",
        decision_status: "planned",
        priority_score: 0.8
      }
    ]
  },
  review_time: {
    average_review_minutes: 8.2,
    sample_count: 4
  },
  recommendation_feedback: {
    useful_count: 3,
    not_useful_count: 1,
    review_value_rate: 0.75,
    feedback_count: 4
  },
  settlement_coverage: {
    total_paper_bids: 8,
    settled_count: 5,
    coverage_rate: 0.625,
    forward_paper_bids: 3,
    forward_settled_count: 2,
    forward_coverage_rate: 0.667
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
  window.localStorage.setItem("bid-vector-dashboard-token", "token-decisions");
  window.history.pushState({}, "", "/admin/decisions");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("DecisionsScreen", () => {
  it("최근 결정 카드에 메인 대시보드와 동일한 3 action 버튼(투찰/검토/보류)을 렌더한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "결정 게이트웨이", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText("전이 대상 공고")).toBeInTheDocument();

    // 메인 대시보드 InlineActionButtons와 정확히 같은 aria-label 사용 — 3개만 노출되어야 함.
    const submitButton = await screen.findByRole("button", { name: "투찰로 전환" });
    const reviewButton = screen.getByRole("button", { name: "검토로 전환" });
    const skipButton = screen.getByRole("button", { name: "보류 처리" });
    expect(submitButton).toBeInTheDocument();
    expect(reviewButton).toBeInTheDocument();
    expect(skipButton).toBeInTheDocument();

    // 4개짜리 legacy(`예정`/`검토 중`/`제출`/`보류`) 라벨 버튼은 더 이상 없어야 함.
    expect(screen.queryByRole("button", { name: "예정" })).toBeNull();
    expect(screen.queryByRole("button", { name: "검토 중" })).toBeNull();
    expect(screen.queryByRole("button", { name: "제출" })).toBeNull();

    // 현재 decision_status="reviewing"이므로 검토 버튼이 aria-pressed.
    expect(reviewButton).toHaveAttribute("aria-pressed", "true");
    expect(submitButton).toHaveAttribute("aria-pressed", "false");
    expect(skipButton).toHaveAttribute("aria-pressed", "false");

    // 사유 초안 작성 전에는 전환을 막아 UX에서 기록 필요성을 드러낸다.
    expect(submitButton).toBeDisabled();
    expect(reviewButton).toBeDisabled();
    expect(skipButton).toBeDisabled();
    expect(screen.getByText("선택 사유 작성 후 전환 가능")).toBeInTheDocument();
  });

  it("추천가 근거 확인 흐름과 현재 가능한 강점/리스크 신호를 보여준다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 전 확인 흐름" })
    ).toBeInTheDocument();
    expect(screen.getByText(/예측 가격대, 하한율 참고값, 분야 통계/)).toBeInTheDocument();

    const evidence = await screen.findByLabelText("전이 대상 공고 추천 근거 확인");
    expect(within(evidence).getByText("추천가 근거 확인")).toBeInTheDocument();
    expect(within(evidence).getByText("우선순위")).toBeInTheDocument();
    expect(within(evidence).getByText("70.0%")).toBeInTheDocument();
    expect(within(evidence).getByText("강점 2개 / 리스크 1개")).toBeInTheDocument();
    expect(within(evidence).getByText("면허 일치")).toBeInTheDocument();
    expect(within(evidence).getByText("가격 변동성")).toBeInTheDocument();
    expect(
      within(evidence).getByText(/운영 KPI의 과거 추천 오차/)
    ).toBeInTheDocument();
  });

  it("투찰 버튼을 누르면 POST /actions(body action=submit) 요청을 보낸다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      if (
        url.startsWith("/api/v1/operations/bid-decisions/101/actions") &&
        init?.method === "POST"
      ) {
        return jsonResponse({
          id: 101,
          project_id: 11,
          operator_id: 1,
          action: "bid_now",
          decision_status: "submitted",
          priority_score: 0.7,
          recommended_amount: 50_000_000,
          probability_score: 0.6,
          matched_score: 0.7,
          reasoning: "",
          created_at: "2026-05-01T00:00:00Z",
          updated_at: "2026-05-19T00:00:00Z"
        });
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "결정 게이트웨이", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText("전이 대상 공고")).toBeInTheDocument();

    const reasonDraft = await screen.findByRole("textbox", {
      name: "결정 101 선택 사유 메모"
    });
    fireEvent.change(reasonDraft, { target: { value: "추천가와 리스크를 확인해 투찰 전환" } });
    expect(await screen.findByText("선택 사유 작성됨")).toBeInTheDocument();

    const submitButton = await screen.findByRole("button", { name: "투찰로 전환" });
    expect(submitButton).toBeEnabled();
    fireEvent.click(submitButton);

    const actionCall = await waitFor(() => {
      const call = fetchMock.mock.calls.find(([url, init]) => {
        return (
          String(url).startsWith("/api/v1/operations/bid-decisions/101/actions") &&
          (init as RequestInit | undefined)?.method === "POST"
        );
      });
      expect(call).toBeDefined();
      return call!;
    });

    // PATCH /status는 더 이상 호출되지 않아야 함.
    expect(
      fetchMock.mock.calls.find(([url, init]) => {
        return (
          String(url) === "/api/v1/operations/bid-decisions/101/status" &&
          (init as RequestInit | undefined)?.method === "PATCH"
        );
      })
    ).toBeUndefined();

    const init = actionCall[1] as RequestInit | undefined;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    expect(body).toEqual({ action: "submit" });

    // useApplyBidDecisionActionMutation의 한국어 토스트 — `bid_now`에 매핑됨.
    expect(await screen.findByText("투찰 결정으로 전환했습니다")).toBeInTheDocument();
  });

  it("세그먼트 차원 탭(카테고리/워크로드/기관)이 토글되어 활성 표시가 바뀐다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "결정 게이트웨이", level: 2 })
    ).toBeInTheDocument();

    // 운영 KPI 패널이 함께 렌더되는지 (api mock 기반)
    expect(await screen.findByRole("heading", { name: /운영 KPI/ })).toBeInTheDocument();
    expect(await screen.findByLabelText("놓친 유효 공고")).toBeInTheDocument();
    expect(await screen.findByText("놓친 공고")).toBeInTheDocument();

    const categoryTab = await screen.findByRole("tab", { name: "카테고리" });
    expect(categoryTab).toHaveAttribute("aria-selected", "true");

    const agencyTab = screen.getByRole("tab", { name: "기관" });
    fireEvent.click(agencyTab);

    await waitFor(() =>
      expect(agencyTab).toHaveAttribute("aria-selected", "true")
    );
    expect(categoryTab).toHaveAttribute("aria-selected", "false");
  });

  // Regression: DecisionsScreen is admin-only; the "투찰 요약" link targets the
  // user app (/dashboard/decisions/:id/summary). In the standalone admin bundle
  // that crosses the app boundary, so it must be a full-page navigation
  // (window.location.assign) — an in-app navigate would hit the admin catch-all.
  it("standalone admin 번들에서 '투찰 요약' 클릭은 user 앱으로 full-page 이동한다", async () => {
    vi.stubEnv("BASE_URL", "/admin/");
    const originalLocation = window.location;
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign: assignMock }
    });

    try {
      const fetchMock = vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
        if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
        if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
        if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
        if (url.startsWith("/api/v1/analytics/decision-recommendations"))
          return jsonResponse(recommendations);
        return jsonResponse({}, 404);
      });
      vi.stubGlobal("fetch", fetchMock);

      renderApp();

      const summaryButton = await screen.findByRole("button", { name: "투찰 요약" });
      fireEvent.click(summaryButton);

      expect(assignMock).toHaveBeenCalledWith("/dashboard/decisions/101/summary");
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: originalLocation
      });
      vi.unstubAllEnvs();
    }
  });

  // Combined-test branch (base "/"): both surfaces share one router, so the
  // same link stays in-app — exercised by `renderApp`'s combined route tree.
  it("combined-test 분기에서 '투찰 요약' 클릭은 in-app으로 summary 경로를 연다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url === "/api/v1/operator/accounts") return jsonResponse(privilegedAccounts);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      if (url.startsWith("/api/v1/decisions/101/summary")) return jsonResponse({}, 404);
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const summaryButton = await screen.findByRole("button", { name: "투찰 요약" });
    fireEvent.click(summaryButton);

    await waitFor(() =>
      expect(window.location.pathname).toBe("/dashboard/decisions/101/summary")
    );
  });
});
