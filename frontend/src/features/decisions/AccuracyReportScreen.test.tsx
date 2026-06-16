import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { AccuracyReportResponse } from "@/shared/types/accuracyReport";

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

function buildReport(overrides: Partial<AccuracyReportResponse> = {}): AccuracyReportResponse {
  return {
    operator_id: 1,
    summary: {
      period_days: 90,
      recommendation_sample_count: 24,
      prediction_sample_count: 22,
      average_recommendation_error_rate: 0.034,
      average_prediction_error_rate: 0.051,
      recommendation_better_than_prediction_count: 14,
      within_1pct_count: 6,
      within_3pct_count: 15,
      within_5pct_count: 19,
      within_1pct_rate: 0.25,
      within_3pct_rate: 0.625,
      within_5pct_rate: 0.792
    },
    error_distribution: [
      { bin_label: "<1%", lower: 0, upper: 0.01, count: 6, rate: 0.25 },
      { bin_label: "1-3%", lower: 0.01, upper: 0.03, count: 9, rate: 0.375 },
      { bin_label: "3-5%", lower: 0.03, upper: 0.05, count: 4, rate: 0.167 },
      { bin_label: "5-10%", lower: 0.05, upper: 0.1, count: 3, rate: 0.125 },
      { bin_label: "≥10%", lower: 0.1, upper: null, count: 2, rate: 0.083 }
    ],
    per_category: [
      {
        category: "소프트웨어",
        sample_count: 12,
        average_recommendation_error_rate: 0.028,
        within_3pct_rate: 0.667
      },
      {
        category: "정보통신",
        sample_count: 8,
        average_recommendation_error_rate: 0.041,
        within_3pct_rate: 0.5
      }
    ],
    time_trend: [
      {
        period_start: "2026-04-01T00:00:00Z",
        period_end: "2026-04-08T00:00:00Z",
        sample_count: 10,
        average_recommendation_error_rate: 0.04
      },
      {
        period_start: "2026-04-08T00:00:00Z",
        period_end: "2026-04-15T00:00:00Z",
        sample_count: 14,
        average_recommendation_error_rate: 0.03
      }
    ],
    items: [
      {
        project_id: 101,
        project_title: "공공 클라우드 구축 사업",
        category: "소프트웨어",
        tender_result_id: 9001,
        result_status: "settled",
        announced_at: "2026-04-10T01:00:00Z",
        winning_amount: 480_000_000,
        winning_rate: 0.872,
        latest_prediction_id: 1,
        predicted_price: 475_000_000,
        prediction_delta_amount: -5_000_000,
        prediction_error_rate: 0.0104,
        latest_decision_record_id: 1,
        recommended_amount: 482_000_000,
        recommendation_delta_amount: 2_000_000,
        recommendation_error_rate: 0.0042,
        recommendation_improved_vs_prediction: true
      }
    ],
    notes: [
      "정산 완료된 건만 포함합니다 (미정산 제외).",
      "error = |추천가 − 실제낙찰가| / 실제낙찰가.",
      "단일 운영자(canonical) 기준입니다."
    ],
    ...overrides
  };
}

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

interface FetchSpy {
  reportCalls: string[];
}

