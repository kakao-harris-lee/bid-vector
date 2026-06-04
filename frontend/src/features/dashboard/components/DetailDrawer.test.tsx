import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
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
