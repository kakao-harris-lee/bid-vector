import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderWithProviders } from "@/test-utils";
import { SampleReportView } from "./SampleReportView";
import type { SyntheticSampleReport } from "@/shared/types/synthetic";

const report: SyntheticSampleReport = {
  preset_name: "g1-software-base-12m",
  group_sample_target: 30,
  operator_sample_target: 30,
  run_total_sample_target: 100,
  synthetic_only: true,
  ready_for_repeatable_reporting: false,
  report_status: "insufficient_sample",
  by_preset: [
    {
      dimension: "preset",
      key: "g1-software-base-12m",
      settled_count: 42,
      sample_target: 100,
      missing_settled_count: 58,
      sample_status: "insufficient_sample",
      would_have_won_count: 10,
      est_price_close_rate: 0.238095,
      avg_abs_bid_rate_error: 0.02
    }
  ],
  by_category: [
    {
      dimension: "category",
      key: "software",
      settled_count: 24,
      sample_target: 30,
      missing_settled_count: 6,
      sample_status: "insufficient_sample",
      would_have_won_count: 8,
      est_price_close_rate: 0.333333,
      avg_abs_bid_rate_error: 0.015
    }
  ],
  by_business_type: [
    {
      dimension: "business_type",
      key: "software",
      settled_count: 42,
      sample_target: 30,
      missing_settled_count: 0,
      sample_status: "sufficient",
      would_have_won_count: 10,
      est_price_close_rate: 0.238095,
      avg_abs_bid_rate_error: 0.02
    }
  ],
  by_budget_band: [
    {
      dimension: "budget_band",
      key: "1eok_5eok",
      settled_count: 24,
      sample_target: 30,
      missing_settled_count: 6,
      sample_status: "insufficient_sample",
      would_have_won_count: 8,
      est_price_close_rate: 0.333333,
      avg_abs_bid_rate_error: 0.015
    }
  ],
  lacking_groups: [
    {
      dimension: "preset",
      key: "g1-software-base-12m",
      settled_count: 42,
      sample_target: 100,
      missing_settled_count: 58
    },
    {
      dimension: "category",
      key: "software",
      settled_count: 24,
      sample_target: 30,
      missing_settled_count: 6
    }
  ]
};

describe("SampleReportView", () => {
  it("renders readiness status, lacking groups, and metric rows", () => {
    renderWithProviders(<SampleReportView report={report} />);

    expect(screen.getByText("sample gaps")).toBeInTheDocument();
    expect(screen.getAllByText("g1-software-base-12m").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("blocked")).toBeInTheDocument();

    const gaps = screen.getByLabelText("샘플 부족 그룹");
    expect(within(gaps).getByText("Preset")).toBeInTheDocument();
    expect(within(gaps).getByText("58")).toBeInTheDocument();
    expect(within(gaps).getByText("카테고리")).toBeInTheDocument();
    expect(within(gaps).getByText("6")).toBeInTheDocument();

    expect(screen.getByLabelText("예산구간 report")).toHaveTextContent("1-5억");
    expect(screen.getAllByText("33.3%").length).toBeGreaterThanOrEqual(1);
  });

  it("flags explicit canonical data mixes", () => {
    renderWithProviders(
      <SampleReportView
        report={{
          ...report,
          synthetic_only: false,
          non_synthetic_operator_slugs: ["operator"],
          report_status: "canonical_synthetic_mixed"
        }}
      />
    );

    expect(screen.getByText("data mixed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("operator");
  });
});
