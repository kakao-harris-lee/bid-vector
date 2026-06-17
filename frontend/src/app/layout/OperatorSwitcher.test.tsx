import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { OperatorAccountListResponse } from "@/shared/api";
import { ACTIVE_OPERATOR_STORAGE_KEY } from "@/app/operatorContext";

const TOKEN_KEY = "bid-vector-dashboard-token";

const summary: DashboardSummaryResponse = {
  operator_id: 1,
  generated_at: "2026-06-01T00:00:00Z",
  today: "2026-06-01",
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

const privilegedAccounts: OperatorAccountListResponse = {
  current_operator_id: 1,
  current_operator_username: "operator",
  is_privileged: true,
  operator_count: 3,
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
    },
    {
      operator_id: 12,
      username: "synthetic-conservative",
      full_name: "보수형",
      company: "Synthetic B",
      business_type: "정보통신",
      is_canonical: false,
      is_synthetic: true,
      is_active: true,
      profile_configured: false
    }
  ]
};

const nonPrivilegedAccounts: OperatorAccountListResponse = {
  current_operator_id: 7,
  current_operator_username: "regular-user",
  is_privileged: false,
  operator_count: 1,
  operators: [
    {
      operator_id: 7,
      username: "regular-user",
      full_name: "단일 사용자",
      company: "사용자 회사",
      business_type: null,
      is_canonical: false,
      is_synthetic: false,
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

function buildFetchMock(accounts: OperatorAccountListResponse) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith("/api/v1/dashboard/summary")) return jsonResponse(summary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
    if (url.startsWith("/api/v1/dashboard/")) {
      return jsonResponse({
        operator_id: 1,
        current_operator_id: 1,
        current_operator_username: "operator",
        generated_at: summary.generated_at,
        returned_count: 0,
        limit: 50,
        items: []
      });
    }
    return jsonResponse({}, 404);
  });
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem(TOKEN_KEY, "token-switcher");
  window.history.pushState({}, "", "/dashboard");
  vi.restoreAllMocks();
});

describe("OperatorSwitcher", () => {
  it("권한이 있는 사용자에게 회사 목록 드롭다운을 노출하고 그룹으로 정렬한다", async () => {
    window.history.pushState({}, "", "/admin/operations");
    vi.stubGlobal("fetch", buildFetchMock(privilegedAccounts));
    renderApp();

    const trigger = await screen.findByRole("button", { name: "회사 전환" });
    expect(trigger).toBeInTheDocument();

    await userEvent.click(trigger);
    const listbox = await screen.findByRole("listbox", { name: "회사 선택" });
    expect(within(listbox).getByText("기본 (본인)")).toBeInTheDocument();
    expect(within(listbox).getByText("본사")).toBeInTheDocument();
    expect(within(listbox).getByText("Synthetic A")).toBeInTheDocument();
    expect(within(listbox).getByText("Synthetic B")).toBeInTheDocument();
  });

  it("권한이 없는 사용자에게는 드롭다운을 렌더링하지 않는다", async () => {
    vi.stubGlobal("fetch", buildFetchMock(nonPrivilegedAccounts));
    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "회사 전환" })).not.toBeInTheDocument();
  });

  it("회사 선택 시 activeOperatorId가 localStorage에 영속되고 dashboard 호출에 operator_id가 전달된다", async () => {
    window.history.pushState({}, "", "/admin/operations");
    const fetchMock = buildFetchMock(privilegedAccounts);
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    const trigger = await screen.findByRole("button", { name: "회사 전환" });
    await userEvent.click(trigger);
    const optionA = await screen.findByRole("option", { name: /Synthetic A/ });
    await userEvent.click(optionA);

    expect(window.localStorage.getItem(ACTIVE_OPERATOR_STORAGE_KEY)).toBe("11");

    await waitFor(() => {
      const matched = fetchMock.mock.calls.find(([url]) =>
        String(url).startsWith("/api/v1/dashboard/summary?operator_id=11")
      );
      expect(matched).toBeDefined();
    });
  });

  it("페이지 새로고침 후에도 activeOperatorId가 유지된다 (localStorage rehydration)", async () => {
    window.history.pushState({}, "", "/admin/operations");
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "12");
    const fetchMock = buildFetchMock(privilegedAccounts);
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    await waitFor(() => {
      const matched = fetchMock.mock.calls.find(([url]) =>
        String(url).startsWith("/api/v1/dashboard/summary?operator_id=12")
      );
      expect(matched).toBeDefined();
    });

    // 드롭다운 트리거가 선택된 회사를 보여주는지 확인.
    const trigger = await screen.findByRole("button", { name: "회사 전환" });
    expect(trigger).toHaveTextContent("Synthetic B");
  });

  it("권한이 없는 사용자는 localStorage에 남아 있는 다른 회사 id를 무시하고 자동 리셋한다", async () => {
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
    vi.stubGlobal("fetch", buildFetchMock(nonPrivilegedAccounts));
    renderApp();

    // 권한 없는 사용자 → /operator/accounts 응답 수신 후 localStorage가 자동 리셋되어야 한다.
    await waitFor(() => {
      expect(window.localStorage.getItem(ACTIVE_OPERATOR_STORAGE_KEY)).toBeNull();
    });
    // 드롭다운은 표시되지 않는다.
    expect(screen.queryByRole("button", { name: "회사 전환" })).not.toBeInTheDocument();
  });

  it("기본(본인)을 다시 선택하면 operator_id 쿼리 파라미터가 제거된다", async () => {
    window.history.pushState({}, "", "/admin/operations");
    window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
    const fetchMock = buildFetchMock(privilegedAccounts);
    vi.stubGlobal("fetch", fetchMock);
    renderApp();

    // 초기 진입 시 ?operator_id=11 호출.
    await waitFor(() => {
      const matched = fetchMock.mock.calls.find(([url]) =>
        String(url).startsWith("/api/v1/dashboard/summary?operator_id=11")
      );
      expect(matched).toBeDefined();
    });

    const trigger = await screen.findByRole("button", { name: "회사 전환" });
    await userEvent.click(trigger);
    const defaultOption = await screen.findByRole("option", { name: /기본 \(본인\)/ });
    await act(async () => {
      await userEvent.click(defaultOption);
    });

    expect(window.localStorage.getItem(ACTIVE_OPERATOR_STORAGE_KEY)).toBeNull();
    await waitFor(() => {
      const matched = fetchMock.mock.calls.find(
        ([url]) => String(url) === "/api/v1/dashboard/summary"
      );
      expect(matched).toBeDefined();
    });
  });

  it("권한이 있어도 사용자 dashboard surface에서는 회사 전환을 숨긴다", async () => {
    vi.stubGlobal("fetch", buildFetchMock(privilegedAccounts));
    renderApp();

    expect(await screen.findByRole("heading", { name: "오늘 할 일" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "회사 전환" })).not.toBeInTheDocument();
  });
});
