import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  OnboardingApplyResponse,
  OnboardingSuggestionsResponse
} from "@/shared/api";
import type { ShellOutletContext } from "@/app/dashboardContext";
import { OnboardingWizard } from "./OnboardingWizard";

const session = { token: "token-onboarding", username: "operator" };

const suggestions: OnboardingSuggestionsResponse = {
  keywords: ["항만"],
  matched_notice_count: 12,
  diagnostics: "12건의 공고에서 후보를 도출했습니다.",
  profile: [
    {
      field: "business_type",
      value: "건설공사",
      source: "internal_notices",
      confidence: 0.8,
      needs_confirmation: true,
      reason: "12건 중 9건이 건설공사",
      matched_notice_count: 9
    },
    {
      field: "license_codes",
      value: ["항만공사업"],
      source: "internal_notices",
      confidence: 0.6,
      needs_confirmation: true,
      reason: "6건에서 면허 제한",
      matched_notice_count: 6
    }
  ],
  strategy: [
    {
      field: "min_budget_estimate",
      value: 50000000,
      source: "internal_notices",
      confidence: 0.5,
      needs_confirmation: true,
      reason: "매칭 공고 예산 중앙값",
      matched_notice_count: 12
    }
  ],
  current_operator_id: 1,
  current_operator_username: "operator"
};

const applyResponse: OnboardingApplyResponse = {
  applied: [{ field: "business_type", target: "profile", value: "건설공사" }],
  ignored: [{ field: "license_codes", reason: "확정하지 않음" }],
  current_operator_id: 1,
  current_operator_username: "operator"
};

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

function installFetchMock() {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/onboarding-suggestions/apply")) return jsonResponse(applyResponse);
    if (url.includes("/onboarding-suggestions")) return jsonResponse(suggestions);
    if (url.includes("/strategy/candidates")) {
      return jsonResponse({
        operator_id: 1,
        evaluated_project_count: 0,
        returned_candidate_count: 0,
        high_priority_only: false,
        candidates: []
      });
    }
    return jsonResponse({}, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWizard() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false }
    }
  });
  const context = { session } as unknown as ShellOutletContext;
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dashboard/onboarding"]}>
        <Routes>
          <Route element={<Outlet context={context} />}>
            <Route path="/dashboard/onboarding" element={<OnboardingWizard />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function submitSeed() {
  const user = userEvent.setup();
  const keywordInput = screen.getByLabelText("관심 키워드 (필수)");
  await user.type(keywordInput, "항만{Enter}");
  await user.click(screen.getByRole("button", { name: /후보 찾기/ }));
  return user;
}

function applyCalls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.filter(
    ([url, init]) =>
      String(url).includes("/onboarding-suggestions/apply") &&
      (init as RequestInit | undefined)?.method === "POST"
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("OnboardingWizard", () => {
  it("seed 제출 후 후보를 렌더하고 provenance(신뢰도·근거·사유)를 노출한다", async () => {
    installFetchMock();
    renderWizard();
    await submitSeed();

    expect(await screen.findByText("건설공사")).toBeInTheDocument();
    expect(screen.getByText("항만공사업")).toBeInTheDocument();
    // provenance는 숨기지 않는다(§2 정직 명세)
    expect(screen.getByText("12건 중 9건이 건설공사")).toBeInTheDocument();
    expect(screen.getAllByText("신뢰도").length).toBeGreaterThan(0);
    expect(screen.getByText(/12건의 공고에서 후보를 도출/)).toBeInTheDocument();
  });

  it("모든 후보를 확정 아님(draft) 상태로 표시하고, 확정 반영 버튼은 수락 전 비활성이다", async () => {
    installFetchMock();
    renderWizard();
    await submitSeed();

    // 3개 후보 모두 draft("추천 후보 · 확인 필요")로 시작 → 확정값처럼 보이지 않게
    const draftBadges = await screen.findAllByText("추천 후보 · 확인 필요");
    expect(draftBadges).toHaveLength(3);
    // 확정 아님을 명시
    expect(screen.getByText(/확정 아님/)).toBeInTheDocument();

    const applyButton = screen.getByRole("button", { name: /수락한 .*건 반영/ });
    expect(applyButton).toBeDisabled();
  });

  it("수락한 후보만 apply로 전송하고(거부/미확정 제외) 성공 시 반영/무시 결과를 보여준다", async () => {
    const fetchMock = installFetchMock();
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("건설공사");

    // business_type 수락, license_codes 거부, min_budget_estimate는 미확정으로 남김
    const businessCard = screen.getByRole("listitem", { name: "업무 구분 후보" });
    const licenseCard = screen.getByRole("listitem", { name: "보유 면허 코드 후보" });
    await user.click(within(businessCard).getByRole("button", { name: "수락" }));
    await user.click(within(licenseCard).getByRole("button", { name: "거부" }));

    // 수락은 1건만
    const applyButton = screen.getByRole("button", { name: /수락한 1건 반영/ });
    expect(applyButton).toBeEnabled();
    await user.click(applyButton);

    await waitFor(() => expect(applyCalls(fetchMock)).toHaveLength(1));
    const [, init] = applyCalls(fetchMock)[0]!;
    expect(JSON.parse(String(init?.body))).toEqual({
      decisions: [{ field: "business_type", value: "건설공사" }]
    });
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer token-onboarding"
    );

    // 결과 단계: 반영/무시 요약
    expect(await screen.findByText("반영된 필드 1건")).toBeInTheDocument();
    expect(screen.getByText("무시된 필드 1건")).toBeInTheDocument();
  });

  it("수정한 값으로 수락하면 편집값이 apply payload에 담긴다", async () => {
    const fetchMock = installFetchMock();
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("건설공사");
    const businessCard = screen.getByRole("listitem", { name: "업무 구분 후보" });
    await user.click(within(businessCard).getByRole("button", { name: /수정/ }));
    const editor = within(businessCard).getByLabelText("업무 구분 편집");
    await user.clear(editor);
    await user.type(editor, "전문건설");
    await user.click(within(businessCard).getByRole("button", { name: /수정 값 적용/ }));

    await user.click(screen.getByRole("button", { name: /수락한 1건 반영/ }));

    await waitFor(() => expect(applyCalls(fetchMock)).toHaveLength(1));
    const [, init] = applyCalls(fetchMock)[0]!;
    expect(JSON.parse(String(init?.body))).toEqual({
      decisions: [{ field: "business_type", value: "전문건설" }]
    });
  });
});
