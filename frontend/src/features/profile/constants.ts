/**
 * Front-end curated recommendation chips for the company-info editor.
 *
 * MATCHING NOTE (critical): the recommendation value that gets stored in
 * `license_codes` must be a token the backend classifier can recognise when it
 * compares against a project's required licenses. The classifier
 * (`app/services/classifier.py::_extract_license_tokens` / `LICENSE_ALIASES`)
 * only normalises a fixed set of license aliases into canonical codes:
 *
 *   SW001  ← 소프트웨어사업자 / SW사업자
 *   NET001 ← 정보통신공사업 / 정보통신공사
 *   ENG001 ← 엔지니어링사업 / 엔지니어링 / 기술사 / 감리
 *   SEC001 ← 정보보호전문서비스 / 보안관제 / ISMS
 *   ELE001 ← 전기공사업 / 전기
 *   FIRE001 ← 소방시설 / 소방
 *
 * Plus any raw `[A-Z]{2,}\d{2,}` code (e.g. typing a code directly). Korean
 * construction-license names that are NOT in that alias table (건축공사업,
 * 토목공사업 등) extract to an empty token set and therefore CANNOT match a
 * project requirement today. We still surface them as chips for record-keeping,
 * but flag them so the operator knows they won't drive matching yet, and we
 * store the canonical code/alias when one exists.
 */

export interface LicenseChip {
  /** Korean label shown on the chip. */
  label: string;
  /** Token actually stored in `license_codes` (matchable when `matchable`). */
  value: string;
  /** Whether the classifier can match this token against project requirements. */
  matchable: boolean;
}

/**
 * Licenses the classifier recognises. `value` is the alias/code that
 * `_extract_license_tokens` normalises into a canonical token, so storing it
 * lets profile↔project license matching work.
 */
export const MATCHABLE_LICENSE_CHIPS: LicenseChip[] = [
  { label: "정보통신공사업", value: "정보통신공사업", matchable: true },
  { label: "전기공사업", value: "전기공사업", matchable: true },
  { label: "소방시설공사업", value: "소방시설", matchable: true },
  { label: "엔지니어링·감리", value: "엔지니어링", matchable: true },
  { label: "소프트웨어사업자", value: "소프트웨어사업자", matchable: true },
  { label: "정보보호전문서비스", value: "정보보호전문서비스", matchable: true }
];

/**
 * Common construction licenses that the classifier does NOT yet map. Stored
 * verbatim for documentation; they will not influence matching until the
 * backend alias table is extended.
 */
export const RECORD_ONLY_LICENSE_CHIPS: LicenseChip[] = [
  { label: "건축공사업", value: "건축공사업", matchable: false },
  { label: "토목공사업", value: "토목공사업", matchable: false },
  { label: "토목건축공사업", value: "토목건축공사업", matchable: false },
  { label: "조경공사업", value: "조경공사업", matchable: false },
  { label: "산업·환경설비공사업", value: "산업환경설비공사업", matchable: false },
  { label: "실내건축공사업", value: "실내건축공사업", matchable: false },
  { label: "기계설비공사업", value: "기계설비공사업", matchable: false },
  { label: "가스시설공사업", value: "가스시설공사업", matchable: false }
];

export const LICENSE_CHIPS: LicenseChip[] = [
  ...MATCHABLE_LICENSE_CHIPS,
  ...RECORD_ONLY_LICENSE_CHIPS
];

/**
 * Region chips. Values use the canonical short names from the classifier's
 * `REGION_ALIASES` table (전국 + 17 시·도), which is exactly what
 * `_extract_regions` recognises, so they drive region matching directly.
 */
export const REGION_CHIPS: readonly string[] = [
  "전국",
  "서울",
  "부산",
  "대구",
  "인천",
  "광주",
  "대전",
  "울산",
  "세종",
  "경기",
  "강원",
  "충북",
  "충남",
  "전북",
  "전남",
  "경북",
  "경남",
  "제주"
];

export interface BusinessTypeOption {
  /** Canonical-ish label stored verbatim into `business_type`. */
  value: string;
  label: string;
}

/**
 * Business-type options. Values are Korean terms the classifier's
 * `BUSINESS_TYPE_ALIASES` normalises (공사→construction, 기술용역→technical-service,
 * 일반용역/용역→service, 물품→goods, 소프트웨어→software, 기타→other).
 */
export const BUSINESS_TYPE_OPTIONS: BusinessTypeOption[] = [
  { value: "공사", label: "공사 (건설)" },
  { value: "기술용역", label: "기술용역 (엔지니어링·설계·감리)" },
  { value: "일반용역", label: "일반용역" },
  { value: "물품", label: "물품 (구매·납품)" },
  { value: "소프트웨어", label: "소프트웨어·정보화" },
  { value: "기타", label: "기타" }
];
