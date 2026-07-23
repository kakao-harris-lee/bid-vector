import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import type {
  OperatorProfileResponse,
  PaperBiddingRunExecutionResponse
} from "@/shared/types/profile";
import type { OperatorStrategyResponse } from "@/shared/types/strategy";
import type { DashboardSummaryResponse } from "@/shared/types";
import type { OperatorAccountListResponse } from "@/shared/api";
import { ACTIVE_OPERATOR_STORAGE_KEY } from "@/app/operatorContext";

const baseProfile: OperatorProfileResponse = {
  operator_id: 1,
  username: "operator",
  email: "op@example.com",
  full_name: "운영자",
  company: "테스트건설",
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  business_type: "공사",
  license_codes: ["전기공사업"],
  region_codes: ["서울"],
  tech_fields: ["수로측량업"],
  association_memberships: ["한국해양조사협회"],
  annual_revenue: 5_000_000_000,
  capacity_score: 0.6,
  construction_capacity_amount: 0,
  awarded_contract_limit: 0,
  total_awards: 12,
  profile_configured: true
};

const baseStrategy: OperatorStrategyResponse = {
  operator_id: 1,
  focus_categories: ["공사"],
  focus_regions: ["서울"],
  exclude_regions: [],
  required_keywords: [],
  exclude_keywords: [],
  min_budget_estimate: 0,
  max_budget_estimate: 0,
  minimum_match_score: 0.6,
  minimum_probability_score: 0.55,
  bid_now_threshold: 0.7,
  review_threshold: 0.5,
  auto_workload_penalty_multiplier: 1,
  category_priority_overrides: {},
  notify_only_high_priority: true,
  max_recommended_candidates: 10,
  strategy_configured: true
};

const baseBacktest: PaperBiddingRunExecutionResponse = {
  run_id: null,
  request: {},
  summary: {
    candidate_count: 4,
    paper_bid_count: 2,
    review_count: 1,
    skip_count: 1,
    action_counts: { bid_now: 2, review: 1, skip: 1 }
  },
  items: [
    {
      project_id: 101,
      project_title: "서울 청사 전기공사",
      category: "공사",
      action: "bid_now",
      matched_score: 0.82,
      reasoning: "필수 면허 코드를 모두 보유하고 있습니다."
    }
  ],
  settlements: []
};

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

function jsonResponse(payload: unknown, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload)
  } as Response);
}

interface RouteOverride {
  matcher: (url: string, init?: RequestInit) => boolean;
  handler: (init?: RequestInit) => Promise<Response>;
}

const accountsCatalogue: OperatorAccountListResponse = {
  current_operator_id: 1,
  current_operator_username: "operator",
  is_privileged: true,
  operator_count: 2,
  operators: [
    {
      operator_id: 1,
      username: "operator",
      full_name: "운영자",
      company: "테스트건설",
      business_type: "공사",
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
      business_type: "공사",
      is_canonical: false,
      is_synthetic: true,
      is_active: true,
      profile_configured: true
    }
  ]
};

function buildFetchMock({
  profile = baseProfile,
  strategy = baseStrategy,
  impersonatedProfile,
  impersonatedStrategy,
  accounts = accountsCatalogue,
  overrides = []
}: {
  profile?: OperatorProfileResponse;
  strategy?: OperatorStrategyResponse;
  impersonatedProfile?: OperatorProfileResponse;
  impersonatedStrategy?: OperatorStrategyResponse;
  accounts?: OperatorAccountListResponse;
  overrides?: RouteOverride[];
} = {}) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    for (const override of overrides) {
      if (override.matcher(url, init)) return override.handler(init);
    }
    if (url.endsWith("/api/v1/dashboard/summary")) return jsonResponse(emptySummary);
    if (url === "/api/v1/operator/accounts") return jsonResponse(accounts);
    if (url.startsWith("/api/v1/operator/profile?operator_id=") && impersonatedProfile) {
      return jsonResponse(impersonatedProfile);
    }
    if (url.startsWith("/api/v1/operator/strategy?operator_id=") && impersonatedStrategy) {
      return jsonResponse(impersonatedStrategy);
    }
    if (url.endsWith("/api/v1/operator/profile")) return jsonResponse(profile);
    if (url.endsWith("/api/v1/operator/strategy")) return jsonResponse(strategy);
    if (url.startsWith("/api/v1/operator/strategy/candidates")) {
      return jsonResponse({
        operator_id: 1,
        evaluated_project_count: 0,
        returned_candidate_count: 0,
        high_priority_only: false,
        candidates: []
      });
    }
    if (url.startsWith("/api/v1/operator/strategy/monitor/runs")) {
      return jsonResponse({ operator_id: 1, result_count: 0, runs: [] });
    }
    if (url.endsWith("/api/v1/backtests/paper-bidding/runs")) {
      return jsonResponse(baseBacktest);
    }
    return jsonResponse({}, 404);
  });
}

