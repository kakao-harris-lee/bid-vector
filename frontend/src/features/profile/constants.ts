/**
 * Front-end curated recommendation chips for the company-info editor.
 *
 * MATCHING NOTE (critical): the recommendation value that gets stored in
 * `license_codes` must be a token the backend classifier can recognise when it
 * compares against a project's required licenses. The classifier
 * (`app/services/classifier.py::_extract_license_tokens` / `LICENSE_ALIASES`)
 * normalises a fixed set of license aliases into canonical codes:
 *
 *   SW001  ← 소프트웨어사업자 / SW사업자
 *   NET001 ← 정보통신공사업 / 정보통신공사
 *   ENG001 ← 엔지니어링사업 / 엔지니어링 / 기술사 / 감리
 *   SEC001 ← 정보보호전문서비스 / 보안관제 / ISMS
 *   ELE001 ← 전기공사업 / 전기
 *   FIRE001 ← 소방시설 / 소방
 *   ARC001 ← 건축공사업
 *   CIV001 ← 토목공사업
 *   CIVARC001 ← 토목건축공사업
 *   LND001 ← 조경공사업
 *   ENV001 ← 산업환경설비공사업
 *   INT001 ← 실내건축공사업
 *   MEC001 ← 기계설비공사업
 *   GAS001 ← 가스시설공사업
 *   PORT001 ← 항만및해안 / 항만및해안기술사 / 항만설계 / 해안기술사
 *   MAR001 ← 해양엔지니어링 / 해양기술사
 *   HYDRO001 ← 수로조사 / 수로측량 / 해양조사
 *
 * Plus any raw `[A-Z]{2,}\d{2,}` code (e.g. typing a code directly). As of the
 * construction-license-matching work the backend alias table now covers the
 * eight common construction licenses above, so every chip we surface here
 * drives profile↔project license matching. The `matchable` flag is retained on
 * the chip type for forward compatibility (in case a future chip is stored for
 * record-keeping only), but all currently curated chips are matchable.
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
  { label: "정보보호전문서비스", value: "정보보호전문서비스", matchable: true },
  // Construction licenses — now recognised by the backend classifier
  // (LICENSE_ALIASES → ARC001/CIV001/CIVARC001/LND001/ENV001/INT001/MEC001/GAS001).
  { label: "건축공사업", value: "건축공사업", matchable: true },
  { label: "토목공사업", value: "토목공사업", matchable: true },
  { label: "토목건축공사업", value: "토목건축공사업", matchable: true },
  { label: "조경공사업", value: "조경공사업", matchable: true },
  { label: "산업·환경설비공사업", value: "산업환경설비공사업", matchable: true },
  { label: "실내건축공사업", value: "실내건축공사업", matchable: true },
  { label: "기계설비공사업", value: "기계설비공사업", matchable: true },
  { label: "가스시설공사업", value: "가스시설공사업", matchable: true },
  // 해양 엔지니어링·기술용역 면허 (해양엔지니어링협회 게이트, Phase 1).
  // 백엔드 LICENSE_ALIASES → PORT001/MAR001/HYDRO001.
  { label: "항만·해안 설계·기술", value: "항만및해안", matchable: true },
  { label: "해양엔지니어링·기술사", value: "해양엔지니어링", matchable: true },
  { label: "수로·해양조사", value: "수로조사", matchable: true }
];

/**
 * Reserved for licenses the classifier does not (yet) map. The construction
 * licenses that used to live here are now matchable (see above), so this list
 * is currently empty. Kept as an export so callers can keep distinguishing
 * record-only chips if any are reintroduced later.
 */
export const RECORD_ONLY_LICENSE_CHIPS: LicenseChip[] = [];

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
