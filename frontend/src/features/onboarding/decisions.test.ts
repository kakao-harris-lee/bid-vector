import { describe, expect, it } from "vitest";
import type { OnboardingFieldSuggestion, OnboardingSuggestionValue } from "@/shared/api";
import {
  buildApplyDecisions,
  sameSuggestionValue,
  sentStatusFor,
  type DecisionMap
} from "./decisions";

/** 최소 provenance 를 채운 후보 팩토리(값/필드만 케이스별로 바꾼다). */
function suggestion(
  field: string,
  value: OnboardingSuggestionValue
): OnboardingFieldSuggestion {
  return {
    field,
    value,
    source: "internal_notices",
    confidence: 0.5,
    needs_confirmation: true,
    reason: "테스트 후보",
    matched_notice_count: 3
  };
}

describe("sameSuggestionValue", () => {
  it.each([
    ["같은 문자열", "construction", "construction", true],
    ["다른 문자열", "construction", "service", false],
    ["같은 숫자", 5_000_000, 5_000_000, true],
    ["다른 숫자", 5_000_000, 6_000_000, false],
    ["같은 배열(동일 순서)", ["a", "b"], ["a", "b"], true],
    ["순서 다른 배열", ["a", "b"], ["b", "a"], false],
    ["길이 다른 배열", ["a", "b"], ["a"], false],
    ["배열 vs 스칼라", ["a"], "a", false]
  ] as [string, OnboardingSuggestionValue, OnboardingSuggestionValue, boolean][])(
    "%s → %s",
    (_label, a, b, expected) => {
      expect(sameSuggestionValue(a, b)).toBe(expected);
    }
  );
});

describe("sentStatusFor", () => {
  it("pending 은 전송하지 않는다(null)", () => {
    const s = suggestion("business_type", "construction");
    expect(sentStatusFor({ status: "pending", value: s.value }, s)).toBeNull();
  });

  it("rejected 는 값과 무관하게 rejected", () => {
    const s = suggestion("business_type", "construction");
    expect(sentStatusFor({ status: "rejected", value: "construction" }, s)).toBe("rejected");
  });

  it("accepted + 원 추천값 그대로 → accepted", () => {
    const s = suggestion("business_type", "construction");
    expect(sentStatusFor({ status: "accepted", value: "construction" }, s)).toBe("accepted");
  });

  it("accepted + 편집된 값 → modified", () => {
    const s = suggestion("business_type", "construction");
    expect(sentStatusFor({ status: "accepted", value: "전문건설" }, s)).toBe("modified");
  });

  it("accepted + 배열 원소가 바뀜 → modified", () => {
    const s = suggestion("license_codes", ["항만공사업"]);
    expect(sentStatusFor({ status: "accepted", value: ["항만공사업", "준설"] }, s)).toBe(
      "modified"
    );
  });

  it("accepted + 배열 deep-equal(동일 순서) → accepted", () => {
    const s = suggestion("license_codes", ["항만공사업", "준설"]);
    expect(sentStatusFor({ status: "accepted", value: ["항만공사업", "준설"] }, s)).toBe(
      "accepted"
    );
  });
});

describe("buildApplyDecisions", () => {
  it("accepted/modified/rejected 를 모두 전송하고 pending 만 제외한다", () => {
    const suggestions = [
      suggestion("business_type", "construction"), // accepted(unchanged)
      suggestion("license_codes", ["항만공사업"]), // modified(배열 편집)
      suggestion("region_codes", ["11"]), // rejected
      suggestion("focus_categories", ["service"]) // pending → 미전송
    ];
    const overrides: DecisionMap = {
      business_type: { status: "accepted", value: "construction" },
      license_codes: { status: "accepted", value: ["항만공사업", "준설"] },
      region_codes: { status: "rejected", value: ["11"] }
      // focus_categories 는 override 없음 → pending
    };

    expect(buildApplyDecisions(suggestions, overrides)).toEqual([
      { field: "business_type", value: "construction", status: "accepted" },
      { field: "license_codes", value: ["항만공사업", "준설"], status: "modified" },
      { field: "region_codes", value: ["11"], status: "rejected" }
    ]);
  });

  it("전송값은 canonical raw 를 그대로 싣는다(표시 라벨 치환 없음)", () => {
    const suggestions = [suggestion("focus_categories", ["service", "goods"])];
    const overrides: DecisionMap = {
      focus_categories: { status: "accepted", value: ["service", "goods"] }
    };
    // service→용역, goods→물품 같은 표시 매핑이 payload 를 오염시키지 않는다.
    expect(buildApplyDecisions(suggestions, overrides)).toEqual([
      { field: "focus_categories", value: ["service", "goods"], status: "accepted" }
    ]);
  });

  it("화이트리스트에 없는 필드는 rejected 여도 방어적으로 skip 한다", () => {
    const suggestions = [suggestion("unknown_field", "x")];
    const overrides: DecisionMap = {
      unknown_field: { status: "rejected", value: "x" }
    };
    expect(buildApplyDecisions(suggestions, overrides)).toEqual([]);
  });

  it("아무 결정도 안 하면(모두 pending) 빈 목록을 반환한다(no-op)", () => {
    const suggestions = [suggestion("business_type", "construction")];
    expect(buildApplyDecisions(suggestions, {})).toEqual([]);
  });
});
