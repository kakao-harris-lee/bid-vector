import { act, fireEvent, screen, waitFor } from "@testing-library/react";
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
      updated_at: "2026-05-18T00:00:00Z"
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
  window.history.pushState({}, "", "/dashboard/decisions");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("DecisionsScreen", () => {
  it("상태 전환 버튼을 누르면 PATCH 요청 + 낙관적 업데이트", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.startsWith("/api/v1/analytics/operations-kpi")) return jsonResponse(operationsKpi);
      if (url.startsWith("/api/v1/analytics/decision-funnel")) return jsonResponse(funnel);
      if (url.startsWith("/api/v1/analytics/decision-recommendations"))
        return jsonResponse(recommendations);
      if (
        url === "/api/v1/operations/bid-decisions/101/status" &&
        init?.method === "PATCH"
      ) {
        return jsonResponse({
          id: 101,
          project_id: 11,
          operator_id: 1,
          action: "review",
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

    const submitButton = screen.getAllByRole("button", { name: "제출" })[0];
    fireEvent.click(submitButton);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.find(([url, init]) => {
          return (
            String(url) === "/api/v1/operations/bid-decisions/101/status" &&
            (init as RequestInit | undefined)?.method === "PATCH"
          );
        })
      ).toBeDefined()
    );
    expect(await screen.findByText("상태 변경 완료")).toBeInTheDocument();
  });

  it("세그먼트 차원 탭(카테고리/워크로드/기관)이 토글되어 활성 표시가 바뀐다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
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
});
