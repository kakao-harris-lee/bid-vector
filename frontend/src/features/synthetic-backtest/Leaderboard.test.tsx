import { describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { Leaderboard } from "./Leaderboard";
import type { SyntheticExperimentResultItem } from "@/shared/types/synthetic";

const results: SyntheticExperimentResultItem[] = [
  {
    operator_slug: "conservative",
    metrics: {
      win_rate_on_settled: 0.4,
      bid_submission_rate: 0.9,
      average_absolute_bid_rate_error: 0.02,
      settled_count: 20
    }
  },
  {
    operator_slug: "aggressive",
    metrics: {
      win_rate_on_settled: 0.7,
      bid_submission_rate: 0.6,
      average_absolute_bid_rate_error: 0.05,
      settled_count: 12
    }
  },
  {
    operator_slug: "no-data",
    metrics: {
      win_rate_on_settled: null,
      bid_submission_rate: 0.1,
      average_absolute_bid_rate_error: null,
      settled_count: 0
    }
  }
];

function dataRowSlugs(): string[] {
  const rows = screen.getAllByRole("row");
  // 첫 행은 thead 헤더 → 제외
  return rows
    .slice(1)
    .map((row) => within(row).getAllByRole("cell")[1]?.textContent ?? "")
    .filter(Boolean);
}

describe("Leaderboard", () => {
  it("renders ranked rows with medals and price-only caveat", () => {
    renderWithProviders(<Leaderboard results={results} />);

    expect(screen.getByText(/가격 기준 추정 낙찰/)).toBeInTheDocument();
    // 기본 정렬(win_rate desc): aggressive(0.7) > conservative(0.4) > no-data(null)
    expect(dataRowSlugs()).toEqual(["aggressive", "conservative", "no-data"]);
    // 메달
    expect(screen.getByText("🥇 1위")).toBeInTheDocument();
    expect(screen.getByText("🥈 2위")).toBeInTheDocument();
    expect(screen.getByText("🥉 3위")).toBeInTheDocument();
    // null win_rate / null avg error 모두 "—"로 표기
    const noDataRow = screen.getByLabelText("no-data 랭킹 3위");
    expect(within(noDataRow).getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });

  it("re-sorts ascending when sorting by avg absolute error", () => {
    renderWithProviders(<Leaderboard results={results} />);

    fireEvent.change(screen.getByLabelText("리더보드 정렬 기준"), {
      target: { value: "average_absolute_bid_rate_error" }
    });

    // 오차 오름차순(작을수록 좋음): conservative(0.02) < aggressive(0.05) < no-data(null→뒤)
    expect(dataRowSlugs()).toEqual(["conservative", "aggressive", "no-data"]);
  });

  it("shows an empty state when there are no results", () => {
    renderWithProviders(<Leaderboard results={[]} />);
    expect(screen.getByText(/랭킹을 표시할 결과 데이터가 없습니다/)).toBeInTheDocument();
  });
});
