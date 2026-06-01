import { describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { BreakdownView } from "./BreakdownView";
import type { SyntheticExperimentResultItem } from "@/shared/types/synthetic";

const results: SyntheticExperimentResultItem[] = [
  {
    operator_slug: "aggressive",
    metrics: { settled_count: 14 },
    breakdown: {
      by_category: [
        {
          category: "소프트웨어",
          settled_count: 8,
          would_have_won_count: 4,
          win_rate: 0.5,
          avg_abs_bid_rate_error: 0.03
        },
        {
          category: "건설",
          settled_count: 6,
          would_have_won_count: 0,
          win_rate: 0,
          avg_abs_bid_rate_error: null
        }
      ],
      by_budget_band: [
        {
          budget_band: "lt_1eok",
          settled_count: 5,
          would_have_won_count: 2,
          win_rate: 0.4,
          avg_abs_bid_rate_error: 0.02
        },
        {
          budget_band: "10eok_50eok",
          settled_count: 9,
          would_have_won_count: 3,
          win_rate: 0.3333,
          avg_abs_bid_rate_error: 0.04
        }
      ]
    }
  }
];

describe("BreakdownView", () => {
  it("renders category and budget-band rows with Korean band labels", () => {
    renderWithProviders(<BreakdownView results={results} />);

    // 카테고리
    expect(screen.getByText("소프트웨어")).toBeInTheDocument();
    expect(screen.getByText("건설")).toBeInTheDocument();

    // 예산구간 한글 라벨 매핑
    expect(screen.getByText("1억 미만")).toBeInTheDocument();
    expect(screen.getByText("10–50억")).toBeInTheDocument();

    // settled_count 표기
    const swRow = screen.getByLabelText("소프트웨어 분해");
    expect(within(swRow).getByText("8")).toBeInTheDocument();

    // null avg error는 "—"
    const constructionRow = screen.getByLabelText("건설 분해");
    expect(within(constructionRow).getByText("—")).toBeInTheDocument();
  });

  it("filters to a single operator when selected", () => {
    const multi: SyntheticExperimentResultItem[] = [
      ...results,
      {
        operator_slug: "conservative",
        metrics: { settled_count: 3 },
        breakdown: {
          by_category: [
            {
              category: "전기",
              settled_count: 3,
              would_have_won_count: 1,
              win_rate: 0.3333,
              avg_abs_bid_rate_error: 0.01
            }
          ],
          by_budget_band: []
        }
      }
    ];
    renderWithProviders(<BreakdownView results={multi} />);

    // 전체 집계에선 두 회사 카테고리 모두 노출
    expect(screen.getByText("전기")).toBeInTheDocument();
    expect(screen.getByText("소프트웨어")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("분해 대상 회사"), {
      target: { value: "conservative" }
    });

    // conservative만 선택 → 전기만 남고 소프트웨어는 사라짐
    expect(screen.getByText("전기")).toBeInTheDocument();
    expect(screen.queryByText("소프트웨어")).not.toBeInTheDocument();
  });

  it("shows an empty state when breakdown is empty", () => {
    const empty: SyntheticExperimentResultItem[] = [
      {
        operator_slug: "aggressive",
        metrics: { settled_count: 0 },
        breakdown: { by_category: [], by_budget_band: [] }
      }
    ];
    renderWithProviders(<BreakdownView results={empty} />);
    expect(screen.getByText(/분해할 정산 데이터가 없습니다/)).toBeInTheDocument();
  });
});
