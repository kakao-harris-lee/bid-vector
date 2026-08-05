import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AmountWithBasis } from "@/shared/components";
import {
  AMOUNT_BASIS_BADGE,
  BID_BASE_FALLBACK_WARNING,
  BID_BASE_NOTE
} from "@/shared/constants/amountBasis";

describe("AmountWithBasis", () => {
  it("추정가격은 부가세 별도 표기 뱃지와 함께 표시한다", () => {
    render(<AmountWithBasis amount={100_000_000} basis="estimate" />);

    expect(screen.getByText("추정가격(부가세 별도 표기)")).toBeInTheDocument();
    expect(screen.getByText("₩100,000,000")).toBeInTheDocument();
    expect(screen.getByText(AMOUNT_BASIS_BADGE.estimate)).toBeInTheDocument();
  });

  it("투찰 기준금액은 부가세 포함 뱃지와 출처·비율을 함께 표시한다", () => {
    render(
      <AmountWithBasis
        amount={110_000_000}
        basis="bid_base"
        source="clean-base"
        ratio={1.1}
        note={BID_BASE_NOTE}
      />
    );

    expect(screen.getByText("투찰 기준금액(기초금액/사업금액)")).toBeInTheDocument();
    expect(screen.getByText(AMOUNT_BASIS_BADGE.bid_base)).toBeInTheDocument();
    expect(screen.getByText(/공고 기초금액\(신뢰\)/)).toBeInTheDocument();
    expect(screen.getByText(/추정가격 대비 1.100배/)).toBeInTheDocument();
    expect(screen.getByText(BID_BASE_NOTE)).toBeInTheDocument();
  });

  it("추천 투찰금액은 제출값이며 부가세는 과세 공고 조건으로만 단다", () => {
    render(<AmountWithBasis amount={98_500_000} basis="submission" variant="hero" />);

    expect(screen.getByText(AMOUNT_BASIS_BADGE.submission)).toBeInTheDocument();
    expect(screen.getByText("₩98,500,000")).toBeInTheDocument();
    // 면세 공고까지 부가세 포함이라 단정하면 안 된다.
    expect(screen.queryByText("부가세 포함 제출값")).not.toBeInTheDocument();
  });

  it("기초금액 미확보 폴백은 경고 문구를 노출한다", () => {
    render(
      <AmountWithBasis
        amount={100_000_000}
        basis="bid_base"
        source="budget-estimate-fallback"
      />
    );

    expect(screen.getByText(/기초금액 미확보 — 추정가격으로 대체/)).toBeInTheDocument();
    expect(screen.getByText(BID_BASE_FALLBACK_WARNING)).toBeInTheDocument();
  });

  it("알 수 없는 출처 값도 감추지 않고 그대로 보여준다", () => {
    render(<AmountWithBasis amount={1} basis="bid_base" source="brand-new-source" />);

    expect(screen.getByText(/brand-new-source/)).toBeInTheDocument();
  });

  it("inline 변형은 라벨 없이 금액과 뱃지만 낸다", () => {
    render(<AmountWithBasis amount={230_000_000} basis="submission" variant="inline" />);

    expect(screen.getByText("₩230,000,000")).toBeInTheDocument();
    expect(screen.getByText(AMOUNT_BASIS_BADGE.submission)).toBeInTheDocument();
    expect(screen.queryByText("투찰금액(제출값)")).not.toBeInTheDocument();
  });
});
