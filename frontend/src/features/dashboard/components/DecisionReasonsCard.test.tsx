import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { DecisionReasonsCard } from "./DecisionReasonsCard";

describe("DecisionReasonsCard", () => {
  it("renders strengths and risk flags as lists", () => {
    renderWithProviders(
      <DecisionReasonsCard
        strengths={["면허 일치", "지역 적합"]}
        riskFlags={["경쟁 과열", "예산 초과 위험"]}
        action="bid_now"
        priorityScore={0.82}
        probabilityScore={0.61}
      />
    );

    const strengthsList = screen.getByLabelText("왜 가능한가");
    expect(within(strengthsList).getByText("면허 일치")).toBeInTheDocument();
    expect(within(strengthsList).getByText("지역 적합")).toBeInTheDocument();

    const risksList = screen.getByLabelText("왜 위험한가");
    expect(within(risksList).getByText("경쟁 과열")).toBeInTheDocument();
    expect(within(risksList).getByText("예산 초과 위험")).toBeInTheDocument();
  });

  it("shows muted empty copy when strengths and risks are absent", () => {
    renderWithProviders(
      <DecisionReasonsCard
        strengths={undefined}
        riskFlags={undefined}
        action="skip"
        priorityScore={0.1}
      />
    );

    expect(screen.getByText("표시할 강점이 없습니다.")).toBeInTheDocument();
    expect(screen.getByText("주요 리스크가 감지되지 않았습니다.")).toBeInTheDocument();
  });

  it("renders the bid_now verdict and action badge", () => {
    renderWithProviders(
      <DecisionReasonsCard strengths={[]} riskFlags={[]} action="bid_now" priorityScore={0.9} />
    );
    expect(screen.getByText("투찰")).toBeInTheDocument();
    expect(screen.getByText("지금 투찰 검토 권장")).toBeInTheDocument();
  });

  it("renders the review verdict", () => {
    renderWithProviders(
      <DecisionReasonsCard strengths={[]} riskFlags={[]} action="review" priorityScore={0.5} />
    );
    expect(screen.getByText("검토")).toBeInTheDocument();
    expect(screen.getByText("추가 검토 필요")).toBeInTheDocument();
  });

  it("renders the skip verdict", () => {
    renderWithProviders(
      <DecisionReasonsCard strengths={[]} riskFlags={[]} action="skip" priorityScore={0.2} />
    );
    expect(screen.getByText("보류")).toBeInTheDocument();
    expect(screen.getByText("현재 기준 보류 권장")).toBeInTheDocument();
  });

  it("renders the priority gauge as a percent and probability when supplied", () => {
    renderWithProviders(
      <DecisionReasonsCard
        strengths={[]}
        riskFlags={[]}
        action="review"
        priorityScore={0.74}
        probabilityScore={0.33}
      />
    );
    expect(screen.getByText("우선순위")).toBeInTheDocument();
    expect(screen.getByText("74%")).toBeInTheDocument();
    expect(screen.getByText("낙찰 확률")).toBeInTheDocument();
    expect(screen.getByText("33%")).toBeInTheDocument();
  });

  it("omits the probability gauge when probability is not provided", () => {
    renderWithProviders(
      <DecisionReasonsCard strengths={[]} riskFlags={[]} action="skip" priorityScore={0.4} />
    );
    expect(screen.queryByText("낙찰 확률")).not.toBeInTheDocument();
  });
});
