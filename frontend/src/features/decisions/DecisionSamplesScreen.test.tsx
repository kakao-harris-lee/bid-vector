import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { DecisionSamplesResponse } from "@/shared/types/decisionSamples";

const NOTES =
  "이 목록은 시스템이 산출·기록한 추천/예측의 감사 증적이며, 정산·실낙찰 결과가 아닙니다. probability_score 는 가격 적합도(추정)이지 P(낙찰)이 아닙니다.";

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

function buildResponse(
  overrides: Partial<DecisionSamplesResponse> = {}
): DecisionSamplesResponse {
  return {
    operator_id: 1,
    period_days: 30,
    limit: 50,
    sample_count: 1,
    generated_at: "2026-05-19T00:00:00Z",
    truncated: false,
    notes: NOTES,
    samples: [
      {
        decision_record_id: 9001,
        project_id: 101,
        notice_number: "2026-NOTICE-0001",
        title: "공공 클라우드 구축 사업",
        demand_agency: "행정안전부",
        category: "소프트웨어",
        budget_estimate: 500_000_000,
        deadline: "2026-05-25T05:00:00Z",
        recommended_amount: 482_000_000,
        recommended_bid_rate: 0.872,
        action: "bid_now",
        decision_status: "submitted",
        probability_score: 0.731,
        priority_score: 0.6,
        matched_score: 0.71,
        competitiveness_score: 0.55,
        reasoning: "분야 적합도 높음 · 카테고리 낙찰하한 통과",
        decided_at: "2026-05-18T02:00:00Z",
        created_at: "2026-05-18T02:00:00Z",
        updated_at: "2026-05-18T02:00:00Z",
        prediction: {
          predicted_price: 478_000_000,
          price_range_min: 470_000_000,
          price_range_max: 486_000_000,
          confidence_score: 0.64,
          predictor_name: "statistical-v3",
          predictor_family: "statistical",
          pricing_mode: "heuristic",
          created_at: "2026-05-18T02:00:00Z"
        }
      }
    ],
    ...overrides
  };
}

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve("csv,body"),
    headers: { get: () => null }
  } as unknown as Response);
}

function installFetchMock(response: DecisionSamplesResponse = buildResponse()): void {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url.startsWith("/api/v1/operations/decision-samples")) {
      return jsonResponse(response);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("bid-vector-dashboard-token", "token-samples");
  window.history.pushState({}, "", "/admin/decision-samples");
  vi.restoreAllMocks();
  // jsdom 의 URL 에는 createObjectURL/revokeObjectURL 이 없으므로 메서드만
  // 주입한다 — 전체 global 을 교체하면 react-router 의 `new URL(...)` 이 깨진다.
  (URL as unknown as { createObjectURL: unknown }).createObjectURL = vi.fn(() => "blob:mock");
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();
});

describe("DecisionSamplesScreen", () => {
  it("샘플 행 · 정직 안내(notes) · CSV 버튼을 렌더한다", async () => {
    installFetchMock();

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "의사결정 증적 샘플", level: 2 })
    ).toBeInTheDocument();

    // 샘플 행: 공고명 + 추천 투찰가
    const listCard = (
      await screen.findByRole("heading", { name: "증적 목록" })
    ).closest("[aria-label='증적 목록']") as HTMLElement;
    expect(within(listCard).getByText("공공 클라우드 구축 사업")).toBeInTheDocument();
    expect(within(listCard).getByText("2026-NOTICE-0001")).toBeInTheDocument();
    expect(within(listCard).getByText("₩482,000,000")).toBeInTheDocument();
    // 예측가 + 예측기
    expect(within(listCard).getByText("₩478,000,000")).toBeInTheDocument();
    expect(within(listCard).getByText("statistical-v3")).toBeInTheDocument();

    // 정직 안내(notes)
    const caveatCard = (
      await screen.findByRole("heading", { name: "해석 주의사항" })
    ).closest("[aria-label='해석 주의사항']") as HTMLElement;
    expect(within(caveatCard).getByText(NOTES)).toBeInTheDocument();

    // CSV 다운로드 버튼
    expect(screen.getByRole("button", { name: /CSV 다운로드/ })).toBeInTheDocument();
  });

  it("표본이 0건이면 정직한 빈 상태를 노출한다", async () => {
    installFetchMock(buildResponse({ sample_count: 0, samples: [] }));

    renderApp();

    expect(
      await screen.findByText("기록된 추천/예측 증적이 아직 없습니다.")
    ).toBeInTheDocument();
    // 표본이 없으면 목록 카드는 렌더하지 않는다.
    expect(screen.queryByRole("heading", { name: "증적 목록" })).not.toBeInTheDocument();
    // notes 는 표본이 없어도 정직하게 노출한다.
    expect(screen.getByRole("heading", { name: "해석 주의사항" })).toBeInTheDocument();
  });
});