function findCall(
  fetchMock: ReturnType<typeof buildFetchMock>,
  predicate: (url: string, init?: RequestInit) => boolean
) {
  return fetchMock.mock.calls.find(([url, init]) =>
    predicate(String(url), init as RequestInit | undefined)
  );
}

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (!init?.body) return {};
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("bid-vector-dashboard-token", "token-profile");
  window.history.pushState({}, "", "/dashboard/profile");
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("CompanyInfoEditor", () => {
  it("profile + strategy를 로드해 화면에 표시한다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "업체 정보", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/profile", expect.anything())
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/strategy", expect.anything())
    );

    // Loaded chips are reflected in the form.
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "보유 면허에서 전기공사업 제거" })
      ).toBeInTheDocument();
    });
    expect((screen.getByLabelText("업무 구분") as HTMLSelectElement).value).toBe("공사");
  });

  it("cohort 필드(기술부문·협회 가입)를 GET 값으로 로드해 제거 가능한 칩으로 표시한다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "기술부문에서 수로측량업 제거" })
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "협회 가입에서 한국해양조사협회 제거" })
    ).toBeInTheDocument();
  });

  it("기술부문·협회 가입 칩을 추가하면 profile PUT body에 canonical raw 값으로 포함된다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "보유 면허에서 전기공사업 제거" })
      ).toBeInTheDocument();
    });

    // 값 그대로(표시 라벨↔payload 분리 없음)가 저장돼야 한다.
    await userEvent.type(screen.getByLabelText("기술부문"), "항만설계{Enter}");
    await userEvent.type(screen.getByLabelText("협회 가입"), "한국항만협회{Enter}");

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => {
      expect(
        findCall(
          fetchMock,
          (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
        )
      ).toBeDefined();
    });

    const profileCall = findCall(
      fetchMock,
      (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
    );
    const profileBody = parseBody(profileCall?.[1] as RequestInit);

    // GET 로드값 + 새로 입력한 값이 raw 문자열 배열로 합쳐져 전송된다.
    expect(profileBody).toHaveProperty("tech_fields", ["수로측량업", "항만설계"]);
    expect(profileBody).toHaveProperty("association_memberships", [
      "한국해양조사협회",
      "한국항만협회"
    ]);
  });

  it("공사 면허 추천 칩 클릭 시 보유 면허에 매칭 가능한 토큰이 추가된다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const addBtn = await screen.findByRole("button", {
      name: "보유 면허에 정보통신공사업 추가"
    });
    await userEvent.click(addBtn);

    // The chip now appears inside the ChipInput as a removable chip.
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "보유 면허에서 정보통신공사업 제거" })
      ).toBeInTheDocument()
    );
  });

  it("건설 면허 추천 칩(건축공사업)도 매칭 가능 칩으로 표시되어 클릭 시 토큰이 추가된다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const addBtn = await screen.findByRole("button", {
      name: "보유 면허에 건축공사업 추가"
    });
    // Construction licenses are now matchable: no ⚠ record-only marker.
    expect(addBtn).not.toHaveTextContent("⚠");

    await userEvent.click(addBtn);

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "보유 면허에서 건축공사업 제거" })
      ).toBeInTheDocument()
    );
  });

  it("모든 추천 면허가 매칭 가능해 ⚠ 기록용 안내 대신 전체 매칭 안내를 보여준다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByText("추천 면허는 모두 공고 요구 면허와 자동 매칭됩니다.")
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/표시 없는 면허만 공고 요구 면허와 자동 매칭/)
    ).not.toBeInTheDocument();
  });

  it("파일럿 준비도에서 면허·지역·금액·관심/제외 조건 보강 상태를 보여준다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });

    const checklist = await screen.findByLabelText("파일럿 준비도 체크리스트");
    expect(within(checklist).getByText("보유 면허")).toBeInTheDocument();
    expect(within(checklist).getByText("1개 면허가 후보 필터에 반영됩니다.")).toBeInTheDocument();
    expect(within(checklist).getByText("수행 지역")).toBeInTheDocument();
    expect(within(checklist).getByText("1개 지역이 매칭 범위로 쓰입니다.")).toBeInTheDocument();
    expect(within(checklist).getByText("시공능력평가액")).toBeInTheDocument();
    expect(
      within(checklist).getByText("0이면 미입력으로 처리되어 금액 검토 근거가 약해집니다.")
    ).toBeInTheDocument();
    expect(within(checklist).getByText("도급한도")).toBeInTheDocument();
    expect(
      within(checklist).getByText("도급한도 확인 전에는 상한 초과 후보를 별도 검토해야 합니다.")
    ).toBeInTheDocument();
    expect(within(checklist).getByText("관심 조건")).toBeInTheDocument();
    expect(within(checklist).getByText("2개 조건으로 후보를 좁힙니다.")).toBeInTheDocument();
    expect(within(checklist).getByText("제외 조건")).toBeInTheDocument();
  });

  it("저장 시 profile과 strategy를 각자 소유 필드로 호출하고 임계값은 보내지 않는다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    expect(
      await screen.findByRole("heading", { name: "업체 정보", level: 2 })
    ).toBeInTheDocument();

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "보유 면허에서 전기공사업 제거" })
      ).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => {
      expect(
        findCall(
          fetchMock,
          (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
        )
      ).toBeDefined();
    });

    const profileCall = findCall(
      fetchMock,
      (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
    );
    const strategyCall = findCall(
      fetchMock,
      (url, init) => url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT"
    );
    expect(strategyCall).toBeDefined();

    const profileBody = parseBody(profileCall?.[1] as RequestInit);
    const strategyBody = parseBody(strategyCall?.[1] as RequestInit);

    // Profile owns these.
    expect(profileBody).toHaveProperty("business_type", "공사");
    expect(profileBody).toHaveProperty("license_codes");
    expect(profileBody).toHaveProperty("region_codes");
    expect(profileBody).toHaveProperty("annual_revenue");
    expect(profileBody).toHaveProperty("capacity_score");
    expect(profileBody).toHaveProperty("construction_capacity_amount");
    expect(profileBody).toHaveProperty("awarded_contract_limit");
    expect(profileBody).toHaveProperty("total_awards");

    // Strategy gets only budget + preference fields (partial update).
    expect(strategyBody).toHaveProperty("min_budget_estimate");
    expect(strategyBody).toHaveProperty("max_budget_estimate");
    expect(strategyBody).toHaveProperty("focus_categories");
    expect(strategyBody).toHaveProperty("focus_regions");

    // Thresholds owned by StrategyEditor must NOT be touched.
    expect(strategyBody).not.toHaveProperty("minimum_match_score");
    expect(strategyBody).not.toHaveProperty("bid_now_threshold");
    expect(strategyBody).not.toHaveProperty("review_threshold");
    expect(strategyBody).not.toHaveProperty("auto_workload_penalty_multiplier");
    expect(strategyBody).not.toHaveProperty("max_recommended_candidates");
    expect(strategyBody).not.toHaveProperty("notify_only_high_priority");
    expect(strategyBody).not.toHaveProperty("category_priority_overrides");

    expect(await screen.findByText("업체 정보 저장 완료")).toBeInTheDocument();
  });

  it("시공능력평가액·도급한도 입력값이 profile PUT body에 포함된다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "보유 면허에서 전기공사업 제거" })
      ).toBeInTheDocument();
    });

    const capacityInput = screen.getByLabelText(
      "시공능력평가액 (원, 0=미입력)"
    ) as HTMLInputElement;
    const limitInput = screen.getByLabelText(
      "도급한도 (원, 0=미입력)"
    ) as HTMLInputElement;

    // Both fields default to 0 (미입력) when profile is fresh.
    expect(capacityInput.value).toBe("0");
    expect(limitInput.value).toBe("0");

    await userEvent.clear(capacityInput);
    await userEvent.type(capacityInput, "12000000000");
    await userEvent.clear(limitInput);
    await userEvent.type(limitInput, "8500000000");

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    await waitFor(() => {
      expect(
        findCall(
          fetchMock,
          (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
        )
      ).toBeDefined();
    });

    const profileCall = findCall(
      fetchMock,
      (url, init) => url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
    );
    const profileBody = parseBody(profileCall?.[1] as RequestInit);

    expect(profileBody).toHaveProperty("construction_capacity_amount", 12_000_000_000);
    expect(profileBody).toHaveProperty("awarded_contract_limit", 8_500_000_000);
  });

  it("두 mutation 중 strategy 저장이 실패하면 danger toast로 표시한다", async () => {
    const strategyPutFail: RouteOverride = {
      matcher: (url, init) =>
        url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT",
      handler: () => jsonResponse({ detail: "validation failed" }, 400)
    };
    const fetchMock = buildFetchMock({ overrides: [strategyPutFail] });
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });
    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: "보유 면허에서 전기공사업 제거" })
      ).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "저장" }));

    expect(await screen.findByText("업체 정보 저장 실패")).toBeInTheDocument();
  });

  it("'백테스트 실행' 클릭 시 paper-bidding run을 호출하고 결과를 렌더한다", async () => {
    const fetchMock = buildFetchMock();
    vi.stubGlobal("fetch", fetchMock);

    renderApp();

    const runBtn = await screen.findByRole("button", { name: "백테스트 실행" });
    await userEvent.click(runBtn);

    await waitFor(() => {
      expect(
        findCall(
          fetchMock,
          (url, init) =>
            url.endsWith("/api/v1/backtests/paper-bidding/runs") && init?.method === "POST"
        )
      ).toBeDefined();
    });

    // Run request defaults: scenario=base, limit=30, persist=false.
    const runCall = findCall(
      fetchMock,
      (url, init) =>
        url.endsWith("/api/v1/backtests/paper-bidding/runs") && init?.method === "POST"
    );
    const runBody = parseBody(runCall?.[1] as RequestInit);
    expect(runBody).toMatchObject({ scenario: "base", limit: 30, persist: false });

    expect(await screen.findByText("후보 4건")).toBeInTheDocument();
    expect(await screen.findByText("서울 청사 전기공사")).toBeInTheDocument();
  });

  it("/dashboard/profile 에서는 BottomNav 어떤 탭도 active로 강조되지 않는다", async () => {
    vi.stubGlobal("fetch", buildFetchMock());

    renderApp();

    await screen.findByRole("heading", { name: "업체 정보", level: 2 });

    const nav = screen.getByRole("navigation", { name: "대시보드 탭" });
    const activeButtons = Array.from(nav.querySelectorAll('[aria-current="page"]'));
    expect(activeButtons).toHaveLength(0);
  });

  describe("등록 마법사 (profile_configured=false)", () => {
    const freshProfile: OperatorProfileResponse = {
      ...baseProfile,
      license_codes: [],
      region_codes: [],
      profile_configured: false
    };

    it("profile_configured=false면 마법사 모드로 진입하고 첫 단계만 노출한다", async () => {
      vi.stubGlobal("fetch", buildFetchMock({ profile: freshProfile }));

      renderApp();

      // 첫 단계 카드(기본·면허·지역)만 보여야 한다.
      expect(
        await screen.findByRole("heading", { name: "기본 · 면허 · 지역" })
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "공사 능력 · 실적" })
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "공사 가능 금액" })
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "선호 · 제외 조건" })
      ).not.toBeInTheDocument();

      // 진행 라벨 + Next 버튼.
      expect(screen.getByText(/단계 1 \/ 4/)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "다음" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "저장" })).not.toBeInTheDocument();
    });

    it("다음/이전 버튼으로 단계가 진행·후퇴한다", async () => {
      vi.stubGlobal("fetch", buildFetchMock({ profile: freshProfile }));

      renderApp();

      await screen.findByRole("heading", { name: "기본 · 면허 · 지역" });

      // Step 1 → 2
      await userEvent.click(screen.getByRole("button", { name: "다음" }));
      expect(
        await screen.findByRole("heading", { name: "공사 능력 · 실적" })
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "기본 · 면허 · 지역" })
      ).not.toBeInTheDocument();
      expect(screen.getByText(/단계 2 \/ 4/)).toBeInTheDocument();

      // Step 2 → 3
      await userEvent.click(screen.getByRole("button", { name: "다음" }));
      expect(
        await screen.findByRole("heading", { name: "공사 가능 금액" })
      ).toBeInTheDocument();

      // Step 3 → 4 (마지막)
      await userEvent.click(screen.getByRole("button", { name: "다음" }));
      expect(
        await screen.findByRole("heading", { name: "선호 · 제외 조건" })
      ).toBeInTheDocument();
      // 마지막 단계는 "다음" 대신 "저장하고 마치기".
      expect(
        screen.queryByRole("button", { name: "다음" })
      ).not.toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "저장하고 마치기" })
      ).toBeInTheDocument();

      // 뒤로 돌아가기 (4 → 3)
      await userEvent.click(screen.getByRole("button", { name: "이전" }));
      expect(
        await screen.findByRole("heading", { name: "공사 가능 금액" })
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "선호 · 제외 조건" })
      ).not.toBeInTheDocument();
    });

    it("마지막 단계의 '저장하고 마치기' 클릭 시 profile + strategy PUT를 모두 호출한다", async () => {
      const fetchMock = buildFetchMock({ profile: freshProfile });
      vi.stubGlobal("fetch", fetchMock);

      renderApp();

      await screen.findByRole("heading", { name: "기본 · 면허 · 지역" });
      await userEvent.click(screen.getByRole("button", { name: "다음" })); // → capacity
      await userEvent.click(screen.getByRole("button", { name: "다음" })); // → budget
      await userEvent.click(screen.getByRole("button", { name: "다음" })); // → preferences

      await userEvent.click(
        screen.getByRole("button", { name: "저장하고 마치기" })
      );

      await waitFor(() => {
        expect(
          findCall(
            fetchMock,
            (url, init) =>
              url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
          )
        ).toBeDefined();
      });

      const profileCall = findCall(
        fetchMock,
        (url, init) =>
          url.endsWith("/api/v1/operator/profile") && init?.method === "PUT"
      );
      const strategyCall = findCall(
        fetchMock,
        (url, init) =>
          url.endsWith("/api/v1/operator/strategy") && init?.method === "PUT"
      );
      expect(strategyCall).toBeDefined();

      const profileBody = parseBody(profileCall?.[1] as RequestInit);
      const strategyBody = parseBody(strategyCall?.[1] as RequestInit);

      // 동일 저장 로직 재사용: profile은 profile 필드만, strategy는 임계값 제외 partial.
      expect(profileBody).toHaveProperty("business_type");
      expect(profileBody).toHaveProperty("license_codes");
      expect(profileBody).toHaveProperty("region_codes");
      expect(profileBody).toHaveProperty("annual_revenue");
      expect(profileBody).toHaveProperty("construction_capacity_amount");
      expect(profileBody).toHaveProperty("awarded_contract_limit");
      expect(strategyBody).toHaveProperty("min_budget_estimate");
      expect(strategyBody).toHaveProperty("max_budget_estimate");
      expect(strategyBody).toHaveProperty("focus_categories");
      expect(strategyBody).not.toHaveProperty("minimum_match_score");
      expect(strategyBody).not.toHaveProperty("bid_now_threshold");
      expect(strategyBody).not.toHaveProperty("review_threshold");
    });

    it("profile_configured=true면 마법사 미진입 — 모든 카드가 한 번에 보인다", async () => {
      vi.stubGlobal("fetch", buildFetchMock());

      renderApp();

      await screen.findByRole("heading", { name: "업체 정보", level: 2 });

      expect(
        screen.getByRole("heading", { name: "기본 · 면허 · 지역" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "공사 능력 · 실적" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "공사 가능 금액" })
      ).toBeInTheDocument();
      expect(
        screen.getByRole("heading", { name: "선호 · 제외 조건" })
      ).toBeInTheDocument();
      // single 모드 액션 버튼.
      expect(screen.getByRole("button", { name: "저장" })).toBeInTheDocument();
      // 단계 progress 라벨은 표시되지 않는다.
      expect(screen.queryByText(/단계 \d+ \/ \d+/)).not.toBeInTheDocument();
    });

    it("헤더 토글로 single ↔ 마법사 전환이 동작한다", async () => {
      vi.stubGlobal("fetch", buildFetchMock());

      renderApp();

      await screen.findByRole("heading", { name: "업체 정보", level: 2 });
      // 처음(profile_configured=true)에는 single 모드 → "단계별 가이드" 버튼.
      const toGuide = screen.getByRole("button", { name: "단계별 가이드" });
      await userEvent.click(toGuide);

      // 마법사 진입: 첫 단계만 노출.
      expect(
        await screen.findByText(/단계 1 \/ 4/)
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("heading", { name: "공사 능력 · 실적" })
      ).not.toBeInTheDocument();

      // 다시 single 모드로.
      await userEvent.click(screen.getByRole("button", { name: "전체 보기" }));
      expect(
        await screen.findByRole("heading", { name: "공사 능력 · 실적" })
      ).toBeInTheDocument();
      expect(screen.queryByText(/단계 \d+ \/ \d+/)).not.toBeInTheDocument();
    });
  });

  describe("사용자 surface active operator boundary", () => {
    const syntheticProfile: OperatorProfileResponse = {
      ...baseProfile,
      operator_id: 11,
      username: "synthetic-aggressive",
      company: "Synthetic A"
    };
    const syntheticStrategy: OperatorStrategyResponse = {
      ...baseStrategy,
      operator_id: 11
    };

    it("저장된 active operator id가 있어도 사용자 profile 편집은 본인 URL만 호출한다", async () => {
      window.localStorage.setItem(ACTIVE_OPERATOR_STORAGE_KEY, "11");
      const fetchMock = buildFetchMock({
        impersonatedProfile: syntheticProfile,
        impersonatedStrategy: syntheticStrategy
      });
      vi.stubGlobal("fetch", fetchMock);

      renderApp();

      await screen.findByRole("heading", { name: "업체 정보", level: 2 });

      // User surface는 Shell에서 activeOperatorId를 null로 마스킹한다.
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/profile", expect.anything());
      });
      await waitFor(() => {
        expect(fetchMock).toHaveBeenCalledWith("/api/v1/operator/strategy", expect.anything());
      });
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url) === "/api/v1/operator/profile?operator_id=11"
        )
      ).toBe(false);
      expect(
        fetchMock.mock.calls.some(
          ([url]) => String(url) === "/api/v1/operator/strategy?operator_id=11"
        )
      ).toBe(false);

      // 사용자 surface에서는 본인 회사 편집 흐름으로 유지된다.
      expect(screen.queryByTestId("profile-readonly-notice")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "저장" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "단계별 가이드" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "저장하고 마치기" })).not.toBeInTheDocument();

      const select = screen.getByLabelText("업무 구분") as HTMLSelectElement;
      expect(select).toBeEnabled();
    });

    it("본인 컨텍스트로 돌아가면 저장 버튼이 다시 노출된다", async () => {
      const fetchMock = buildFetchMock();
      vi.stubGlobal("fetch", fetchMock);

      renderApp();

      await screen.findByRole("heading", { name: "업체 정보", level: 2 });
      expect(await screen.findByRole("button", { name: "저장" })).toBeInTheDocument();
      expect(screen.queryByTestId("profile-readonly-notice")).not.toBeInTheDocument();
    });
  });
});
