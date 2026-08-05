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

/**
 * 빈도 밴드 → 뱃지에 실제로 찍히는 색 토큰(`ui/badge.tsx` 의 tone 클래스).
 *
 * 톤을 `describeFloorShortfall` 이 계산해 놓고 렌더러가 뱃지를 안 그리면 시각 신호가
 * 죽는다. 그래서 반환값이 아니라 **DOM 에 나온 클래스**를 확인한다.
 */
const TONE_CASES = [
  { frequency: 0, label: "표본 내 미달 사실상 없음", colorToken: "--color-success" },
  { frequency: 0.05, label: "표본 내 미달 드묾", colorToken: "--color-info" },
  { frequency: 0.124, label: "표본 내 미달 잦음", colorToken: "--color-warn" },
  { frequency: 0.7, label: "표본 내 미달 매우 잦음", colorToken: "--color-danger" }
];

const MUTED_TOKEN = "var(--color-secondary)";

describe("FloorShortfallStat", () => {
  it("측정된 빈도는 표본 수와 비율 문장으로 보여준다", () => {
    render(<FloorShortfallStat estimate={measured} />);

    expect(screen.getByText(FLOOR_SHORTFALL_TITLE)).toBeInTheDocument();
    expect(
      screen.getByText("과거 개찰 표본 137건 중 12.4%가 낙찰하한 미달")
    ).toBeInTheDocument();
    expect(screen.getByText(/임계 사정률 1.0234/)).toBeInTheDocument();
  });

  it.each(TONE_CASES)(
    "측정된 빈도 $label 는 숫자 문장 옆에 톤 뱃지로도 신호를 낸다",
    ({ frequency, label, colorToken }) => {
      render(
        <FloorShortfallStat
          estimate={{ ...measured, shortfall_frequency: frequency }}
        />
      );

      const badge = screen.getByText(label);
      expect(badge).toBeInTheDocument();
      expect(badge.className).toContain(colorToken);
      // 판정 불가(muted)와 색이 겹치면 구분 신호가 되지 않는다.
      expect(badge.className).not.toContain(MUTED_TOKEN);
    }
  );

  it("판정 불가 뱃지는 측정 톤과 다른 muted 이고 빈도 밴드 라벨을 달지 않는다", () => {
    render(
      <FloorShortfallStat
        estimate={{
          ...measured,
          shortfall_frequency: null,
          unmeasurable_reason: "사정률 표본 4건 < 최소 30건"
        }}
      />
    );

    expect(screen.getByText("판정 불가").className).toContain(MUTED_TOKEN);
    expect(screen.queryByText(/표본 내 미달/)).not.toBeInTheDocument();
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
