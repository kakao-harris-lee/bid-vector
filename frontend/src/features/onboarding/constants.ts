import type { BadgeTone } from "@/shared/components/ui";
import type { OnboardingApplyField } from "@/shared/api";

/**
 * 온보딩 wizard 의 선언적 구성(§4.5-1/2/3).
 *
 * 단계 순서·라벨·필드 편집 종류·후보 상태 표시를 코드 분기 대신 데이터로 모은다.
 * 새 필드/단계는 여기 표에 한 줄 추가하는 것으로 확장한다.
 */

// --- wizard 단계 ------------------------------------------------------------

export type StepKey = "seed" | "review" | "result" | "preview";

export interface StepMeta {
  key: StepKey;
  /** 진행 표시용 순번(1-base). */
  order: number;
  title: string;
  description: string;
}

/** 설계 §UI 2~5단계에 대응(1단계 사업자번호는 국세청 미구현이라 후속). */
export const STEP_ORDER: readonly StepKey[] = ["seed", "review", "result", "preview"];

export const STEP_META: Record<StepKey, StepMeta> = {
  seed: {
    key: "seed",
    order: 1,
    title: "기본 후보 입력",
    description: "관심 키워드로 내부 공고에서 프로필·전략 후보를 역추천합니다."
  },
  review: {
    key: "review",
    order: 2,
    title: "후보 검토",
    description: "추천 후보는 확정이 아닙니다. 각 후보를 수락·수정하거나 거부하세요."
  },
  result: {
    key: "result",
    order: 3,
    title: "확정 반영",
    description: "수락한 값만 회사 정보·감시 전략에 반영했습니다."
  },
  preview: {
    key: "preview",
    order: 4,
    title: "공고 미리보기",
    description: "확정한 조건으로 현재 열린 공고 중 추천 후보를 확인합니다."
  }
};

// --- 후보 필드 메타 ---------------------------------------------------------

/** 후보값 편집 방식 — 편집 UI 는 이 종류를 룩업으로 디스패치(§4.5-2). */
export type FieldKind = "text" | "chips" | "number";

/** 후보가 반영되는 대상(설계 §3). */
export type FieldTarget = "profile" | "strategy";

export interface FieldMeta {
  label: string;
  kind: FieldKind;
  target: FieldTarget;
  /**
   * 값이 canonical 코드(예: `technical-service`)라 표시할 때 한국어로 매핑해야 하는지.
   * 면허/지역은 이미 한국어라 매핑 대상이 아니다(§8 ko 단일 번들).
   */
  codeValued?: boolean;
}

/**
 * 후보 필드 → 표시 라벨·편집 종류·반영 대상 단일 출처.
 * 키는 백엔드 `OnboardingApplyField` enum 과 동일해야 한다(apply 화이트리스트).
 */
export const FIELD_META: Record<string, FieldMeta> = {
  business_type: { label: "업무 구분", kind: "text", target: "profile", codeValued: true },
  license_codes: { label: "보유 면허 코드", kind: "chips", target: "profile" },
  region_codes: { label: "수행 지역 코드", kind: "chips", target: "profile" },
  focus_categories: { label: "관심 카테고리", kind: "chips", target: "strategy", codeValued: true },
  focus_regions: { label: "관심 지역", kind: "chips", target: "strategy" },
  min_budget_estimate: { label: "최소 예산", kind: "number", target: "strategy" },
  max_budget_estimate: { label: "최대 예산", kind: "number", target: "strategy" }
};

export function fieldMeta(field: string): FieldMeta {
  return FIELD_META[field] ?? { label: field, kind: "text", target: "profile" };
}

/**
 * canonical 업무구분/카테고리 코드 → 한국어 라벨(§4.5-1, ko 단일 번들 §8).
 * `app/services/classification/taxonomy.py` `BUSINESS_TYPE_ALIASES` 정본 키에 대응하며,
 * project.category(focus_categories 소스)도 같은 taxonomy 키를 공유한다.
 */
export const VALUE_LABELS: Record<string, string> = {
  software: "소프트웨어",
  "technical-service": "기술용역",
  service: "용역",
  goods: "물품",
  construction: "공사",
  other: "기타"
};

/**
 * codeValued 필드값을 표시용 한국어로 매핑한다. **미지 코드는 raw 그대로 노출**하고
 * (조용한 오표시 금지), 저장/전송 값은 바꾸지 않는다 — 표시 전용 매핑이다.
 */
export function displayValue(field: string, value: string): string {
  if (!fieldMeta(field).codeValued) return value;
  return VALUE_LABELS[value] ?? value;
}

// --- 후보 출처(source) 라벨 --------------------------------------------------

/**
 * 후보 출처 코드 → 한국어 라벨(§4.5-1/2). 현재 백엔드는 internal_notices 단일 source
 * 지만, source 를 데이터로 구동해 백엔드가 외부확인/직접입력 source 를 추가해도
 * **미지 source 는 raw 그대로 노출**한다(조용한 오표시 금지, 설계 §2).
 */
export const SOURCE_META: Record<string, string> = {
  internal_notices: "내부 공고 추론"
};

export function sourceLabel(source: string): string {
  return SOURCE_META[source] ?? source;
}

// --- apply 화이트리스트(런타임 가드) -----------------------------------------

/**
 * apply 로 전송 가능한 필드 집합. `satisfies` 로 백엔드 `OnboardingApplyField` union 과
 * 컴파일 타임에 묶어, 두 곳이 어긋나면 빌드가 깨지게 한다.
 */
export const APPLY_FIELDS = [
  "business_type",
  "license_codes",
  "region_codes",
  "focus_categories",
  "focus_regions",
  "min_budget_estimate",
  "max_budget_estimate"
] as const satisfies readonly OnboardingApplyField[];

const APPLY_FIELD_SET: ReadonlySet<string> = new Set(APPLY_FIELDS);

export function isApplyField(field: string): field is OnboardingApplyField {
  return APPLY_FIELD_SET.has(field);
}

// --- 후보 결정 상태 ---------------------------------------------------------

/** 후보별 사용자 결정. `pending`= 미확정(초기), `accepted`= 수락, `rejected`= 거부. */
export type DecisionStatus = "pending" | "accepted" | "rejected";

export interface DecisionStatusMeta {
  label: string;
  tone: BadgeTone;
}

/**
 * 후보 상태 표시(색이 아니라 라벨로도 구분 — 접근성).
 * `pending`= 추천 후보(미확정)를 확정값처럼 보이지 않게(§2 정직 명세).
 */
export const DECISION_STATUS_META: Record<DecisionStatus, DecisionStatusMeta> = {
  pending: { label: "추천 후보 · 확인 필요", tone: "watch" },
  accepted: { label: "확정", tone: "healthy" },
  rejected: { label: "거부", tone: "muted" }
};
