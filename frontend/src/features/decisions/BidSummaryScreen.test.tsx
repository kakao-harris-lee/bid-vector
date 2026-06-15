import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { BidSummaryResponse } from "@/shared/types/bidSummary";

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

const bidSummary: BidSummaryResponse = {
  decision_record_id: 101,
  operator_id: 1,
  generated_at: "2026-06-12T00:00:00Z",
  notice: {
    project_id: 11,
    title: "테스트 공항 시설 공고",
    notice_number: "R26BK00123-001",
    category: "construction",
    business_type_label: "토목공사",
    budget_estimate: 500_000_000,
    demand_agency: "한국공항공사",
    issuing_agency: "조달청",
    deadline: "2026-06-20T05:00:00Z",
    source_url: null,
    status: "open"
  },
  recommendation: {
    recommended_amount: 430_000_000,
    recommended_bid_rate: 0.86,
    probability_score: 0.72,
    action: "bid_now",
    decision_status: "planned",
    priority_score: 0.8,
    matched_score: 0.75,
    competitiveness_score: 0.65,
    reasoning: "면허·지역·예산 모두 충족하며 예측 적정대 내 가격입니다.",
    strengths: ["면허 일치", "지역 우대"],
    risk_flags: ["경쟁 다수"]
  },
  prediction: {
    predicted_price: 432_000_000,
    predicted_bid_rate: 0.864,
    price_range_min: 420_000_000,
    price_range_max: 445_000_000,
    confidence_score: 0.7,
    pricing_mode: "model",
    predictor_name: "price-predictor-v3",
    guardrail_applied: true,
    floor_bid_rate: 0.8,
    created_at: "2026-06-11T00:00:00Z"
  },
  category_floor: {
    category: "construction",
    business_group: "토목",
    floor_bid_rate: 0.8,
    floor_price: 400_000_000,
    note: "카테고리 낙찰하한율(참고)입니다. 실제 낙찰하한가는 개찰 시 결정됩니다."
  },
  field_stat: {
    category: "construction",
    settled_count: 42,
    est_price_close_rate: 0.31,
    eligible_favorable_rate: 0.58,
    source_run_id: 7,
    source_operator_slug: "synthetic-aggressive",
    note: "최신 백테스트 분야 추정 지표입니다. 실제 낙찰률이 아닙니다."
  },
  direct_submission_notice:
    "이 요약은 투찰 판단 참고용입니다. 실제 나라장터(KONEPS) 투찰서 작성·제출은 운영자가 직접 진행해야 합니다."
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
  window.localStorage.setItem("bid-vector-dashboard-token", "token-summary");
  window.history.pushState({}, "", "/dashboard/decisions/101/summary");
  vi.restoreAllMocks();
});

describe("BidSummaryScreen", () => {
  it("추천 투찰가·투찰률·가격 적합도(추정)·근거·하한율 참고·분야 통계·직접 제출 안내를 렌더한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(bidSummary);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    // 추천 투찰가/투찰률
    expect(await screen.findByText("추천 투찰가")).toBeInTheDocument();
    expect(screen.getByText("₩430,000,000")).toBeInTheDocument();
    expect(screen.getByText("86.0%")).toBeInTheDocument();

    // 가격 적합도(추정) 라벨 정직성 — "낙찰 확률/가능성" 류 문구가 없어야 함.
    expect(screen.getAllByText("가격 적합도(추정)").length).toBeGreaterThan(0);
    expect(screen.getByText("72.0%")).toBeInTheDocument();
    expect(screen.queryByText(/낙찰 확률/)).toBeNull();
    expect(screen.queryByText(/낙찰 가능성/)).toBeNull();

    // 근거 / 강점 / 리스크
    expect(
      screen.getByText("면허·지역·예산 모두 충족하며 예측 적정대 내 가격입니다.")
    ).toBeInTheDocument();
    expect(screen.getByText("면허 일치")).toBeInTheDocument();
    expect(screen.getByText("경쟁 다수")).toBeInTheDocument();

    // 카테고리 낙찰하한율(참고) + 개찰 전 미공개 note
    expect(screen.getByText("카테고리 낙찰하한율 (참고)")).toBeInTheDocument();
    expect(
      screen.getByText(/개찰 전에는 실제 낙찰하한가가 미공개/)
    ).toBeInTheDocument();

    // 분야 통계
    expect(screen.getByText("분야 통계 (백테스트 추정)")).toBeInTheDocument();
    expect(screen.getByText("42건")).toBeInTheDocument();

    // 직접 제출 안내가 눈에 띄게(서버 문구 그대로)
    expect(
      screen.getByText(/실제 나라장터\(KONEPS\) 투찰서 작성·제출은 운영자가 직접 진행/)
    ).toBeInTheDocument();
  });

  it("로딩 중에는 로딩 문구를, 404 응답에는 에러 alert을 렌더한다", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      // 요약 엔드포인트는 404 (없는 결정 기록)
      return jsonResponse({ detail: "not found" }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    // 404 → 에러 alert
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "투찰 요약을 불러오지 못했습니다."
      );
    });
  });

  it("prediction·field_stat 가 null 이면 graceful empty 문구를 렌더한다", async () => {
    const minimal: BidSummaryResponse = {
      ...bidSummary,
      recommendation: {
        ...bidSummary.recommendation,
        recommended_bid_rate: null,
        strengths: [],
        risk_flags: []
      },
      prediction: null,
      field_stat: null,
      category_floor: { ...bidSummary.category_floor, floor_bid_rate: null, floor_price: null }
    };
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
      if (url.endsWith("/api/v1/operations/bid-decisions/101/summary")) {
        return jsonResponse(minimal);
      }
      return jsonResponse({}, 404);
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "투찰 의사결정 요약", level: 2 })
    ).toBeInTheDocument();

    expect(
      await screen.findByText("이 결정에 연결된 가격 예측이 없습니다.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("이 분야의 백테스트 통계가 아직 없습니다.")
    ).toBeInTheDocument();
    expect(
      screen.getByText("이 카테고리에 설정된 낙찰하한율이 없습니다.")
    ).toBeInTheDocument();
    expect(screen.getByText("표시할 강점이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("감지된 주요 리스크가 없습니다.")).toBeInTheDocument();
  });
});
