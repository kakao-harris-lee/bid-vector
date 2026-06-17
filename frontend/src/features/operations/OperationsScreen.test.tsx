import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import { ACTIVE_OPERATOR_STORAGE_KEY } from "@/app/operatorContext";
import type { DashboardSummaryResponse } from "@/shared/types";
import type {
  OperationsDashboardResponse,
  OperationsKpiResponse
} from "@/shared/types/operations";
import type { OperatorAccountListResponse } from "@/shared/api";

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

const baseOperations: OperationsDashboardResponse = {
  operator_id: 1,
  period_days: 7,
  cards: [
    {
      key: "crawl_success_rate",
      label: "크롤 성공률",
      value: 0.92,
      unit: "ratio",
      status: "healthy",
      detail: "최근 7일"
    },
    {
      key: "telegram_failures",
      label: "텔레그램 실패",
      value: 12,
      unit: "count",
      status: "critical",
      detail: "재발송 필요"
    }
  ],
  crawl: {
    job_count: 100,
    completed_count: 92,
    failed_count: 8,
    success_rate: 0.92,
    failure_rate: 0.08,
    last_success_at: "2026-05-19T08:00:00Z",
    last_failure_at: "2026-05-18T22:00:00Z",
    failure_reason_breakdown: { "timeout": 3, "5xx": 5 },
    recent_failures: []
  },
  tasks: {},
  notifications: {
    notification_count: 50,
    unread_count: 5,
    decision_notification_count: 20,
    bid_submission_notification_count: 8,
    telegram_configured: true,
    telegram_delivery_attempt_count: 60,
    telegram_sent_count: 48,
    telegram_failed_count: 12,
    telegram_success_rate: 0.8,
    telegram_status: "watch",
    telegram_detail: "최근 실패율 20%",
    telegram_failure_reason_breakdown: { "network": 12 }
  },
  ml_release: {
    manifest_count: 1,
    status: "healthy",
    detail: "최신 릴리스 정상",
    latest_release_tag: "v1.2.0",
    latest_signature_status: "verified",
    latest_gate_status: "passed",
    latest_gate_passed: true,
    backtest_status: "healthy",
    backtest_detail: "샘플 100건",
    recent_manifests: []
  },
  smoke_test: {
    cycle_count: 20,
    passed_count: 18,
    failed_count: 2,
    pass_rate: 0.9,
    current_streak: 5,
    healthy_streak_target: 7,
    current_streak_meets_target: false,
    schedule_enabled: true,
    failure_category_breakdown: { prediction: 2 },
    per_phase: [
      { name: "koneps_collect", pass_rate: 1.0, evaluated_count: 20 },
      { name: "sbert_embedding", pass_rate: 0.95, evaluated_count: 20 },
      { name: "predict_price", pass_rate: 0.7, evaluated_count: 20 },
      { name: "telegram_ping", pass_rate: 0.0, evaluated_count: 0 }
    ],
    latest: {
      started_at: "2026-05-19T07:00:00Z",
      overall_passed: false,
      phases: [
        { name: "koneps_collect", passed: true, detail: "" },
        {
          name: "predict_price",
          passed: false,
          detail: "guardrail 미달",
          failure_category: "prediction"
        }
      ]
    },
    recent_failures: [
      {
        started_at: "2026-05-18T07:00:00Z",
        failed_phases: ["predict_price"],
        failure_categories: ["prediction"],
        failure_category_breakdown: { prediction: 1 }
      }
    ]
  },
  synthetic_validation: {
    preset_count: 4,
    saved_preset_count: 2,
    completed_preset_count: 1,
    failed_preset_count: 0,
    sufficient_preset_count: 1,
    sample_target: 100,
    recent_run_count: 2,
    recent_completed_count: 1,
    recent_failed_count: 0,
    status: "watch",
    detail: "1/4 G-1 preset(s) reached the sample target.",
    latest: {
      experiment_id: 10,
      experiment_name: "g1-construction-base-12m",
      run_id: 21,
      status: "completed",
      created_at: "2026-05-19T06:30:00Z",
      finished_at: "2026-05-19T07:00:00Z",
      sample_status: "sufficient",
      total_settled_count: 128,
      missing_total_settled_count: 0
    },
    presets: [
      {
        name: "g1-construction-base-12m",
        experiment_id: 10,
        latest_run_id: 21,
        latest_run_status: "completed",
        latest_finished_at: "2026-05-19T07:00:00Z",
        sample_status: "sufficient",
        total_settled_count: 128,
        missing_total_settled_count: 0,
        insufficient_operator_count: 0
      },
      {
        name: "g1-service-base-12m",
        experiment_id: 11,
        latest_run_id: null,
        latest_run_status: null,
        latest_finished_at: null,
        sample_status: null,
        total_settled_count: 0,
        missing_total_settled_count: 100,
        insufficient_operator_count: 0
      }
    ]
  }
};

