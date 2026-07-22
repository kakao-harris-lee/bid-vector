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
      // canonical english 코드 — 표시할 때 한국어(공사)로 매핑되어야 한다(ko 번들)
      field: "business_type",
      value: "construction",
      source: "internal_notices",
      confidence: 0.8,
      needs_confirmation: true,
      reason: "매칭 공고 12건 중 9건이 공사 카테고리",
      matched_notice_count: 9
    },
    {
      // 이미 한국어 — 매핑 대상 아님(raw 그대로 노출)
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
      // canonical category 코드 — service→용역, goods→물품 으로 매핑
      field: "focus_categories",
      value: ["service", "goods"],
      source: "internal_notices",
      confidence: 0.55,
      needs_confirmation: true,
      reason: "매칭 공고 카테고리 분포",
      matched_notice_count: 12
    },
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

const emptySuggestions: OnboardingSuggestionsResponse = {
  ...suggestions,
  matched_notice_count: 0,
  diagnostics: "조건에 맞는 공고가 없어 후보를 만들지 못했습니다.",
  profile: [],
  strategy: []
};

// cohort 정체성(협회 가입/기술부문) 후보 — license_codes 와 동일한 다중값 profile 후보.
// 값이 이미 한국어 명칭이라 raw chips 로 노출된다(codeValued 매핑 없음).
const cohortSuggestions: OnboardingSuggestionsResponse = {
  ...suggestions,
  profile: [
    {
      field: "tech_fields",
      value: ["수로측량업"],
      source: "internal_notices",
      confidence: 0.6,
      needs_confirmation: true,
      reason: "매칭 공고 7건에서 기술부문 추정",
      matched_notice_count: 7
    },
    {
      field: "association_memberships",
      value: ["한국수로측량협회"],
      source: "internal_notices",
      confidence: 0.5,
      needs_confirmation: true,
      reason: "매칭 공고 4건에서 협회 제한",
      matched_notice_count: 4
    }
  ],
  strategy: []
};