function installFetchMock(
  report: AccuracyReportResponse | ((url: string) => AccuracyReportResponse) = buildReport()
): FetchSpy {
  const spy: FetchSpy = { reportCalls: [] };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url.startsWith("/api/v1/analytics/accuracy-report")) {
      spy.reportCalls.push(url);
      const payload = typeof report === "function" ? report(url) : report;
      return jsonResponse(payload);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return spy;
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("bid-vector-dashboard-token", "token-accuracy");
  window.history.pushState({}, "", "/dashboard/accuracy-report");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("AccuracyReportScreen", () => {
  it("요약 지표 · 오차 분포 bin · 분야 행 · 상세 · caveat 를 렌더한다", async () => {
    installFetchMock();

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "추천 vs 실제 통합 정확도 리포트", level: 2 })
    ).toBeInTheDocument();

    // 요약 지표 카드
    const summarySection = await screen.findByRole("region", { name: "요약 지표" });
    expect(within(summarySection).getByText("추천 표본수")).toBeInTheDocument();
    expect(within(summarySection).getByText("24건")).toBeInTheDocument();
    expect(within(summarySection).getByText("3.4%")).toBeInTheDocument(); // 평균 추천 오차율
    expect(within(summarySection).getByText("±1% 적중률")).toBeInTheDocument();
    expect(within(summarySection).getByText("25.0%")).toBeInTheDocument(); // ±1% rate
    expect(within(summarySection).getByText("추천이 예측보다 나은 건수")).toBeInTheDocument();

    // 오차 분포 bin label
    const distCard = (
      await screen.findByRole("heading", { name: "오차 분포" })
    ).closest("[aria-label='오차 분포']") as HTMLElement;
    expect(within(distCard).getByText("1-3%")).toBeInTheDocument();
    expect(within(distCard).getByText("≥10%")).toBeInTheDocument();

    // 분야별 정확도 행
    const categoryCard = (
      await screen.findByRole("heading", { name: "분야별 정확도" })
    ).closest("[aria-label='분야별 정확도']") as HTMLElement;
    expect(within(categoryCard).getByText("소프트웨어")).toBeInTheDocument();
    expect(within(categoryCard).getByText("정보통신")).toBeInTheDocument();

    // 상세 drill-down
    const itemsCard = (
      await screen.findByRole("heading", { name: "상세" })
    ).closest("[aria-label='상세']") as HTMLElement;
    expect(within(itemsCard).getByText("공공 클라우드 구축 사업")).toBeInTheDocument();

    // 정직 caveat / notes
    const caveatCard = (
      await screen.findByRole("heading", { name: "해석 주의사항" })
    ).closest("[aria-label='해석 주의사항']") as HTMLElement;
    expect(
      within(caveatCard).getByText("정산 완료된 건만 포함합니다 (미정산 제외).")
    ).toBeInTheDocument();
    expect(
      within(caveatCard).getByText("error = |추천가 − 실제낙찰가| / 실제낙찰가.")
    ).toBeInTheDocument();
  });

  it("표본이 0건이면 정직한 빈 상태를 노출하고 차트는 그리지 않는다", async () => {
    installFetchMock(
      buildReport({
        summary: {
          period_days: 90,
          recommendation_sample_count: 0,
          prediction_sample_count: 0,
          average_recommendation_error_rate: null,
          average_prediction_error_rate: null,
          recommendation_better_than_prediction_count: 0,
          within_1pct_count: 0,
          within_3pct_count: 0,
          within_5pct_count: 0,
          within_1pct_rate: null,
          within_3pct_rate: null,
          within_5pct_rate: null
        },
        error_distribution: [],
        per_category: [],
        time_trend: [],
        items: []
      })
    );

    renderApp();

    expect(
      await screen.findByText("정산 완료된 추천 비교 표본이 아직 없습니다.")
    ).toBeInTheDocument();
    // 표본이 없으면 오차 분포/상세 카드는 렌더하지 않는다.
    expect(screen.queryByRole("heading", { name: "오차 분포" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "상세" })).not.toBeInTheDocument();
    // notes 는 표본이 없어도 정직하게 노출한다.
    expect(screen.getByRole("heading", { name: "해석 주의사항" })).toBeInTheDocument();
  });

  it("기간 셀렉터를 바꾸면 새 days 값으로 리포트를 재조회한다", async () => {
    const spy = installFetchMock();

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "추천 vs 실제 통합 정확도 리포트", level: 2 })
    ).toBeInTheDocument();
    expect(spy.reportCalls.some((url) => url.includes("days=90"))).toBe(true);

    const before = spy.reportCalls.length;
    await userEvent.selectOptions(screen.getByLabelText("리포트 기간 (일)"), "30");

    expect(spy.reportCalls.length).toBeGreaterThan(before);
    expect(spy.reportCalls.some((url) => url.includes("days=30"))).toBe(true);
  });
});