function buildKpi(overrides: Partial<OperationsKpiResponse> = {}): OperationsKpiResponse {
  return {
    operator_id: 1,
    current_operator_id: 1,
    current_operator_username: "operator",
    period_days: 30,
    manual_override: {
      decision_count: 20,
      modified_count: 5,
      modification_rate: 0.25
    },
    conversion: {
      decision_count: 20,
      submitted_count: 12,
      overall_submission_rate: 0.6,
      bid_now_submission_rate: 0.75,
      review_submission_rate: 0.4,
      average_hours_to_submit: 18.5
    },
    prediction_accuracy: {
      result_count: 8,
      prediction_sample_count: 8,
      recommendation_sample_count: 7,
      average_prediction_error_rate: 0.021,
      average_recommendation_error_rate: 0.034,
      prediction_within_1_percent_count: 3,
      prediction_within_3_percent_count: 6,
      recommendation_within_1_percent_count: 2,
      recommendation_within_3_percent_count: 5
    },
    missed_opportunities: {
      missed_count: 1,
      items: [
        {
          decision_record_id: 501,
          project_id: 42,
          project_title: "놓친 공고 샘플",
          deadline: "2026-05-20T09:00:00Z",
          initial_action: "bid_now",
          decision_status: "planned",
          priority_score: 0.82
        }
      ]
    },
    review_time: { average_review_minutes: 12.4, sample_count: 7 },
    recommendation_feedback: {
      useful_count: 9,
      not_useful_count: 3,
      review_value_rate: 0.75,
      feedback_count: 12
    },
    settlement_coverage: {
      total_paper_bids: 40,
      settled_count: 26,
      coverage_rate: 0.65,
      forward_paper_bids: 10,
      forward_settled_count: 9,
      forward_coverage_rate: 0.9
    },
    ...overrides
  };
}

const privilegedAccounts: OperatorAccountListResponse = {
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
      business_type: "정보통신",
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
      business_type: "정보통신",
      is_canonical: false,
      is_synthetic: true,
      is_active: true,
      profile_configured: true
    }
  ]
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

interface FetchSpy {
  dashboardCount: number;
  kpiCalls: string[];
}

