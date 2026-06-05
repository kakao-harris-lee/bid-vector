import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
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

function buildFetchMock(profile: OperatorProfileResponse | null) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/v1/dashboard/summary")) return jsonResponse(summary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
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

  it("synthetic 운영자 선택 시 /operator/profile 호출이 발생하지 않고 5축 상세도 노출하지 않는다", async () => {
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
    const fetchMock = buildFetchMock(configuredProfile);
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    // 자격 상태 위젯 자체는 표시되지만 5축 grid 는 없다.
    expect(await screen.findByLabelText("내 자격 상태")).toBeInTheDocument();
    expect(screen.queryByLabelText("5축 자격 요약")).not.toBeInTheDocument();
    expect(await screen.findByText(/5축 상세는 본인 회사만 표시됩니다/)).toBeInTheDocument();

    // /operator/profile 은 다른 회사 컨텍스트에서 호출하지 않는다 (PR #70 한계).
    await waitFor(() => {
      const calls = fetchMock.mock.calls.filter(
        ([url]) => String(url) === "/api/v1/operator/profile"
      );
      expect(calls.length).toBe(0);
    });
  });
});
