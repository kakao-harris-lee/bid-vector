import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderApp } from "@/test-utils";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { OperatorAccountListResponse } from "@/shared/api";
import type { OperatorProfileResponse } from "@/shared/types/profile";
import { ACTIVE_OPERATOR_STORAGE_KEY } from "@/app/operatorContext";

const TOKEN_KEY = "bid-vector-dashboard-token";

const summary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-06-03T00:00:00Z",
  today: "2026-06-03",
  current_operator_id: 1,
  current_operator_username: "operator",
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

const accounts: OperatorAccountListResponse = {
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
      profile_configured: false
    }
  ]
};

const configuredProfile: OperatorProfileResponse = {
  operator_id: 1,
  username: "operator",
  email: "operator@example.com",
  full_name: "본사 운영자",
  company: "본사",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  business_type: "정보통신",
  license_codes: ["정보통신공사업", "전기공사업"],
  region_codes: ["서울", "경기"],
  tech_fields: [],
  association_memberships: [],
  annual_revenue: 3_000_000_000,
  capacity_score: 0.65,
  construction_capacity_amount: 8_000_000_000,
  awarded_contract_limit: 15_000_000_000,
  total_awards: 12,
  profile_configured: true
};

const unconfiguredProfile: OperatorProfileResponse = {
  ...configuredProfile,
  license_codes: [],
  region_codes: [],
  annual_revenue: 0,
  construction_capacity_amount: 0,
  awarded_contract_limit: 0,
  total_awards: 0,
  profile_configured: false
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response);
}

function buildFetchMock(
  profile: OperatorProfileResponse | null,
  impersonatedProfile?: OperatorProfileResponse,
  dashboardSummary: DashboardSummaryResponse = summary
) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/v1/dashboard/summary")) return jsonResponse(dashboardSummary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
    // `?operator_id=<n>` → impersonated GET (cross-operator). PR #74.
    if (url.startsWith("/api/v1/operator/profile?operator_id=")) {
      if (impersonatedProfile) return jsonResponse(impersonatedProfile);
      return jsonResponse({ detail: "not found" }, 404);
    }
    if (url === "/api/v1/operator/profile") {
      if (profile) return jsonResponse(profile);
      return jsonResponse({ detail: "not found" }, 404);
    }
    return jsonResponse({}, 404);
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem(TOKEN_KEY, "token-home");
  window.history.pushState({}, "", "/dashboard");
  vi.restoreAllMocks();
});

describe("HomeScreen + ProfileStatusWidget integration", () => {
  it("본인 컨텍스트에서 /operator/profile 을 1회 호출하고 5축 상세를 표시한다", async () => {
    const fetchMock = buildFetchMock(configuredProfile);
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();

    // Widget is mounted with the 자격 상태 aria-label.
    const widget = await screen.findByLabelText("내 자격 상태");
    expect(widget).toBeInTheDocument();

    // 5축 cells render once the profile loads.
    expect(await screen.findByLabelText("5축 자격 요약")).toBeInTheDocument();

    // Profile fetched exactly once for the canonical context.
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        ([url]) => String(url) === "/api/v1/operator/profile"
      );
      expect(calls.length).toBe(1);
    });
  });

  it("미입력자(profile_configured=false)는 마법사 CTA를 클릭하면 /dashboard/profile 로 이동한다", async () => {
    const fetchMock = buildFetchMock(unconfiguredProfile);
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    const cta = await screen.findByRole("button", { name: /단계별 입력 시작/ });
    await userEvent.click(cta);

    expect(window.location.pathname).toBe("/dashboard/profile");
  });

  it("사용자 dashboard surface는 저장된 synthetic 운영자 컨텍스트를 조회에 사용하지 않는다", async () => {
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
    const impersonated: OperatorProfileResponse = {
      ...configuredProfile,
      operator_id: 11,
      username: "synthetic-aggressive",
      company: "Synthetic A"
    };
    const fetchMock = buildFetchMock(configuredProfile, impersonated);
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(await screen.findByLabelText("내 자격 상태")).toBeInTheDocument();
    expect(await screen.findByLabelText("5축 자격 요약")).toBeInTheDocument();
    const widget = screen.getByLabelText("내 자격 상태");
    expect(within(widget).getByRole("button", { name: /편집/ })).toBeInTheDocument();
    expect(
      within(widget).queryByRole("button", { name: /단계별 입력 시작/ })
    ).not.toBeInTheDocument();

    // 사용자 surface에서는 cross-operator 조회가 admin 화면으로만 제한된다.
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        ([url]) => String(url) === "/api/v1/operator/profile"
      );
      expect(calls.length).toBe(1);
    });
    const impersonatedCalls = fetchMock.mock.calls.filter(
      ([url]) => String(url) === "/api/v1/operator/profile?operator_id=11"
    );
    expect(impersonatedCalls.length).toBe(0);
    expect(
      fetchMock.mock.calls.some(
        ([url]) => String(url) === "/api/v1/dashboard/summary?operator_id=11"
      )
    ).toBe(false);
  });

  it("사용자 dashboard에서 관리자/백테스트성 지표를 숨긴다", async () => {
    const fetchMock = buildFetchMock(configuredProfile, undefined, {
      ...summary,
      metrics: [
        {
          key: "due_opportunities",
          label: "오늘 마감",
          value: 2,
          unit: "count",
          status: "watch",
          detail: "사용자 투찰 판단 지표"
        },
        {
          key: "paper_backtest",
          label: "페이퍼 검증",
          value: 12,
          unit: "count",
          status: "info",
          detail: "관리자용 백테스트 지표"
        },
        {
          key: "smoke_success_rate",
          label: "스모크 통과율",
          value: 0.9,
          unit: "ratio",
          status: "healthy",
          detail: "관리자용 smoke 지표"
        }
      ]
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(screen.getByText("오늘 마감")).toBeInTheDocument();
    expect(screen.queryByText("페이퍼 검증")).not.toBeInTheDocument();
    expect(screen.queryByText("스모크 통과율")).not.toBeInTheDocument();
  });
});
