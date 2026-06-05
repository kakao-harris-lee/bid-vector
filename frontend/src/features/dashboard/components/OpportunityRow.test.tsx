import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import { OpportunityRow } from "./Rows";
import type { DashboardOpportunityItem } from "@/shared/types";

const decisionOpportunity: DashboardOpportunityItem = {
  source: "decision",
  source_label: "판단",
  decision_record_id: 42,
  paper_bid_id: null,
  project: {
    project_id: 100,
    title: "도로 보수공사",
    category: "토목",
    notice_number: "R26TX01",
    issuing_agency: "국토관리청",
    demand_agency: "국토관리청",
    budget_estimate: 250_000_000,
    deadline: "2026-07-15T09:00:00Z",
    status: "open"
  },
  action: "bid_now",
  decision_status: "planned",
  recommended_amount: 230_000_000,
  probability_score: 0.7,
  matched_score: 0.8,
  priority_score: 0.85,
  urgency_score: 0.5,
  deadline_hours_remaining: 72,
  reasoning: "면허 일치, 지역 적합, 예산 가용.",
  strengths: ["면허 일치", "지역 적합"],
  risk_flags: ["가격 압박"],
  updated_at: "2026-06-01T00:00:00Z",
  detail_href: "/dashboard/projects/100"
};

const paperOpportunity: DashboardOpportunityItem = {
  ...decisionOpportunity,
  source: "paper_bid",
  source_label: "페이퍼",
  decision_record_id: null,
  paper_bid_id: 555,
  project: { ...decisionOpportunity.project, project_id: 200, title: "전기 시설 점검" }
};

const session = { token: "test-token", username: "operator-1" } as const;

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response;
}

beforeEach(() => {
  vi.restoreAllMocks();
  act(() => {
    toastApi.clearAll();
  });
});

describe("OpportunityRow inline actions", () => {
  it("decision-source 행에는 투찰/검토/보류 버튼 3개가 나타난다", () => {
    renderWithProviders(
      <OpportunityRow
        item={decisionOpportunity}
        onSelect={() => {}}
        session={session}
        activeOperatorId={11}
      />
    );

    const group = screen.getByRole("group", { name: "결정 액션" });
    expect(within(group).getByRole("button", { name: "투찰로 전환" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "검토로 전환" })).toBeInTheDocument();
    expect(within(group).getByRole("button", { name: "보류 처리" })).toBeInTheDocument();
  });

  it("paper_bid-source 행에는 인라인 액션 버튼이 노출되지 않는다", () => {
    renderWithProviders(
      <OpportunityRow
        item={paperOpportunity}
        onSelect={() => {}}
        session={session}
        activeOperatorId={11}
      />
    );

    expect(screen.queryByRole("group", { name: "결정 액션" })).not.toBeInTheDocument();
  });

  it("투찰 버튼 클릭 시 정확한 URL/메서드/바디로 POST한다 (operator_id 포함)", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        jsonResponse({
          id: 42,
          project_id: 100,
          operator_id: 11,
          pursue_bid: true,
          action: "bid_now",
          decision_status: "submitted",
          initial_action: "bid_now",
          initial_decision_status: "planned",
          first_decided_at: null,
          priority_score: 0.85,
          urgency_score: 0.5,
          competitiveness_score: 0.5,
          budget_capture_score: 0.5,
          expected_margin_score: 0.5,
          execution_complexity_score: 0.5,
          recommended_amount: 230_000_000,
          probability_score: 0.7,
          matched_score: 0.8,
          deadline_hours_remaining: 72,
          current_active_bids: 0,
          max_active_bids: 5,
          current_workload_score: 0.0,
          workload_source: "auto",
          score_breakdown: {},
          strengths: [],
          risk_flags: [],
          reasoning: "",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z"
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <OpportunityRow
        item={decisionOpportunity}
        onSelect={() => {}}
        session={session}
        activeOperatorId={11}
      />
    );

    const submitButton = screen.getByRole("button", { name: "투찰로 전환" });
    fireEvent.click(submitButton);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [calledUrl, calledInit] = fetchMock.mock.calls[0];
    expect(String(calledUrl)).toBe(
      "/api/v1/operations/bid-decisions/42/actions?operator_id=11"
    );
    expect((calledInit as RequestInit).method).toBe("POST");
    expect(JSON.parse(String((calledInit as RequestInit).body))).toEqual({
      action: "submit"
    });
    expect((calledInit as RequestInit).headers).toMatchObject({
      Authorization: "Bearer test-token"
    });

    expect(await screen.findByText("투찰 결정으로 전환했습니다")).toBeInTheDocument();
  });

  it("operatorId가 null이면 URL에 operator_id 쿼리를 붙이지 않는다", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        jsonResponse({
          id: 42,
          project_id: 100,
          operator_id: 1,
          pursue_bid: false,
          action: "skip",
          decision_status: "skipped",
          initial_action: "skip",
          initial_decision_status: "planned",
          first_decided_at: null,
          priority_score: 0.85,
          urgency_score: 0.5,
          competitiveness_score: 0.5,
          budget_capture_score: 0.5,
          expected_margin_score: 0.5,
          execution_complexity_score: 0.5,
          recommended_amount: 230_000_000,
          probability_score: 0.7,
          matched_score: 0.8,
          deadline_hours_remaining: 72,
          current_active_bids: 0,
          max_active_bids: 5,
          current_workload_score: 0.0,
          workload_source: "auto",
          score_breakdown: {},
          strengths: [],
          risk_flags: [],
          reasoning: "",
          created_at: "2026-06-01T00:00:00Z",
          updated_at: "2026-06-01T00:00:00Z"
        })
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <OpportunityRow
        item={decisionOpportunity}
        onSelect={() => {}}
        session={session}
        activeOperatorId={null}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "보류 처리" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/v1/operations/bid-decisions/42/actions"
    );
    expect(
      JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))
    ).toEqual({ action: "skip" });
  });

  it("액션 버튼 클릭은 행 클릭(DetailDrawer 오픈)을 트리거하지 않는다", async () => {
    const fetchMock = vi.fn(
      (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Promise<Response>(() => {
          /* never resolve — we only need to observe stopPropagation */
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    const onSelect = vi.fn();

    renderWithProviders(
      <OpportunityRow
        item={decisionOpportunity}
        onSelect={onSelect}
        session={session}
        activeOperatorId={11}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "검토로 전환" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("404 응답은 한국어 에러 토스트를 띄운다", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(jsonResponse({ detail: "not found" }, 404))
    );
    vi.stubGlobal("fetch", fetchMock);

    renderWithProviders(
      <OpportunityRow
        item={decisionOpportunity}
        onSelect={() => {}}
        session={session}
        activeOperatorId={11}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "투찰로 전환" }));

    expect(await screen.findByText("결정 액션 실패")).toBeInTheDocument();
    expect(screen.getByText("결정 기록을 찾을 수 없습니다.")).toBeInTheDocument();
  });

  it("현재 decision_status에 매칭되는 버튼은 aria-pressed=true로 강조된다", () => {
    renderWithProviders(
      <OpportunityRow
        item={{ ...decisionOpportunity, decision_status: "submitted" }}
        onSelect={() => {}}
        session={session}
        activeOperatorId={11}
      />
    );

    expect(screen.getByRole("button", { name: "투찰로 전환" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.getByRole("button", { name: "검토로 전환" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
    expect(screen.getByRole("button", { name: "보류 처리" })).toHaveAttribute(
      "aria-pressed",
      "false"
    );
  });
});
