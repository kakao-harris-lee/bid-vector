import type {
  OnboardingApplyRequest,
  OnboardingFieldSuggestion,
  OnboardingSuggestionValue
} from "@/shared/api";
import { isApplyField, type DecisionStatus } from "./constants";

/** 후보별 사용자 결정(상태 + 편집된 값). I/O 없는 순수 상태(§4.7-4). */
export interface DecisionState {
  status: DecisionStatus;
  value: OnboardingSuggestionValue;
}

export type DecisionMap = Record<string, DecisionState>;

/**
 * 후보의 유효 결정을 계산한다. override 가 없으면 초기값은 항상 `pending`(미확정)이고
 * 값은 서버 추천값이다 — 확정 전에는 어떤 후보도 accepted 로 두지 않는다(§2 정직 명세).
 */
export function effectiveDecision(
  suggestion: OnboardingFieldSuggestion,
  overrides: DecisionMap
): DecisionState {
  return overrides[suggestion.field] ?? { status: "pending", value: suggestion.value };
}

/**
 * apply 로 전송할 확정 결정 목록을 만든다. **accepted 후보만** 포함한다 —
 * pending/rejected 는 절대 전송하지 않는다(자동 반영 금지, §2 정직 명세).
 */
export function buildApplyDecisions(
  suggestions: readonly OnboardingFieldSuggestion[],
  overrides: DecisionMap
): OnboardingApplyRequest["decisions"] {
  const decisions: NonNullable<OnboardingApplyRequest["decisions"]> = [];
  for (const suggestion of suggestions) {
    const decision = effectiveDecision(suggestion, overrides);
    // accepted 이고, apply 화이트리스트에 있는 필드만 전송(미지 필드는 방어적으로 skip).
    // isApplyField 가 `suggestion.field` 를 OnboardingApplyField 로 좁혀 cast 가 불필요하다.
    if (decision.status === "accepted" && isApplyField(suggestion.field)) {
      decisions.push({ field: suggestion.field, value: decision.value });
    }
  }
  return decisions;
}

/** accepted 후보 수(확정 반영 버튼 활성 조건). */
export function acceptedCount(
  suggestions: readonly OnboardingFieldSuggestion[],
  overrides: DecisionMap
): number {
  return suggestions.reduce(
    (count, suggestion) =>
      effectiveDecision(suggestion, overrides).status === "accepted" ? count + 1 : count,
    0
  );
}
