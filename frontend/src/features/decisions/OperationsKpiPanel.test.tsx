import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "@/test-utils";
import { OperationsKpiPanel } from "./OperationsKpiPanel";
import type { OperationsKpiResponse } from "@/shared/types/operations";

const fullData: OperationsKpiResponse = {
  operator_id: 1,
  period_days: 30,
  manual_override: {
    decision_count: 20,
    modified_count: 5,
    modification_rate: 0.25
  },
  conversion: {
    decision_count: 20,
    submitted_count: 12,
    overall_submission_rate: 0.6,
    bid_now_submission_rate: 0.75,
    review_submission_rate: 0.4,
    average_hours_to_submit: 18.5
  },
  prediction_accuracy: {
    result_count: 8,
    prediction_sample_count: 8,
    recommendation_sample_count: 7,
    average_prediction_error_rate: 0.021,
    average_recommendation_error_rate: 0.034,
    prediction_within_1_percent_count: 3,
    prediction_within_3_percent_count: 6,
    recommendation_within_1_percent_count: 2,
    recommendation_within_3_percent_count: 5
  },
  missed_opportunities: {
    missed_count: 2,
    items: [
      {
        decision_record_id: 501,
        project_id: 42,
        project_title: "지나친 유효 공고 A",
        deadline: "2026-05-20T09:00:00Z",
        initial_action: "bid_now",
        decision_status: "planned",
        priority_score: 0.82
      },
      {
        decision_record_id: 502,
        project_id: 43,
        project_title: "지나친 유효 공고 B",
        deadline: null,
        initial_action: "review",
        decision_status: "reviewing",
        priority_score: 0.55
      }
    ]
  },
  review_time: {
    average_review_minutes: 12.4,
    sample_count: 7
  },
  recommendation_feedback: {
    useful_count: 9,
    not_useful_count: 3,
    review_value_rate: 0.75,
    feedback_count: 12
  },
  settlement_coverage: {
    total_paper_bids: 40,
    settled_count: 26,
    coverage_rate: 0.65,
    forward_paper_bids: 10,
    forward_settled_count: 9,
    forward_coverage_rate: 0.9
  }
};

