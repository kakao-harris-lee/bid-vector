import { t } from "@/shared/i18n";
import type { DecisionAction, DecisionStatus } from "@/shared/types/decisions";

/**
 * 단일 출처 결정 라벨 맵.
 *
 * `bid_now/review/skip` 액션 라벨·톤과 `planned/reviewing/submitted/skipped`
 * 결정 상태 라벨이 여러 화면에 복붙돼 값이 어긋나던 것을 한 곳으로 모았습니다.
 * 문구는 ko 단일 번들(`shared/i18n/ko.json`)에서 가져옵니다.
 *
 * 정본 결정(`planned` 드리프트 해소): 화면 다수(DecisionsScreen,
 * OperationsKpiPanel, ProjectDetailScreen)가 `planned:"예정"`,
 * `reviewing:"검토 중"`을 렌더했으므로 이를 정본으로 삼고, 어긋나 있던
 * `shared/lib.ts`의 `"계획"`/`"검토"`를 정렬했습니다.
 */

/** 추천 액션(`bid_now/review/skip`) 한국어 라벨. */
export const ACTION_LABEL: Record<DecisionAction, string> = {
  bid_now: t("decision_reasons.action_bid_now"),
  review: t("decision_reasons.action_review"),
  skip: t("decision_reasons.action_skip")
};

/** 추천 액션 배지 톤. */
export const ACTION_TONE: Record<DecisionAction, "healthy" | "watch" | "muted"> = {
  bid_now: "healthy",
  review: "watch",
  skip: "muted"
};

/** 결정 상태(`planned/reviewing/submitted/skipped`) 한국어 라벨. */
export const DECISION_STATUS_LABEL: Record<DecisionStatus, string> = {
  planned: t("decision_status.planned"),
  reviewing: t("decision_status.reviewing"),
  submitted: t("decision_status.submitted"),
  skipped: t("decision_status.skipped")
};

/** 임의 문자열도 받아 미지정 상태는 원문을 그대로 돌려주는 안전한 룩업. */
export function labelDecisionStatus(status: string): string {
  return DECISION_STATUS_LABEL[status as DecisionStatus] ?? status;
}
