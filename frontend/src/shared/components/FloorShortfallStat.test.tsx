import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FloorShortfallStat } from "@/shared/components";
import { FLOOR_SHORTFALL_TITLE } from "@/shared/floorShortfall";
import type { FloorShortfallEstimate } from "@/shared/types/floorShortfall";

const measured: FloorShortfallEstimate = {
  shortfall_frequency: 0.124,
  shortfall_sample_count: 17,
  sample_count: 137,
  minimum_sample_count: 30,
  critical_assessment_rate: 1.0234,
  scope: "공사 · clean base",
  unmeasurable_reason: null
};

describe("FloorShortfallStat", () => {
  it("측정된 빈도는 표본 수와 비율 문장으로 보여준다", () => {
    render(<FloorShortfallStat estimate={measured} />);

    expect(screen.getByText(FLOOR_SHORTFALL_TITLE)).toBeInTheDocument();
    expect(
      screen.getByText("과거 개찰 표본 137건 중 12.4%가 낙찰하한 미달")
    ).toBeInTheDocument();
    expect(screen.getByText(/임계 사정률 1.0234/)).toBeInTheDocument();
  });

  it("판정 불가는 0% 로 표시하지 않고 사유를 함께 보여준다", () => {
    render(
      <FloorShortfallStat
        estimate={{
          ...measured,
          shortfall_frequency: null,
          sample_count: 4,
          unmeasurable_reason: "사정률 표본 4건 < 최소 30건"
        }}
      />
    );

    expect(screen.getByText("판정 불가")).toBeInTheDocument();
    expect(screen.getByText("사정률 표본 4건 < 최소 30건")).toBeInTheDocument();
    expect(screen.queryByText(/0\.0%/)).not.toBeInTheDocument();
  });

  it("추정치가 없으면 미산출로 구분해 표시한다", () => {
    render(<FloorShortfallStat estimate={null} />);

    expect(screen.getByText("미산출")).toBeInTheDocument();
    expect(
      screen.getByText(/하한 미달 위험이 없다는 뜻이 아니라 계산되지 않았다는 뜻입니다/)
    ).toBeInTheDocument();
  });
});
