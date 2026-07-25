import type {
  OnboardingApplyDecisionPayload,
  OnboardingFieldSuggestion,
  OnboardingSentStatus,
  OnboardingSuggestionValue
} from "@/shared/api";
import { isApplyField, type DecisionButtonStatus } from "./constants";

/** 후보별 사용자 결정(상태 + 편집된 값). I/O 없는 순수 상태(§4.7-4). */
export interface DecisionState {
  status: DecisionButtonStatus;
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
 * 두 후보값이 같은지(값이 편집됐는지) 판정하는 순수 함수(§4.7-4, I/O 없음).
 * 값 종류는 문자열/숫자/문자열 리스트라, 배열은 길이 + 원소를 순서까지 deep-equal
 * 로 비교한다(원소나 순서가 바뀌면 편집으로 본다 → 감사에 modified 로 정직 기록).
 */
export function sameSuggestionValue(
  a: OnboardingSuggestionValue,
  b: OnboardingSuggestionValue
): boolean {
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    return a.length === b.length && a.every((item, index) => item === b[index]);
  }
  return a === b;
}

/**
 * UI 결정을 전송(감사) 상태로 매핑하는 순수 함수(§4.7-4). `pending`(미확정)은 절대
 * 전송하지 않으므로(정직 명세 §2) `null` 을 돌려준다. accepted 는 값이 원 추천값과
 * 다르면 `modified`, 같으면 `accepted` 로 세분한다 — SuggestionCard 에 새 UI 상태를
 * 심지 않고, 전송 시점에 `suggestion.value` 와 deep-equal 비교로 도출한다.
 */
export function sentStatusFor(
  decision: DecisionState,
  suggestion: OnboardingFieldSuggestion
): OnboardingSentStatus | null {
  switch (decision.status) {
    case "rejected":
      return "rejected";
    case "accepted":
      return sameSuggestionValue(decision.value, suggestion.value) ? "accepted" : "modified";
    default:
      return null; // pending — 절대 전송하지 않음
  }
}

/**
 * apply 로 전송할 결정 목록을 만든다. accepted/modified/rejected 를 모두 전송하되
 * **pending 은 절대 전송하지 않는다**(자동 반영 금지, §2 정직 명세). rejected 는
 * 감사만 되고(백엔드가 반영 상태를 강제) 프로필/전략을 바꾸지 않으므로, 프론트는
 * 결정 상태를 정직하게 보고만 한다.
 */
export function buildApplyDecisions(
  suggestions: readonly OnboardingFieldSuggestion[],
  overrides: DecisionMap
): OnboardingApplyDecisionPayload[] {
  const decisions: OnboardingApplyDecisionPayload[] = [];
  for (const suggestion of suggestions) {
    const decision = effectiveDecision(suggestion, overrides);
    const status = sentStatusFor(decision, suggestion);
    // pending(status===null)은 전송 제외. apply 화이트리스트에 없는 필드는 거부
    // 결정이라도 방어적으로 skip(미지 필드 오염 방지). isApplyField 가
    // `suggestion.field` 를 OnboardingApplyField 로 좁혀 cast 가 불필요하다.
    if (status === null || !isApplyField(suggestion.field)) continue;
    // 전송값은 canonical raw(decision.value) — 표시 라벨로 치환하지 않는다.
    // GET 후보의 provenance(source/confidence/reason)를 감사용으로 전달한다(#236 감사
    // 테이블이 저장). 후보에 실제로 있는 필드만 싣고, 없으면(undefined) 생략한다 —
    // 반영 로직에는 영향이 없고 감사 컬럼만 채운다.
    decisions.push({
      field: suggestion.field,
      value: decision.value,
      status,
      ...pickProvenance(suggestion)
    });
  }
  return decisions;
}

/**
 * GET 후보에서 감사용 provenance 만 추려낸다(§4.7-4 순수 함수). `undefined` 인 필드는
 * payload 에서 생략해(빈 값 오염 방지), 후보가 실제로 가진 provenance 만 전달한다.
 */
function pickProvenance(
  suggestion: OnboardingFieldSuggestion
): Pick<OnboardingApplyDecisionPayload, "source" | "confidence" | "reason"> {
  const provenance: Pick<
    OnboardingApplyDecisionPayload,
    "source" | "confidence" | "reason"
  > = {};
  if (suggestion.source !== undefined) provenance.source = suggestion.source;
  if (suggestion.confidence !== undefined) provenance.confidence = suggestion.confidence;
  if (suggestion.reason !== undefined) provenance.reason = suggestion.reason;
  return provenance;
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