describe("OperationsKpiPanel", () => {
  it("7개 KPI 그룹을 정상 값과 함께 렌더한다", () => {
    renderWithProviders(<OperationsKpiPanel data={fullData} loading={false} error={null} />);

    expect(screen.getByRole("heading", { name: /운영 KPI/ })).toBeInTheDocument();

    // 7 KPI groups present (aria-label on each article)
    const manual = screen.getByLabelText("수동 수정");
    const conversion = screen.getByLabelText("투찰 전환율");
    const accuracy = screen.getByLabelText("예측 정확도");
    const reviewTime = screen.getByLabelText("검토 시간");
    const feedback = screen.getByLabelText("추천 유용성");
    const missed = screen.getByLabelText("놓친 유효 공고");
    const settlement = screen.getByLabelText("정산 커버리지");
    expect(manual).toBeInTheDocument();
    expect(conversion).toBeInTheDocument();
    expect(accuracy).toBeInTheDocument();
    expect(reviewTime).toBeInTheDocument();
    expect(feedback).toBeInTheDocument();
    expect(missed).toBeInTheDocument();
    expect(settlement).toBeInTheDocument();

    // settlement coverage: forward rate emphasised + counts, overall rate + counts
    expect(within(settlement).getByText("90.0%")).toBeInTheDocument();
    expect(within(settlement).getByText("forward 10건 중 9건 정산")).toBeInTheDocument();
    expect(within(settlement).getByText("65.0%")).toBeInTheDocument();
    expect(within(settlement).getByText("전체 40건 중 26건")).toBeInTheDocument();

    // review time: average minutes + sample count
    expect(within(reviewTime).getByText("평균 12.4분")).toBeInTheDocument();
    expect(within(reviewTime).getByText("표본 7건")).toBeInTheDocument();

    // recommendation feedback: rate % + counts
    expect(within(feedback).getByText("75.0%")).toBeInTheDocument();
    expect(within(feedback).getByText("👍 9")).toBeInTheDocument();
    expect(within(feedback).getByText("👎 3")).toBeInTheDocument();
    expect(within(feedback).getByText("피드백 12건")).toBeInTheDocument();

    // manual override: rate as % + counts
    expect(within(manual).getByText("25.0%")).toBeInTheDocument();
    expect(within(manual).getByText(/결정 20건 중 5건 수정/)).toBeInTheDocument();

    // conversion: % rates + average hours
    expect(within(conversion).getByText("60.0%")).toBeInTheDocument();
    expect(within(conversion).getByText("75.0%")).toBeInTheDocument();
    expect(within(conversion).getByText("40.0%")).toBeInTheDocument();
    expect(within(conversion).getByText("평균 18.5시간")).toBeInTheDocument();

    // prediction accuracy: error rates as %
    expect(within(accuracy).getByText("2.1%")).toBeInTheDocument();
    expect(within(accuracy).getByText("3.4%")).toBeInTheDocument();
  });

  it("rate가 null이면 대시(—)로 표시한다", () => {
    const data: OperationsKpiResponse = {
      ...fullData,
      manual_override: { decision_count: 0, modified_count: 0, modification_rate: null },
      conversion: {
        decision_count: 0,
        submitted_count: 0,
        overall_submission_rate: null,
        bid_now_submission_rate: null,
        review_submission_rate: null,
        average_hours_to_submit: null
      },
      prediction_accuracy: {
        result_count: 0,
        prediction_sample_count: 0,
        recommendation_sample_count: 0,
        average_prediction_error_rate: null,
        average_recommendation_error_rate: null,
        prediction_within_1_percent_count: 0,
        prediction_within_3_percent_count: 0,
        recommendation_within_1_percent_count: 0,
        recommendation_within_3_percent_count: 0
      },
      review_time: { average_review_minutes: null, sample_count: 0 },
      recommendation_feedback: {
        useful_count: 0,
        not_useful_count: 0,
        review_value_rate: null,
        feedback_count: 0
      },
      settlement_coverage: {
        total_paper_bids: 0,
        settled_count: 0,
        coverage_rate: null,
        forward_paper_bids: 0,
        forward_settled_count: 0,
        forward_coverage_rate: null
      }
    };
    renderWithProviders(<OperationsKpiPanel data={data} loading={false} error={null} />);

    const manual = screen.getByLabelText("수동 수정");
    // modification rate dash + empty-state note
    expect(within(manual).getByText("—")).toBeInTheDocument();
    expect(within(manual).getByText("집계할 결정이 없습니다.")).toBeInTheDocument();

    const accuracy = screen.getByLabelText("예측 정확도");
    expect(within(accuracy).getByText("정확도 표본이 아직 없습니다.")).toBeInTheDocument();

    const reviewTime = screen.getByLabelText("검토 시간");
    expect(within(reviewTime).getByText("—")).toBeInTheDocument();
    expect(within(reviewTime).getByText("표본 0건")).toBeInTheDocument();
    expect(within(reviewTime).getByText("측정 표본이 아직 없습니다.")).toBeInTheDocument();

    const feedback = screen.getByLabelText("추천 유용성");
    expect(within(feedback).getByText("—")).toBeInTheDocument();
    expect(within(feedback).getByText("아직 피드백이 없습니다.")).toBeInTheDocument();

    const settlement = screen.getByLabelText("정산 커버리지");
    // both forward & overall coverage rates are null → dash
    expect(within(settlement).getAllByText("—").length).toBeGreaterThanOrEqual(2);
    expect(within(settlement).getByText("forward 0건 중 0건 정산")).toBeInTheDocument();
    expect(within(settlement).getByText("전체 0건 중 0건")).toBeInTheDocument();
    expect(within(settlement).getByText("forward 정산 대상이 아직 없습니다.")).toBeInTheDocument();
    expect(within(settlement).getByText("집계할 paper bid가 없습니다.")).toBeInTheDocument();
  });

  it("정산 커버리지: forward_coverage_rate가 null이면 forward 값을 대시로 표시한다", () => {
    const data: OperationsKpiResponse = {
      ...fullData,
      settlement_coverage: {
        total_paper_bids: 12,
        settled_count: 4,
        coverage_rate: 0.333,
        forward_paper_bids: 0,
        forward_settled_count: 0,
        forward_coverage_rate: null
      }
    };
    renderWithProviders(<OperationsKpiPanel data={data} loading={false} error={null} />);
    const settlement = screen.getByLabelText("정산 커버리지");
    // forward rate null → dash, but overall rate still renders
    expect(within(settlement).getByText("—")).toBeInTheDocument();
    expect(within(settlement).getByText("33.3%")).toBeInTheDocument();
    // forward 0건 empty-state note
    expect(within(settlement).getByText("forward 정산 대상이 아직 없습니다.")).toBeInTheDocument();
    // overall not empty (12건) → no overall empty-state note
    expect(
      within(settlement).queryByText("집계할 paper bid가 없습니다.")
    ).not.toBeInTheDocument();
  });

  it("놓친 유효 공고 항목 리스트를 렌더하고 클릭 시 공고 상세로 이동한다", () => {
    renderWithProviders(<OperationsKpiPanel data={fullData} loading={false} error={null} />);

    const missed = screen.getByLabelText("놓친 유효 공고");
    expect(within(missed).getByText("지나친 유효 공고 A")).toBeInTheDocument();
    expect(within(missed).getByText("지나친 유효 공고 B")).toBeInTheDocument();
    // missed_count emphasized
    expect(within(missed).getByText("2")).toBeInTheDocument();
    // action badge label
    expect(within(missed).getByText("투찰")).toBeInTheDocument();
    expect(within(missed).getByText("검토")).toBeInTheDocument();

    // clicking an item navigates to the project detail route
    fireEvent.click(within(missed).getByText("지나친 유효 공고 A"));
    expect(window.location.pathname).toBe("/dashboard/projects/42");
  });

  it("놓친 유효 공고가 0건이면 빈 상태 문구를 표시한다", () => {
    const emptyData: OperationsKpiResponse = {
      ...fullData,
      missed_opportunities: { missed_count: 0, items: [] }
    };
    renderWithProviders(<OperationsKpiPanel data={emptyData} loading={false} error={null} />);
    const missedEmpty = screen.getByLabelText("놓친 유효 공고");
    expect(within(missedEmpty).getByText("놓친 유효 공고가 없습니다.")).toBeInTheDocument();
  });

  it("로딩/에러 상태를 표시한다", () => {
    const { unmount } = renderWithProviders(
      <OperationsKpiPanel data={undefined} loading={true} error={null} />
    );
    expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
    unmount();
    cleanup();

    renderWithProviders(
      <OperationsKpiPanel
        data={undefined}
        loading={false}
        error={new Error("운영 KPI를 불러오지 못했습니다.")}
      />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("운영 KPI를 불러오지 못했습니다.");
  });
});
