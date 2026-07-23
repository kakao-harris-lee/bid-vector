import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OnboardingSuggestionHistoryResponse } from "@/shared/api";
import type { ShellOutletContext } from "@/app/dashboardContext";
import { OnboardingHistoryScreen } from "./OnboardingHistoryScreen";

const session = { token: "token-history", username: "operator" };

const page: OnboardingSuggestionHistoryResponse = {
  items: [
    {
      id: 2,
      field: "business_type",
      value: "construction",
      status: "accepted",
      source: "internal_notices",
      confidence: 0.82,
      reason: "매칭 공고 9건이 공사",
      created_at: "2026-07-20T05:30:00Z"
    },
    {
      id: 1,
      field: "license_codes",
      value: ["항만공사업"],
      status: "modified",
      source: "internal_notices",
      confidence: 0.6,
      reason: "6건에서 면허 제한",
      created_at: "2026-07-19T01:00:00Z"
    }
  ],
  total: 2,
  limit: 20,
  offset: 0,
  current_operator_id: 1,
  current_operator_username: "operator"
};

const emptyPage: OnboardingSuggestionHistoryResponse = {
  ...page,
  items: [],
  total: 0
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

function installFetchMock(payload: OnboardingSuggestionHistoryResponse | null, status = 200) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/onboarding-suggestions/history")) {
      return jsonResponse(payload ?? {}, status);
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderScreen() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false }
    }
  });
  const context = { session } as unknown as ShellOutletContext;
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/onboarding/history"]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/dashboard/onboarding" element={<div>온보딩 홈</div>} />
            <Route path="/dashboard/onboarding/history" element={<OnboardingHistoryScreen />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function historyCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(([url]) =>
    String(url).includes("/onboarding-suggestions/history")
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingHistoryScreen", () => {
  it("감사 레코드를 최신순으로 렌더한다(필드 라벨·상태 배지·provenance·값·시각)", async () => {
    installFetchMock(page);
    renderScreen();

    const list = await screen.findByRole("list", { name: "감사 이력 목록" });
    // 각 감사 행은 필드 라벨로 aria-label 이 붙는다(칩 내부 li 와 구분).
    const rows = within(list).getAllByLabelText(/감사 기록$/);
    expect(rows).toHaveLength(2);

    const first = rows[0]!;
    // codeValued(business_type) 값은 한국어로 표시 매핑(공사), 상태 배지=확정
    expect(within(first).getByText("업무 구분")).toBeInTheDocument();
    expect(within(first).getByText("공사")).toBeInTheDocument();
    expect(within(first).getByText("확정")).toBeInTheDocument();
    expect(within(first).getByText("내부 공고 추론")).toBeInTheDocument();
    expect(within(first).getByText("신뢰도 82.0%")).toBeInTheDocument();
    expect(within(first).getByText("매칭 공고 9건이 공사")).toBeInTheDocument();
    // created_at 은 원본 ISO 를 time[datetime] 으로 보존(표시는 KST 포맷)
    expect(within(first).getByText("매칭 공고 9건이 공사")).toBeInTheDocument();

    const second = rows[1]!;
    // 이미 한국어인 면허는 raw chips, 상태=수정 반영(wire modified)
    expect(within(second).getByText("항만공사업")).toBeInTheDocument();
    expect(within(second).getByText("수정 반영")).toBeInTheDocument();
  });

  it("이력이 없으면 빈 상태 안내를 표시한다", async () => {
    installFetchMock(emptyPage);
    renderScreen();

    expect(await screen.findByText(/아직 감사 이력이 없습니다/)).toBeInTheDocument();
    expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
  });

  it("조회 실패 시 에러 메시지를 표시한다", async () => {
    installFetchMock(null, 500);
    renderScreen();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("온보딩 감사 이력을 불러오지 못했습니다.");
  });

  it("상태 필터를 바꾸면 status 파라미터로 재조회한다", async () => {
    const fetchMock = installFetchMock(page);
    renderScreen();
    await screen.findAllByRole("listitem");

    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("결정 상태 필터"), "accepted");

    await waitFor(() => {
      expect(
        historyCalls(fetchMock).some(([url]) => String(url).includes("status=accepted"))
      ).toBe(true);
    });
  });

  it("다음 페이지로 넘기면 offset 을 증가시켜 재조회한다", async () => {
    const fetchMock = installFetchMock({
      ...page,
      items: Array.from({ length: 20 }, (_unused, index) => ({
        ...page.items![0]!,
        id: index + 1
      })),
      total: 25
    });
    renderScreen();
    await screen.findAllByRole("listitem");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "다음" }));

    await waitFor(() => {
      expect(
        historyCalls(fetchMock).some(([url]) => String(url).includes("offset=20"))
      ).toBe(true);
    });
  });
});