const applyResponse: OnboardingApplyResponse = {
  applied: [{ field: "business_type", target: "profile", value: "construction" }],
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

interface MockOptions {
  suggestionsPayload?: OnboardingSuggestionsResponse;
  suggestionsStatus?: number;
  applyStatus?: number;
}

function installFetchMock(opts: MockOptions = {}) {
  const {
    suggestionsPayload = suggestions,
    suggestionsStatus = 200,
    applyStatus = 200
  } = opts;
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/onboarding-suggestions/apply")) {
      return jsonResponse(applyResponse, applyStatus);
    }
    if (url.includes("/onboarding-suggestions")) {
      return jsonResponse(suggestionsPayload, suggestionsStatus);
    }
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

    // canonical 코드는 한국어로 매핑(공사/용역/물품), 이미 한국어인 면허는 raw 유지
    expect(await screen.findByText("공사")).toBeInTheDocument();
    expect(screen.getByText("용역")).toBeInTheDocument();
    expect(screen.getByText("물품")).toBeInTheDocument();
    expect(screen.getByText("항만공사업")).toBeInTheDocument();
    // provenance는 숨기지 않는다(§2 정직 명세)
    expect(screen.getByText("매칭 공고 12건 중 9건이 공사 카테고리")).toBeInTheDocument();
    expect(screen.getAllByText("신뢰도").length).toBeGreaterThan(0);
    // source는 데이터 구동 라벨(하드코딩 아님)
    expect(screen.getAllByText("내부 공고 추론").length).toBeGreaterThan(0);
    expect(screen.getByText(/12건의 공고에서 후보를 도출/)).toBeInTheDocument();
  });

  it("모든 후보를 확정 아님(draft) 상태로 표시하고, 확정 반영 버튼은 수락 전 비활성이다", async () => {
    installFetchMock();
    renderWizard();
    await submitSeed();

    // 4개 후보 모두 draft("추천 후보 · 확인 필요")로 시작 → 확정값처럼 보이지 않게
    const draftBadges = await screen.findAllByText("추천 후보 · 확인 필요");
    expect(draftBadges).toHaveLength(4);
    // 확정 아님을 명시
    expect(screen.getByText(/확정 아님/)).toBeInTheDocument();

    const applyButton = screen.getByRole("button", { name: /수락한 .*건 반영/ });
    expect(applyButton).toBeDisabled();
  });

  it("수락한 후보만 apply로 전송하고(거부/미확정 제외) 성공 시 반영/무시 결과를 보여준다", async () => {
    const fetchMock = installFetchMock();
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("공사");

    // business_type 수락, license_codes 거부, 나머지는 미확정으로 남김
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
    // 전송값은 canonical raw(표시 매핑이 payload를 오염시키지 않는다)
    expect(JSON.parse(String(init?.body))).toEqual({
      decisions: [{ field: "business_type", value: "construction" }]
    });
    expect((init?.headers as Record<string, string>).Authorization).toBe(
      "Bearer token-onboarding"
    );

    // 결과 단계: 반영/무시 요약 + 반영값도 한국어로 매핑
    expect(await screen.findByText("반영된 필드 1건")).toBeInTheDocument();
    expect(screen.getByText("무시된 필드 1건")).toBeInTheDocument();
    expect(screen.getByText("공사")).toBeInTheDocument();
  });

  it("수정한 값으로 수락하면 편집값이 apply payload에 담긴다", async () => {
    const fetchMock = installFetchMock();
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("공사");
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

  it("후보 조회 실패(GET 에러) 시 에러 메시지를 표시한다", async () => {
    installFetchMock({ suggestionsStatus: 500 });
    renderWizard();
    await submitSeed();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("온보딩 후보를 불러오지 못했습니다.");
  });

  it("후보가 비어 있으면(profile/strategy 빈 배열) 후보 없음 안내를 표시하고 반영 버튼을 비활성화한다", async () => {
    installFetchMock({ suggestionsPayload: emptySuggestions });
    renderWizard();
    await submitSeed();

    expect(await screen.findByText(/추천할 후보가 없습니다/)).toBeInTheDocument();
    expect(screen.getByText(/조건에 맞는 공고가 없어/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /수락한 .*건 반영/ })).toBeDisabled();
  });

  it("apply 실패 시 에러를 표시하고 결과 단계로 넘어가지 않는다", async () => {
    installFetchMock({ applyStatus: 500 });
    renderWizard();
    const user = await submitSeed();

    await screen.findByText("공사");
    const businessCard = screen.getByRole("listitem", { name: "업무 구분 후보" });
    await user.click(within(businessCard).getByRole("button", { name: "수락" }));
    await user.click(screen.getByRole("button", { name: /수락한 1건 반영/ }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("확정 반영에 실패했습니다.");
    // 결과 단계로 전이하지 않음 — 여전히 review(반영 버튼 존재)
    expect(screen.getByRole("button", { name: /수락한 .*건 반영/ })).toBeInTheDocument();
    expect(screen.queryByText("반영된 필드 1건")).not.toBeInTheDocument();
  });

  it("cohort 후보(기술부문/협회 가입)를 다른 profile 후보와 동일하게 draft로 렌더한다", async () => {
    installFetchMock({ suggestionsPayload: cohortSuggestions });
    renderWizard();
    await submitSeed();

    // FIELD_META 룩업이 raw 필드명 대신 한국어 라벨을 주고(카드 aria-label),
    // 값은 이미 한국어라 codeValued 매핑 없이 raw chips 로 노출된다.
    const techCard = await screen.findByRole("listitem", { name: "기술부문/전문분야 후보" });
    const assocCard = screen.getByRole("listitem", { name: "협회 가입 후보" });
    expect(within(techCard).getByText("수로측량업")).toBeInTheDocument();
    expect(within(assocCard).getByText("한국수로측량협회")).toBeInTheDocument();
    // 다른 후보와 동일하게 draft("추천 후보 · 확인 필요")로 시작(확정 아님, §2 정직 명세)
    expect(within(techCard).getByText("추천 후보 · 확인 필요")).toBeInTheDocument();
    expect(within(assocCard).getByText("추천 후보 · 확인 필요")).toBeInTheDocument();
  });

  it("수락한 cohort 후보(기술부문/협회 가입)를 apply decisions에 포함한다", async () => {
    const fetchMock = installFetchMock({ suggestionsPayload: cohortSuggestions });
    renderWizard();
    const user = await submitSeed();

    const techCard = await screen.findByRole("listitem", { name: "기술부문/전문분야 후보" });
    const assocCard = screen.getByRole("listitem", { name: "협회 가입 후보" });
    await user.click(within(techCard).getByRole("button", { name: "수락" }));
    await user.click(within(assocCard).getByRole("button", { name: "수락" }));

    await user.click(screen.getByRole("button", { name: /수락한 2건 반영/ }));

    await waitFor(() => expect(applyCalls(fetchMock)).toHaveLength(1));
    const [, init] = applyCalls(fetchMock)[0]!;
    // 다중값(문자열 리스트) 그대로 apply payload 에 담긴다(APPLY_FIELDS 화이트리스트 통과)
    expect(JSON.parse(String(init?.body))).toEqual({
      decisions: [
        { field: "tech_fields", value: ["수로측량업"] },
        { field: "association_memberships", value: ["한국수로측량협회"] }
      ]
    });
  });
});
