import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { toastApi } from "@/shared/components/ui";
import { DetailDrawer } from "./DetailDrawer";
import type { DetailSelection } from "../types";
import type { DashboardOpportunityItem } from "@/shared/types";

const opportunity: DashboardOpportunityItem = {
  source: "decision",
  source_label: "판단",
  decision_record_id: 7,
  paper_bid_id: null,
  project: {
    project_id: 12,
    title: "공항 시설 유지보수",
    category: "시설관리",
    notice_number: "R26BK01",
    issuing_agency: "한국공항공사",
    demand_agency: "한국공항공사",
    budget_estimate: 500_000_000,
    deadline: "2026-07-01T09:00:00Z",
    status: "open"
  },
  action: "bid_now",
  decision_status: "planned",
  recommended_amount: 470_000_000,
  probability_score: 0.62,
  matched_score: 0.71,
  priority_score: 0.83,
  urgency_score: 0.4,
  deadline_hours_remaining: 48,
  reasoning: "면허·지역·예산 조건이 모두 충족되어 추구 가치가 높습니다.",
  strengths: ["면허 일치", "지역 적합"],
  risk_flags: ["경쟁 과열"],
  updated_at: "2026-06-01T00:00:00Z",
  detail_href: "/dashboard/projects/12"
};

const selection: DetailSelection = { kind: "opportunity", item: opportunity };

describe("DetailDrawer", () => {
  it("shows the decision reasons card with strengths, risks and verdict", () => {
    renderWithProviders(<DetailDrawer selection={selection} onClose={() => {}} username="operator" />);

    expect(within(screen.getByLabelText("왜 가능한가")).getByText("면허 일치")).toBeInTheDocument();
    expect(within(screen.getByLabelText("왜 위험한가")).getByText("경쟁 과열")).toBeInTheDocument();
    expect(screen.getByText("지금 투찰 검토 권장")).toBeInTheDocument();
  });

  it("keeps the full reasoning text accessible via the collapsible details", () => {
    renderWithProviders(<DetailDrawer selection={selection} onClose={() => {}} username="operator" />);

    expect(screen.getByText("판단 설명 전문 보기")).toBeInTheDocument();
    expect(
      screen.getByText("면허·지역·예산 조건이 모두 충족되어 추구 가치가 높습니다.")
    ).toBeInTheDocument();
  });

  it("renders nothing when there is no selection", () => {
    const { container } = renderWithProviders(
      <DetailDrawer selection={null} onClose={() => {}} username="operator" />
    );
    expect(container).toBeEmptyDOMElement();
  });
});

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    headers: { get: () => null }
  } as unknown as Response;
}

describe("DetailDrawer inline actions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    act(() => {
      toastApi.clearAll();
    });
  });

  it("session/operatorId가 주어지면 액션 버튼 3개를 노출하고 POST한다", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
      Promise.resolve(
        jsonResponse({
          id: 7,
          project_id: 12,
          operator_id: 11,
          pursue_bid: true,
          action: "review",
          decision_status: "reviewing",
          initial_action: "bid_now",
          initial_decision_status: "planned",
          first_decided_at: null,
          priority_score: 0.83,
          urgency_score: 0.4,
          competitiveness_score: 0.5,
          budget_capture_score: 0.5,
          expected_margin_score: 0.5,
          execution_complexity_score: 0.5,
          recommended_amount: 470_000_000,
          probability_score: 0.62,
          matched_score: 0.71,
          deadline_hours_remaining: 48,
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
      <DetailDrawer
        selection={selection}
        onClose={() => {}}
        username="operator"
        authToken="test-token"
        session={{ token: "test-token", username: "operator-1" } as never}
        activeOperatorId={11}
      />
    );

    const group = screen.getByRole("group", { name: "결정 액션" });
    const reviewBtn = within(group).getByRole("button", { name: "검토로 전환" });
    fireEvent.click(reviewBtn);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const actionCall = fetchMock.mock.calls.find(([url]) =>
      String(url).includes("/actions")
    );
    expect(actionCall).toBeDefined();
    expect(String(actionCall![0])).toBe(
      "/api/v1/operations/bid-decisions/7/actions?operator_id=11"
    );
    expect(
      JSON.parse(String((actionCall![1] as RequestInit).body))
    ).toEqual({ action: "review" });

    expect(await screen.findByText("검토 상태로 전환했습니다")).toBeInTheDocument();
  });

  it("session이 없으면 액션 버튼이 노출되지 않는다", () => {
    renderWithProviders(
      <DetailDrawer
        selection={selection}
        onClose={() => {}}
        username="operator"
      />
    );

    expect(screen.queryByRole("group", { name: "결정 액션" })).not.toBeInTheDocument();
  });
});
