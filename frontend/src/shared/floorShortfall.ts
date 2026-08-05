/**
 * 하한 미달 빈도(과거 표본) 표시 결정 로직 — 순수 함수.
 *
 * 화면은 렌더만 하고 "무엇을 어떤 문구로 보여줄지"는 여기서 정한다(§4.7-4). 세 상태를
 * 절대 섞지 않는 것이 이 모듈의 존재 이유다:
 *
 * - `measured`     : 표본으로 빈도가 나옴(0% 도 여기 — 실제 측정값이다).
 * - `unmeasurable` : `shortfall_frequency === null`. **0% 가 아니라 판정 불가**이며
 *                    사유(`unmeasurable_reason`)를 함께 보여준다.
 * - `unavailable`  : 응답에 추정치 자체가 없음(산출 경로 미동작).
 *
 * 정직 명세(§2): 이 빈도는 이번 공고의 실격 가능성에 대한 주장이 아니라 과거 개찰
 * 표본에서 같은 투찰율이 하한 아래로 갔던 비율이다.
 */

import type { BadgeTone } from "@/shared/components/ui";
import { formatPercent } from "@/shared/lib";
import type { FloorShortfallEstimate } from "@/shared/types/floorShortfall";

export const FLOOR_SHORTFALL_TITLE = "하한 미달 빈도(과거 표본)";

export const FLOOR_SHORTFALL_UNMEASURABLE_LABEL = "판정 불가";
export const FLOOR_SHORTFALL_UNAVAILABLE_LABEL = "미산출";

/** 사유 없이 판정 불가만 온 경우 — 사유 자리를 비워두지 않는다. */
export const FLOOR_SHORTFALL_UNMEASURABLE_FALLBACK_REASON =
  "판정 불가 사유가 응답에 없습니다.";

export const FLOOR_SHORTFALL_UNAVAILABLE_NOTE =
  "이 결정에는 하한 미달 빈도가 산출되지 않았습니다. 하한 미달 위험이 없다는 뜻이 " +
  "아니라 계산되지 않았다는 뜻입니다.";

/** `app/services/bid_form_draft.py::SHORTFALL_NOTE` 미러(마크업 없는 평문). */
export const FLOOR_SHORTFALL_NOTE =
  "과거 개찰 표본에서 같은 투찰율이 낙찰하한 미달(실격)로 떨어졌을 표본 비율입니다. " +
  "이번 공고의 실격 확률이 아닙니다 — 예정가격은 개찰 시 추첨으로 정해집니다.";

export type FloorShortfallDisplayKind = "measured" | "unmeasurable" | "unavailable";

export interface FloorShortfallDisplay {
  kind: FloorShortfallDisplayKind;
  /** 값 자리에 그대로 넣는 문장. */
  headline: string;
  /** 근거(측정) 또는 사유(판정 불가). 없으면 null. */
  detail: string | null;
  /** 항상 따라붙는 정직 caveat. */
  note: string;
  tone: BadgeTone;
}

/**
 * 빈도 → 뱃지 톤. 조건 체인 대신 경계표로 선언한다(§4.5-2).
 *
 * 0% 는 "표본에서 한 건도 하한 아래로 가지 않았다"는 측정 결과라 healthy 를 쓴다.
 * 판정 불가는 이 표를 타지 않는다 — 그쪽은 안전해 보이면 안 된다.
 */
const TONE_BANDS: ReadonlyArray<{ below: number; tone: BadgeTone }> = [
  { below: 0.0005, tone: "healthy" },
  { below: 0.1, tone: "info" },
  { below: 0.3, tone: "watch" },
  { below: Number.POSITIVE_INFINITY, tone: "critical" }
];

function frequencyTone(frequency: number): BadgeTone {
  return TONE_BANDS.find((band) => frequency < band.below)?.tone ?? "critical";
}

function formatCount(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString("ko-KR") : "-";
}

/** 측정된 경우의 근거 한 줄: 임계 사정률 + 표본 범위. */
function measuredDetail(estimate: FloorShortfallEstimate): string | null {
  const parts: string[] = [];
  if (
    typeof estimate.critical_assessment_rate === "number" &&
    Number.isFinite(estimate.critical_assessment_rate)
  ) {
    parts.push(`임계 사정률 ${estimate.critical_assessment_rate}`);
  }
  parts.push(`하한 미달 표본 ${formatCount(estimate.shortfall_sample_count)}건`);
  if (estimate.scope) parts.push(estimate.scope);
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function describeFloorShortfall(
  estimate: FloorShortfallEstimate | null | undefined
): FloorShortfallDisplay {
  if (!estimate) {
    return {
      kind: "unavailable",
      headline: FLOOR_SHORTFALL_UNAVAILABLE_LABEL,
      detail: null,
      note: FLOOR_SHORTFALL_UNAVAILABLE_NOTE,
      tone: "muted"
    };
  }

  const frequency = estimate.shortfall_frequency;
  if (typeof frequency !== "number" || !Number.isFinite(frequency)) {
    return {
      kind: "unmeasurable",
      headline: FLOOR_SHORTFALL_UNMEASURABLE_LABEL,
      detail:
        estimate.unmeasurable_reason || FLOOR_SHORTFALL_UNMEASURABLE_FALLBACK_REASON,
      note: FLOOR_SHORTFALL_NOTE,
      tone: "muted"
    };
  }

  return {
    kind: "measured",
    headline: `과거 개찰 표본 ${formatCount(estimate.sample_count)}건 중 ${formatPercent(
      frequency
    )}가 낙찰하한 미달`,
    detail: measuredDetail(estimate),
    note: FLOOR_SHORTFALL_NOTE,
    tone: frequencyTone(frequency)
  };
}
