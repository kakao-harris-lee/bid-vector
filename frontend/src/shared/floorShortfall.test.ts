import { describe, expect, it } from "vitest";
import {
  FLOOR_SHORTFALL_UNAVAILABLE_LABEL,
  FLOOR_SHORTFALL_UNMEASURABLE_FALLBACK_REASON,
  FLOOR_SHORTFALL_UNMEASURABLE_LABEL,
  describeFloorShortfall
} from "@/shared/floorShortfall";
import type { FloorShortfallEstimate } from "@/shared/types/floorShortfall";

function estimate(overrides: Partial<FloorShortfallEstimate> = {}): FloorShortfallEstimate {
  return {
    shortfall_frequency: 0.124,
    shortfall_sample_count: 17,
    sample_count: 137,
    minimum_sample_count: 30,
    critical_assessment_rate: 1.0234,
    scope: "공사 · clean base · 2025-01-01 이후",
    unmeasurable_reason: null,
    ...overrides
  };
}

describe("describeFloorShortfall", () => {
  it("측정된 빈도는 표본 수와 비율을 함께 문장으로 낸다", () => {
    const display = describeFloorShortfall(estimate());

    expect(display.kind).toBe("measured");
    expect(display.headline).toBe("과거 개찰 표본 137건 중 12.4%가 낙찰하한 미달");
    expect(display.detail).toContain("임계 사정률 1.0234");
    expect(display.detail).toContain("하한 미달 표본 17건");
    expect(display.detail).toContain("공사 · clean base");
  });

  it("빈도 0% 는 측정 결과이므로 판정 불가와 구분한다", () => {
    const display = describeFloorShortfall(
      estimate({ shortfall_frequency: 0, shortfall_sample_count: 0 })
    );

    expect(display.kind).toBe("measured");
    expect(display.headline).toContain("0.0%");
    expect(display.headline).not.toContain(FLOOR_SHORTFALL_UNMEASURABLE_LABEL);
    expect(display.tone).toBe("healthy");
  });

  it("빈도가 높을수록 경고 톤이 올라간다", () => {
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.05 })).tone).toBe("info");
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.2 })).tone).toBe("watch");
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.7 })).tone).toBe(
      "critical"
    );
  });

  it("측정된 경우 톤 뱃지 라벨을 함께 낸다 — 과거 표본 서술이지 이번 공고 주장이 아니다", () => {
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0 })).badgeLabel).toBe(
      "표본 내 미달 사실상 없음"
    );
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.05 })).badgeLabel).toBe(
      "표본 내 미달 드묾"
    );
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.2 })).badgeLabel).toBe(
      "표본 내 미달 잦음"
    );
    expect(describeFloorShortfall(estimate({ shortfall_frequency: 0.7 })).badgeLabel).toBe(
      "표본 내 미달 매우 잦음"
    );
  });

  it("frequency 가 null 이면 0% 가 아니라 판정 불가 + 사유다", () => {
    const display = describeFloorShortfall(
      estimate({
        shortfall_frequency: null,
        sample_count: 4,
        unmeasurable_reason: "사정률 표본 4건 < 최소 30건"
      })
    );

    expect(display.kind).toBe("unmeasurable");
    expect(display.headline).toBe(FLOOR_SHORTFALL_UNMEASURABLE_LABEL);
    expect(display.headline).not.toContain("%");
    expect(display.detail).toBe("사정률 표본 4건 < 최소 30건");
    // headline 자체가 뱃지 문구다 — 빈도 밴드 라벨을 붙이면 측정된 것처럼 읽힌다.
    expect(display.badgeLabel).toBeNull();
  });

  it("사유 없는 판정 불가도 사유 자리를 비워두지 않는다", () => {
    const display = describeFloorShortfall(
      estimate({ shortfall_frequency: null, unmeasurable_reason: null })
    );

    expect(display.kind).toBe("unmeasurable");
    expect(display.detail).toBe(FLOOR_SHORTFALL_UNMEASURABLE_FALLBACK_REASON);
  });

  it("추정치 자체가 없으면 미산출로 구분하고 안전으로 읽히지 않게 한다", () => {
    const display = describeFloorShortfall(null);

    expect(display.kind).toBe("unavailable");
    expect(display.headline).toBe(FLOOR_SHORTFALL_UNAVAILABLE_LABEL);
    expect(display.badgeLabel).toBeNull();
    expect(display.note).toContain("하한 미달 위험이 없다는 뜻이 아니라");
  });
});
