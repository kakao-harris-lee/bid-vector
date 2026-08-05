/**
 * 금액 basis 표시 어휘 — 프론트 단일 출처.
 *
 * 실투찰 혼동의 원인은 화면이 추정가격(부가세 별도 표기)을 "예산" 한 이름으로 놓고,
 * 투찰율이 실제로 곱해지는 기초금액/사업금액(과세 공고면 부가세 포함)을 아예 보여주지
 * 않은 것이었다. 그래서 금액을 쓰는 자리는 화면마다 문구를 짓지 말고 이 어휘를 쓴다.
 *
 * 백엔드 대응: `app/schemas/bid_summary.py` 의 `BID_BASE_NOTE`,
 * `app/services/bid_base.py` 의 출처 어휘(`ReliableBaseSource` +
 * `BID_BASE_SOURCE_BUDGET_FALLBACK`). 응답이 note 를 실어 보내면 그 문자열을 그대로
 * 쓰고, note 가 없는 응답(공고 상세)에서만 여기 미러를 폴백으로 쓴다.
 */

import type { BadgeTone } from "@/shared/components/ui";

/**
 * 금액이 어느 기준(basis)의 값인지.
 * - `estimate`   : 추정가격 — 부가세 별도 표기. 투찰 기준금액이 아니다.
 * - `bid_base`   : 기초금액/사업금액 — 투찰율이 곱해지는 금액.
 * - `submission` : 나라장터에 그대로 입력하는 투찰금액(기초금액 기준 산정값).
 */
export type AmountBasis = "estimate" | "bid_base" | "submission";

/** 금액 행/필드의 기본 라벨. */
export const AMOUNT_BASIS_LABEL: Record<AmountBasis, string> = {
  estimate: "추정가격(부가세 별도)",
  bid_base: "투찰 기준금액(기초금액/사업금액)",
  submission: "투찰금액(제출값)"
};

/**
 * 금액 옆 뱃지 문구 — 라벨을 못 읽고 숫자만 훑어도 basis 가 붙어 보이게 한다.
 *
 * 기초금액 쪽을 "부가세 포함"이라 단정하지 않는 이유: 면세 공고는 추정가격과 같고,
 * 저장된 추정가격의 부가세 포함 여부도 수집 키에 따라 이력상 일관되지 않다
 * (`NoticeBidBase.to_estimate_ratio` docstring).
 */
export const AMOUNT_BASIS_BADGE: Record<AmountBasis, string> = {
  estimate: "부가세 별도",
  bid_base: "과세 공고 부가세 포함",
  submission: "부가세 포함 제출값"
};

/**
 * 뱃지를 놓을 수 없는 자리(테이블 헤더·`dt` 한 칸)에서 basis 를 라벨 텍스트에 합쳐 쓴다.
 * 같은 어휘를 화면마다 다시 짓지 않기 위한 것이며, 뱃지를 쓸 수 있으면 뱃지를 쓴다.
 */
export const AMOUNT_BASIS_TEXT_LABEL: Record<AmountBasis, string> = {
  estimate: "추정가격(부가세 별도)",
  bid_base: "투찰 기준금액(기초금액, 과세 공고 부가세 포함)",
  submission: "추천 투찰금액(부가세 포함 제출값)"
};

export const AMOUNT_BASIS_TONE: Record<AmountBasis, BadgeTone> = {
  estimate: "muted",
  bid_base: "info",
  submission: "watch"
};

/**
 * 두 금액이 왜 다른지 — `app/schemas/bid_summary.py::BID_BASE_NOTE` 미러.
 *
 * 응답에 `bid_base_note` 가 있으면 그 값을 쓰고, 없는 경계(공고 상세)에서만 쓴다.
 */
export const BID_BASE_NOTE =
  "투찰율이 곱해지는 기준금액은 기초금액/사업금액(과세 공고면 부가세 포함)이며, " +
  "추정가격(부가세 별도 표기)과 다른 금액입니다. 투찰금액은 기초금액 기준으로 " +
  "산정되므로 추정가격에 투찰율을 곱해 검산하면 값이 어긋납니다.";

/** 기초금액을 확보하지 못해 추정가격을 그대로 기준금액으로 쓴 상태의 provenance 값. */
export const BID_BASE_SOURCE_BUDGET_FALLBACK = "budget-estimate-fallback";

/** 기초금액 출처(provenance) 라벨 — `ReliableBaseSource` 어휘 그대로. */
export const BID_BASE_SOURCE_LABEL: Record<string, string> = {
  "clean-base": "공고 기초금액(신뢰)",
  "reserve-estimate": "복수예비가격 복원 추정",
  "base-fallback": "저장된 기초금액(basis 미상)",
  [BID_BASE_SOURCE_BUDGET_FALLBACK]: "기초금액 미확보 — 추정가격으로 대체",
  unavailable: "기초금액 없음"
};

/**
 * 폴백 상태 경고 — 이때 표시 금액은 기초금액이 아니라 추정가격이다.
 * 과세 공고면 실제 투찰 기준금액과 어긋나므로 조용히 넘기지 않는다.
 */
export const BID_BASE_FALLBACK_WARNING =
  "기초금액을 확보하지 못해 추정가격을 그대로 기준금액으로 표시했습니다. " +
  "과세 공고라면 실제 투찰 기준금액과 다를 수 있으니 공고 원문으로 확인하세요.";

/** 알 수 없는 출처 값도 감추지 않고 원문 그대로 노출한다(정직 표기). */
export function bidBaseSourceLabel(source: string | null | undefined): string | null {
  if (!source) return null;
  return BID_BASE_SOURCE_LABEL[source] ?? source;
}

/** 기초금액 ÷ 추정가격 표시. 두 금액의 관측된 비일 뿐 과세 여부 주장이 아니다. */
export function formatBaseToEstimateRatio(ratio: number | null | undefined): string | null {
  if (typeof ratio !== "number" || !Number.isFinite(ratio)) return null;
  return `추정가격 대비 ${ratio.toFixed(3)}배`;
}

/** 기초금액 기준 투찰률 라벨 — 추정가격 대비 율과 절대 같은 이름을 쓰지 않는다. */
export const BID_RATE_ON_BASE_LABEL = "투찰률(기초금액 대비)";

/** 참고 지표 쪽 라벨. 적격심사가 보는 율이 아니다. */
export const BID_RATE_ON_ESTIMATE_LABEL = "투찰률(추정가격 대비, 참고)";

export const BID_RATE_ON_BASE_NOTE =
  "낙찰하한율과 같은 기준(기초금액)의 투찰률입니다. 하한 비교는 이 값으로 합니다.";

export const BID_RATE_ON_ESTIMATE_NOTE =
  "추정가격 대비 참고 지표입니다. 적격심사가 보는 율이 아닙니다.";
