import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReasonIndicators } from "./ReasonIndicators";

describe("ReasonIndicators", () => {
  it("renders both counts when strengths and risk flags exist", () => {
    render(<ReasonIndicators strengths={["a", "b", "c"]} riskFlags={["x"]} />);
    expect(screen.getByLabelText("추구 가능 근거 개수 3")).toHaveTextContent("3");
    expect(screen.getByLabelText("리스크 신호 개수 1")).toHaveTextContent("1");
  });

  it("renders only the strengths badge when there are no risks", () => {
    render(<ReasonIndicators strengths={["a", "b"]} riskFlags={[]} />);
    expect(screen.getByLabelText("추구 가능 근거 개수 2")).toBeInTheDocument();
    expect(screen.queryByLabelText(/리스크 신호 개수/)).not.toBeInTheDocument();
  });

  it("renders only the risk badge when there are no strengths", () => {
    render(<ReasonIndicators strengths={[]} riskFlags={["x", "y"]} />);
    expect(screen.getByLabelText("리스크 신호 개수 2")).toBeInTheDocument();
    expect(screen.queryByLabelText(/추구 가능 근거 개수/)).not.toBeInTheDocument();
  });

  it("renders nothing when both are empty or undefined", () => {
    const { container } = render(<ReasonIndicators strengths={undefined} riskFlags={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});