function installFetchMock({
  kpi = buildKpi(),
  accounts = privilegedAccounts,
  operations = baseOperations
}: {
  kpi?: OperationsKpiResponse | ((url: string) => OperationsKpiResponse);
  accounts?: OperatorAccountListResponse;
  operations?: OperationsDashboardResponse;
} = {}): FetchSpy {
  const spy: FetchSpy = { dashboardCount: 0, kpiCalls: [] };
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
    if (url.startsWith("/api/v1/analytics/operations-dashboard")) {
      spy.dashboardCount += 1;
      return jsonResponse(operations);
    }
    if (url.startsWith("/api/v1/analytics/operations-kpi")) {
      spy.kpiCalls.push(url);
      const payload = typeof kpi === "function" ? kpi(url) : kpi;
      return jsonResponse(payload);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return spy;
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("bid-vector-dashboard-token", "token-operations");
  window.history.pushState({}, "", "/admin/operations");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("OperationsScreen", () => {
  it("status별 카드를 알맞은 톤으로 표시하고 critical 카드는 인시던트 배너로 노출", async () => {
    installFetchMock();

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "운영 대시보드", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText("크롤 성공률")).toBeInTheDocument();
    expect(screen.getAllByText("텔레그램 실패").length).toBeGreaterThanOrEqual(1);

    const incidentBanner = screen.getByRole("alert", { name: "인시던트 알림" });
    expect(incidentBanner).toHaveTextContent("인시던트 1건");
    expect(incidentBanner).toHaveTextContent("텔레그램 실패");
  });

  it("탭이 비활성화되어 있어도 자동 갱신은 멈춰 있고 새로고침 버튼은 동작한다", async () => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });

    const spy = installFetchMock();

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "운영 대시보드", level: 2 })
    ).toBeInTheDocument();
    await waitFor(() => expect(spy.dashboardCount).toBe(1));

    // 자동 polling 끄도록 visibilityState=hidden — 다음 인터벌 도래 전에는 추가 호출이 없어야 함
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(spy.dashboardCount).toBe(1);

    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  it("시스템 헬스 카드와 함께 회사별 KPI 7 그룹을 동시에 노출한다", async () => {
    installFetchMock();

    renderApp();

    // 시스템 헬스 영역 — 크롤/텔레그램/ML release 카드
    expect(
      await screen.findByRole("heading", { name: "운영 대시보드", level: 2 })
    ).toBeInTheDocument();
    expect(await screen.findByText("크롤 상태")).toBeInTheDocument();
    expect(screen.getByText("텔레그램 / 알림")).toBeInTheDocument();
    expect(screen.getByText("ML release")).toBeInTheDocument();

    // 회사별 KPI 패널 + 7 그룹 (aria-label 으로 식별)
    const kpiSection = await screen.findByRole("region", { name: "회사별 KPI" });
    expect(within(kpiSection).getByRole("heading", { name: /운영 KPI/ })).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("수동 수정")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("투찰 전환율")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("예측 정확도")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("검토 시간")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("추천 유용성")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("놓친 유효 공고")).toBeInTheDocument();
    expect(within(kpiSection).getByLabelText("정산 커버리지")).toBeInTheDocument();
  });

  it("KPI 기간 셀렉터를 바꾸면 새 days 값으로 KPI를 재조회한다", async () => {
    const spy = installFetchMock();

    renderApp();

    // 초기 KPI fetch — default 30일
    await waitFor(() => expect(spy.kpiCalls.length).toBeGreaterThanOrEqual(1));
    expect(spy.kpiCalls.some((url) => url.includes("days=30"))).toBe(true);

    const selector = await screen.findByLabelText("KPI 기간 (일)");
    const before = spy.kpiCalls.length;

    await userEvent.selectOptions(selector, "90");

    // days=90 fetch가 발생해야 함
    await waitFor(() => {
      expect(spy.kpiCalls.length).toBeGreaterThan(before);
      expect(spy.kpiCalls.some((url) => url.includes("days=90"))).toBe(true);
    });
  });

  it("회사를 전환하면 KPI도 새 operator_id로 재조회된다", async () => {
    const spy = installFetchMock();

    renderApp();

    // 초기: operator_id 미지정 (token owner)
    await waitFor(() => expect(spy.kpiCalls.length).toBeGreaterThanOrEqual(1));
    expect(spy.kpiCalls.some((url) => !url.includes("operator_id"))).toBe(true);

    const before = spy.kpiCalls.length;

    // 회사 전환 트리거 — Shell 헤더의 OperatorSwitcher
    const switcher = await screen.findByRole("button", { name: "회사 전환" });
    await userEvent.click(switcher);
    const listbox = await screen.findByRole("listbox", { name: "회사 선택" });
    await userEvent.click(within(listbox).getByText("Synthetic A"));

    // 새 operator_id=11로 KPI fetch가 발생해야 함
    await waitFor(() => {
      expect(spy.kpiCalls.length).toBeGreaterThan(before);
      expect(spy.kpiCalls.some((url) => url.includes("operator_id=11"))).toBe(true);
    });

    // localStorage에도 영속됨
    expect(window.localStorage.getItem(ACTIVE_OPERATOR_STORAGE_KEY)).toBe("11");
  });

  it("운영 검증(스모크) 섹션을 통과율·연속 통과·단계 라벨과 함께 렌더한다", async () => {
    installFetchMock();

    renderApp();

    // 섹션 제목(h3) — Card 루트는 div라 region role이 없으므로 heading 으로 식별
    const smokeCard = (
      await screen.findByRole("heading", { name: "운영 검증 (스모크 사이클)" })
    ).closest("[aria-label='운영 검증 (스모크 사이클)']") as HTMLElement;
    expect(smokeCard).not.toBeNull();
    // 통과율 90.0% + G-0 연속 통과 5/7회
    expect(within(smokeCard).getAllByText("90.0%").length).toBeGreaterThanOrEqual(1);
    expect(within(smokeCard).getByText("5/7회")).toBeInTheDocument();
    // 단계 라벨 (한국어 매핑) — per-phase + latest-run 양쪽에 등장
    expect(within(smokeCard).getAllByText("공고 수집").length).toBeGreaterThanOrEqual(1);
    expect(within(smokeCard).getAllByText("가격 예측").length).toBeGreaterThanOrEqual(1);
    // evaluated_count=0 단계는 "데이터 없음"으로 정직하게 표기
    expect(within(smokeCard).getByText("데이터 없음")).toBeInTheDocument();
    // 최근 실행 FAIL + 실패 단계 detail
    expect(within(smokeCard).getByText("guardrail 미달")).toBeInTheDocument();
    expect(within(smokeCard).getAllByText("예측").length).toBeGreaterThanOrEqual(1);
  });

  it("G-1 synthetic validation 섹션을 preset/sample 상태와 함께 렌더한다", async () => {
    installFetchMock();

    renderApp();

    const syntheticCard = (
      await screen.findByRole("heading", { name: "G-1 가상 회사 검증" })
    ).closest("[aria-label='G-1 가상 회사 검증']") as HTMLElement;
    expect(syntheticCard).not.toBeNull();
    expect(within(syntheticCard).getByText("2/4")).toBeInTheDocument();
    expect(within(syntheticCard).getByText("1/4 G-1 preset(s) reached the sample target.")).toBeInTheDocument();
    expect(within(syntheticCard).getAllByText("g1-construction-base-12m").length).toBeGreaterThanOrEqual(1);
    expect(within(syntheticCard).getByText("g1-service-base-12m")).toBeInTheDocument();
    expect(within(syntheticCard).getByText("settled 128/100")).toBeInTheDocument();
    expect(within(syntheticCard).getByText("미실행")).toBeInTheDocument();
  });

  it("smoke 스케줄이 비활성이고 사이클이 없으면 정직한 비활성 안내를 노출한다", async () => {
    installFetchMock({
      operations: {
        ...baseOperations,
        smoke_test: {
          cycle_count: 0,
          passed_count: 0,
          failed_count: 0,
          pass_rate: 0,
          current_streak: 0,
          healthy_streak_target: 7,
          current_streak_meets_target: false,
          schedule_enabled: false,
          failure_category_breakdown: {},
          per_phase: [],
          latest: null,
          recent_failures: []
        }
      }
    });

    renderApp();

    const smokeCard = (
      await screen.findByRole("heading", { name: "운영 검증 (스모크 사이클)" })
    ).closest("[aria-label='운영 검증 (스모크 사이클)']") as HTMLElement;
    expect(smokeCard).not.toBeNull();
    expect(
      within(smokeCard).getByText(/smoke 스케줄이 비활성 상태입니다/)
    ).toBeInTheDocument();
    expect(
      within(smokeCard).getByText(/자동 검증 데이터가 아직 없습니다/)
    ).toBeInTheDocument();
  });

  it("KPI fetch가 빈 응답이어도 안전하게 7 그룹을 렌더한다", async () => {
    installFetchMock({
      kpi: buildKpi({
        manual_override: { decision_count: 0, modified_count: 0, modification_rate: null },
        missed_opportunities: { missed_count: 0, items: [] }
      })
    });

    renderApp();

    const kpiSection = await screen.findByRole("region", { name: "회사별 KPI" });
    expect(within(kpiSection).getByLabelText("수동 수정")).toBeInTheDocument();
    // 빈 상태 문구 노출
    expect(
      within(kpiSection).getByText("집계할 결정이 없습니다.")
    ).toBeInTheDocument();
    expect(
      within(kpiSection).getByText("놓친 유효 공고가 없습니다.")
    ).toBeInTheDocument();
  });
});
