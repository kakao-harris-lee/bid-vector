"""운영자 보유 면허 ↔ 공고 면허요건(license_limits) 매칭 (순수 해석기).

``Project.eligibility_raw["license_limits"]`` 의 면허제한 행과 ``CompanyProfile.
license_codes`` 를 대조해 **"이 공고에 우리가 참가 자격이 있는가"** 를 판정한다.

``eligibility_raw`` 구조(실측 2026-07-19, backfill 스크립트가 유일 writer)::

    {"flags": {...}, "license_limits": [
        {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1", "lmtSno": "1"},
        {"lcnsLmtNm": "건설폐기물 수집·운반업/6728",
         "permsnIndstrytyList": "[건설폐기물 중간처리업/1253]",
         "lmtGrpNo": "2", "lmtSno": "3"}, ...]}

면허명 형식은 ``면허명/코드`` 다.

그룹 의미론 (**해석 가정 — 실데이터로 재검증 필요**):

- **그룹 간 = 대안(OR)**: ``lmtGrpNo`` 가 다른 그룹 중 **하나만** 충족하면 자격 있음.
- **그룹 내 = 모두 필요(AND)**: 같은 ``lmtGrpNo`` 행의 면허를 **전부** 보유해야 함.

근거는 실측 샘플(그룹1 = 중간처리업 단독 / 그룹2 = 중간처리업 + 수집·운반업)의
형태뿐이며 KONEPS 공식 정의로 확인한 것이 아니다. 이 가정은
:data:`GROUP_SEMANTICS_ASSUMPTION` 로 노출되어 리포트 출력에도 함께 고지된다.
또한 **단독 사업자 기준**이라 공동수급체(컨소시엄) 구성으로 요건을 나눠 갖는
경우는 고려하지 않는다.

설계 원칙:

- 순수 함수 — IO/DB/네트워크 접근 없음. 이 모듈에는 **소비자가 없다**(분류/추천/
  수집 미반영). wiring 여부는 후속 PR 에서 영향 리포트 수치로 결정한다.
- 면허명 정규화는 classifier 의 :func:`extract_license_tokens`
  (``taxonomy.LICENSE_ALIASES``)를 **재사용**한다. 새 별칭 테이블을 만들지 않는다.
- 별칭 미등재 면허명은 정규화 실패로 **버리지 않고** 원문 정규화 키로 보존해
  비교한다. 미등재를 "요건 없음"으로 처리하면 ineligible 이 eligible 로 뒤집히는
  오판이 생기기 때문이다(별칭 테이블은 해양 세그먼트 중심이라 일반 면허는 대부분
  미등재). 원문 키는 문자열 동치 비교라 보수적으로 동작한다.
- §2 정직: 모르는 것을 판정으로 만들지 않는다. 자격 데이터가 없거나 보유 면허
  정보가 없으면 :data:`VERDICT_UNKNOWN` 이며 **ineligible 로 취급하지 않는다**.

알려진 한계 (**eligible 정밀도 미검증 — wiring 전 선결 과제**): taxonomy 의 포괄
별칭(ENG001 의 bare "엔지니어링"·"감리" 등)이 무관한 면허를 매칭시킨다. 예컨대
공고의 "정보시스템 감리법인" 이 프로필의 "엔지니어링" 과 ENG001 으로 만나
eligible 이 된다. 2026-07-20 라이브 리포트의 eligible 3건은 전부 이 경로였다.
과매칭은 eligible 을 관대하게 만드는 방향이라 "ineligible 은 보수적"이라는 성질은
유지되지만, eligible 을 신호로 쓰려면 별칭 정밀화가 먼저 필요하다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.single_user import split_multi_value_text
from app.services.classification.text import extract_license_tokens
from app.services.eligibility_labeling import LICENSE_LIMITS_KEY

__all__ = [
    "GROUP_SEMANTICS_ASSUMPTION",
    "LICENSE_NAME_FIELD",
    "GROUP_NO_FIELD",
    "UNGROUPED_KEY",
    "VERDICT_ELIGIBLE",
    "VERDICT_INELIGIBLE",
    "VERDICT_UNKNOWN",
    "VERDICT_VALUES",
    "LicenseRequirement",
    "LicenseGroup",
    "LicenseEligibility",
    "normalize_license_key",
    "profile_license_keys",
    "parse_license_limit_groups",
    "assess_license_eligibility",
]

# 그룹 의미론 가정 고지 — 코드/리포트가 같은 문구를 쓰도록 단일 출처로 선언한다.
GROUP_SEMANTICS_ASSUMPTION = (
    "가정 — 실데이터로 재검증 필요: lmtGrpNo 그룹 간 = 대안(OR), 그룹 내 = 모두 필요(AND). "
    "실측 샘플 형태에 기반한 해석이며 공동수급체 구성은 고려하지 않는다(단독 사업자 기준)."
)

# license_limits 행에서 읽는 필드 (매직값 금지 §4.5.1 — eligibility_labeling 과
# 같은 원문 스키마를 참조한다).
LICENSE_NAME_FIELD = "lcnsLmtNm"
GROUP_NO_FIELD = "lmtGrpNo"

# lmtGrpNo 가 없는 행들이 모이는 그룹 키. 결측 행은 하나의 그룹으로 묶여
# AND(전부 필요)로 해석된다 — OR 로 흩는 것보다 보수적이다.
UNGROUPED_KEY = "-"

# 판정 값. unknown 은 "자격 데이터 없음/보유 면허 정보 없음"이며 ineligible 과
# 엄격히 구분된다(§2 정직).
VERDICT_ELIGIBLE = "eligible"
VERDICT_INELIGIBLE = "ineligible"
VERDICT_UNKNOWN = "unknown"
VERDICT_VALUES = (VERDICT_ELIGIBLE, VERDICT_INELIGIBLE, VERDICT_UNKNOWN)

# 면허명 뒤에 붙는 KONEPS 숫자 코드 접미("/1253")를 떼기 위한 패턴.
_CODE_SUFFIX_RE = re.compile(r"/\s*\d+\s*$")
# 원문 정규화 키에서 제거하는 구분 문자(공백·가운뎃점·괄호·문장부호).
_KEY_NOISE_RE = re.compile(r"[\s·・‧⋅,.\-_/()\[\]（）]+")

# unknown 사유 문구(고정 근거).
_NO_DATA_EVIDENCE = "면허요건(license_limits) 데이터 없음 — 판정 불가"
_UNPARSABLE_EVIDENCE = "면허요건 행에서 면허명을 읽지 못함 — 판정 불가"
_NO_PROFILE_EVIDENCE = "보유 면허 정보 없음 — 판정 불가(미보유가 아니라 미기재)"


@dataclass(frozen=True)
class LicenseRequirement:
    """공고가 요구하는 면허 하나 — 원문 + 비교 키 집합."""

    raw: str  # 원문 그대로 ("건설폐기물 중간처리업/1253")
    name: str  # 코드 접미를 뗀 표시용 이름 ("건설폐기물 중간처리업")
    keys: frozenset[str]  # 비교 키 (canonical 코드 집합, 없으면 원문 정규화 키)
    alias_mapped: bool  # taxonomy 별칭으로 정규화됐는지(원문 키 fallback 구분)

    def is_held_by(self, profile_keys: frozenset[str]) -> bool:
        """프로필 키 집합이 이 면허 요건을 충족하는지(모든 키 보유).

        ``keys`` 는 같은 면허명 하나에서 나온 표현들이라 부분 보유는 충족이
        아니다(기존 ``assess_license`` 의 subset 의미론과 동일).
        """
        return bool(self.keys) and self.keys <= profile_keys


@dataclass(frozen=True)
class LicenseGroup:
    """``lmtGrpNo`` 로 묶인 면허 요건 그룹 — 그룹 내는 AND, 그룹 간은 OR."""

    group_no: str
    requirements: tuple[LicenseRequirement, ...]

    def missing(self, profile_keys: frozenset[str]) -> tuple[str, ...]:
        """이 그룹에서 프로필이 충족하지 못한 면허명 목록(표시용, 중복 제거)."""
        missing: list[str] = []
        for requirement in self.requirements:
            if requirement.is_held_by(profile_keys):
                continue
            if requirement.name not in missing:
                missing.append(requirement.name)
        return tuple(missing)

    def names(self) -> tuple[str, ...]:
        """그룹이 요구하는 면허명 목록(표시용, 중복 제거)."""
        names: list[str] = []
        for requirement in self.requirements:
            if requirement.name not in names:
                names.append(requirement.name)
        return tuple(names)


@dataclass(frozen=True)
class LicenseEligibility:
    """면허 자격 판정 결과 + 근거(§2 정직: 판정 사유를 감사 가능하게 남긴다)."""

    verdict: str  # VERDICT_ELIGIBLE | VERDICT_INELIGIBLE | VERDICT_UNKNOWN
    matched_groups: tuple[str, ...]  # 충족한 그룹 번호
    missing_by_group: dict[str, tuple[str, ...]]  # 그룹 번호 → 부족 면허명
    required_any: tuple[str, ...]  # 요구 면허명 전체(그룹 간 OR 이므로 "이 중")
    evidence: tuple[str, ...]

    @property
    def has_eligibility_data(self) -> bool:
        """판정 근거가 되는 면허요건 데이터가 있었는지."""
        return bool(self.required_any)

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "matched_groups": list(self.matched_groups),
            "missing_by_group": {
                group_no: list(names)
                for group_no, names in self.missing_by_group.items()
            },
            "required_any": list(self.required_any),
            "evidence": list(self.evidence),
            "group_semantics_assumption": GROUP_SEMANTICS_ASSUMPTION,
        }


# --- 정규화 (순수) -----------------------------------------------------------


def normalize_license_key(value: str) -> str:
    """별칭 미등재 면허명을 비교용 원문 키로 정규화한다.

    KONEPS 코드 접미("/1253")를 떼고 공백·가운뎃점·괄호 등 구분 문자를 제거한 뒤
    소문자화한다. 공고 쪽과 프로필 쪽에 같은 정규화를 적용해 문자열 동치로만
    비교하므로(별칭 확장 없음) 보수적으로 동작한다.
    """
    without_code = _CODE_SUFFIX_RE.sub("", value.strip())
    return _KEY_NOISE_RE.sub("", without_code).lower()


def _display_name(value: str) -> str:
    """면허명에서 코드 접미를 뗀 표시용 이름."""
    return _CODE_SUFFIX_RE.sub("", value.strip()).strip()


def _requirement_from_name(raw_name: str) -> LicenseRequirement | None:
    """면허명 원문 하나를 비교 가능한 요건으로 변환한다(빈 문자열이면 None).

    taxonomy 별칭으로 canonical 코드가 나오면 그것을 키로 쓰고, 나오지 않으면
    원문 정규화 키로 보존한다(미등재를 "요건 없음"으로 흘리지 않기 위함).
    공고 면허명은 이미 면허 필드이므로 ``require_context=False`` 로 추출한다.
    """
    text = raw_name.strip()
    if not text:
        return None

    codes = extract_license_tokens(text, require_context=False)
    if codes:
        return LicenseRequirement(
            raw=text,
            name=_display_name(text),
            keys=frozenset(codes),
            alias_mapped=True,
        )

    fallback_key = normalize_license_key(text)
    if not fallback_key:
        return None
    return LicenseRequirement(
        raw=text,
        name=_display_name(text),
        keys=frozenset({fallback_key}),
        alias_mapped=False,
    )


def profile_license_keys(license_codes: str | None) -> frozenset[str]:
    """운영자 보유 면허 문자열을 비교 키 집합으로 변환한다.

    canonical 코드(별칭 매칭)와 항목별 원문 정규화 키를 **모두** 담는다. 원문 키를
    함께 담아야 별칭 미등재 면허를 프로필이 실제로 보유한 경우 매칭된다.
    """
    if not license_codes or not license_codes.strip():
        return frozenset()

    keys: set[str] = set(extract_license_tokens(license_codes))
    for entry in split_multi_value_text(license_codes):
        normalized = normalize_license_key(entry)
        if normalized:
            keys.add(normalized)
    return frozenset(keys)


# --- 그룹 파싱 (순수) ---------------------------------------------------------


def parse_license_limit_groups(eligibility_raw: dict | None) -> list[LicenseGroup]:
    """``eligibility_raw`` 의 면허제한 행을 ``lmtGrpNo`` 기준 그룹으로 묶는다.

    그룹 번호가 비었거나 없는 행은 :data:`UNGROUPED_KEY` 하나로 모여 단일 그룹
    (AND)으로 취급된다. 그룹 순서는 처음 등장 순서를 유지한다. IO 접근 없음.
    """
    if not isinstance(eligibility_raw, dict):
        return []

    rows = eligibility_raw.get(LICENSE_LIMITS_KEY)
    if not isinstance(rows, list):
        return []

    grouped: dict[str, list[LicenseRequirement]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        requirement = _requirement_from_name(str(row.get(LICENSE_NAME_FIELD) or ""))
        if requirement is None:
            continue
        group_no = str(row.get(GROUP_NO_FIELD) or "").strip() or UNGROUPED_KEY
        grouped.setdefault(group_no, []).append(requirement)

    return [
        LicenseGroup(group_no=group_no, requirements=tuple(requirements))
        for group_no, requirements in grouped.items()
        if requirements
    ]


# --- 판정 (순수) -------------------------------------------------------------


def _required_any(groups: list[LicenseGroup]) -> tuple[str, ...]:
    """모든 그룹의 요구 면허명을 등장 순서대로 중복 없이 모은다."""
    names: list[str] = []
    for group in groups:
        for name in group.names():
            if name not in names:
                names.append(name)
    return tuple(names)


def _unknown(evidence: str, required_any: tuple[str, ...] = ()) -> LicenseEligibility:
    """판정 불가 결과를 만든다(자격 데이터 부재 또는 보유 면허 미기재)."""
    return LicenseEligibility(
        verdict=VERDICT_UNKNOWN,
        matched_groups=(),
        missing_by_group={},
        required_any=required_any,
        evidence=(evidence,),
    )


def assess_license_eligibility(
    eligibility_raw: dict | None,
    profile_license_codes: str | None,
) -> LicenseEligibility:
    """공고 면허요건과 운영자 보유 면허를 대조해 참가 자격을 판정한다(순수 함수).

    - ``eligible``: 최소 한 그룹의 요구 면허를 프로필이 전부 보유(그룹 간 OR).
    - ``ineligible``: 자격 데이터가 있고 어느 그룹도 충족하지 못함.
    - ``unknown``: 면허요건 데이터 부재/파싱 불가, 또는 보유 면허 정보 부재.
      **절대 ineligible 로 취급하지 않는다**(§2 정직).

    그룹 의미론은 :data:`GROUP_SEMANTICS_ASSUMPTION` 의 해석 가정을 따른다.
    IO/DB 접근 없음.
    """
    groups = parse_license_limit_groups(eligibility_raw)
    if not groups:
        has_rows = isinstance(eligibility_raw, dict) and bool(
            eligibility_raw.get(LICENSE_LIMITS_KEY)
        )
        return _unknown(_UNPARSABLE_EVIDENCE if has_rows else _NO_DATA_EVIDENCE)

    required_any = _required_any(groups)
    profile_keys = profile_license_keys(profile_license_codes)
    if not profile_keys:
        # 보유 면허 미기재는 "미보유"가 아니라 데이터 공백이다.
        return _unknown(_NO_PROFILE_EVIDENCE, required_any)

    matched_groups: list[str] = []
    missing_by_group: dict[str, tuple[str, ...]] = {}
    evidence: list[str] = []
    for group in groups:
        missing = group.missing(profile_keys)
        if missing:
            missing_by_group[group.group_no] = missing
            evidence.append(
                f"그룹 {group.group_no} 미충족 — 요구: {', '.join(group.names())}"
                f" / 누락: {', '.join(missing)}"
            )
            continue
        matched_groups.append(group.group_no)
        evidence.append(f"그룹 {group.group_no} 충족 — 요구: {', '.join(group.names())}")

    return LicenseEligibility(
        verdict=VERDICT_ELIGIBLE if matched_groups else VERDICT_INELIGIBLE,
        matched_groups=tuple(matched_groups),
        missing_by_group=missing_by_group,
        required_any=required_any,
        evidence=tuple(evidence),
    )
