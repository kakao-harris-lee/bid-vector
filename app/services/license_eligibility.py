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

가정이 틀렸을 때의 **영향 크기**(2026-07-20 코퍼스 실측, license_limits 보유
591건): 그룹 수는 1개 430건(73%)·2개 126·3개 26·4개 7·6개 2 이고, 그룹 내 면허
수는 1개 702그룹(88%)·2개 84·3개 5·4개 7·5개 2 다. 즉 **OR 가정은 73% 공고에서
무의미**(단일 그룹)하고 **AND 가정이 답을 바꾸는 공고는 76건(13%)뿐**이라, 가정이
틀려도 흔들리는 범위는 좁다.

다만 위 실측 샘플은 **비전형**이다(그룹1 ⊂ 그룹2 형태는 다중 그룹 161건 중
20건, 12%). 그리고 그 형태에는 논리적 긴장이 있다 — OR 로 읽으면 그룹1만으로
통과되어 더 엄격한 그룹2가 잉여가 되고, AND 로 읽으면 그룹1이 그룹2에 흡수되어
잉여가 된다. 어느 쪽으로 읽어도 한 그룹이 남는다. 이를 설명하는 **대안 해석**:
``lmtGrpNo`` 가 대안이 아니라 **공동수급체 구성원 슬롯**일 수 있다(그룹2 = 구성원
A 가 중간처리업, 구성원 B 가 수집·운반업). ``lmtSno`` 가 그룹을 가로질러 1,2,3 으로
이어지는 점과 수집·운반업 행에만 ``permsnIndstrytyList``(허용 결합 업종)가 붙은
점이 이 해석을 지지한다. 소비(wiring) 전에 KONEPS 문서로 확인할 값어치가 있다.

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

eligible 정밀도 (2026-07-21 ENG001 배제 정밀화): taxonomy 의 ENG001 별칭은 전부
포괄 substring("엔지니어링사업"·"엔지니어링"·"감리"·"기술사")이라 서로 다른
전문분야를 한 코드로 collapse 시켜 면허 대조 오탐을 냈다(2026-07-20 라이브 eligible
3건 중 1건 = "정보시스템 감리법인/6146" 서민금융진흥원 AI 플랫폼 감리가 프로필
"엔지니어링"과 ENG001 로 만난 명백한 오탐 — 해양 엔지니어링과 무관). ENG001 에는
전문분야를 구분하는 구체 별칭이 하나도 없어(코드 자체 외 전부 포괄어)
:data:`IMPRECISE_MATCH_CODES` 로 이 코드를 면허 대조 키에서 배제했다. 이제 그런
이름들은 원문 정규화 키로 비교돼 전문분야가 구분된다(예: "엔지니어링사업(해양)" ↔
"엔지니어링사업(전기설비)" ↔ "정보시스템 감리법인" 은 더 이상 서로/프로필과
매칭되지 않는다). taxonomy 는 건드리지 않아 추천·게이트 경로는 불변이다.

남은 한계(정밀도 **여전히 미검증**): (1) 실제 엔지니어링 면허의 전문분야 적합성은
코드가 단정할 수 없어 **도메인 확인 필요**하다("건설엔지니어링업(종합)/4966" 등).
(2) 일부 구체 코드는 하위 종목을 아직 collapse 한다 — HYDRO001 은 "해양조사"·
"수로조사"·"수로측량" 을 공유해 "해양조사정보업(수로측량업/해도제작업/해양관측업)"
을 구분하지 못한다(현재 운영자는 셋 다 보유해 오탐으로 드러나지 않음). 이 한계는
:data:`ELIGIBLE_PRECISION_CAVEAT` 로 리포트 출력에도 함께 고지된다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.single_user import split_multi_value_text
from app.services.classification.text import extract_license_tokens
from app.services.eligibility_labeling import LICENSE_LIMITS_KEY

__all__ = [
    "GROUP_SEMANTICS_ASSUMPTION",
    "ELIGIBLE_PRECISION_CAVEAT",
    "IMPRECISE_MATCH_CODES",
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
    "license_limit_names",
    "assess_license_eligibility",
]

# 그룹 의미론 가정 고지 — 코드/리포트가 같은 문구를 쓰도록 단일 출처로 선언한다.
GROUP_SEMANTICS_ASSUMPTION = (
    "가정 — 실데이터로 재검증 필요: lmtGrpNo 그룹 간 = 대안(OR), 그룹 내 = 모두 필요(AND). "
    "실측 샘플 형태에 기반한 해석이며 공동수급체 구성은 고려하지 않는다(단독 사업자 기준)."
)

# eligible 정밀도 한계 고지 — 코드/리포트가 같은 문구를 쓰도록 단일 출처로 선언한다.
# 이 리포트가 wiring 여부를 결정하는 산출물이므로, 출력만 보는 사람이 eligible 을
# 검증된 자격 신호로 오독하지 않도록 stdout 에도 반드시 함께 노출한다(§2 정직).
ELIGIBLE_PRECISION_CAVEAT = (
    "주의 — eligible 정밀도 부분 검증: 과거 오탐(정보시스템 감리법인 — 포괄 별칭 '감리' 경로)은 "
    "ENG001 포괄 별칭을 면허 대조 키에서 배제(IMPRECISE_MATCH_CODES)해 제거했다. "
    "다만 실제 엔지니어링 면허의 전문분야 적합성은 도메인 확인 필요이고, 일부 코드(HYDRO001 해양조사 계열)는 "
    "하위 종목을 collapse 할 수 있어 정밀도는 아직 미검증이다."
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

# 면허 대조에서 배제하는 canonical 코드 (2026-07-21 실측 정밀화 — 규칙=데이터).
#
# taxonomy 의 ENG001 별칭은 전부 포괄 substring("엔지니어링사업"·"엔지니어링"·
# "감리"·"기술사")이라 서로 다른 전문분야를 한 코드로 collapse 시켜 면허 대조
# 오탐을 낸다: "엔지니어링사업(해양)"·"엔지니어링사업(전기설비)"·"정보시스템 감리법인"
# 이 모두 ENG001 로 뭉쳐 프로필 "엔지니어링"과 매칭됐다(2026-07-20 라이브 eligible
# 3건 중 1건 = AI 플랫폼 감리 용역의 명백한 오탐). ENG001 에는 전문분야를 구분하는
# 구체 별칭이 하나도 없어(코드 자체 "ENG001" 외 전부 포괄어) 이 코드를 대조 키에서
# 빼면 해당 이름들이 원문 정규화 키로 비교돼 전문분야가 구분된다.
#
# taxonomy 에서 별칭을 지우지 않고 이 모듈에서만 배제하는 이유: extract_license_tokens
# 는 **살아있는 추천 경로**(classification/semantic.py 의 역량요약 임베딩 텍스트)와
# marine 게이트 정규화(scripts/seed_marine_gate.py)가 공유하므로, taxonomy 를 건드리면
# 면허 대조와 무관한 추천 점수·게이트 동작이 함께 바뀐다. 정밀화는 면허 대조 축에서만
# 필요하므로 배제 규칙을 이 모듈 로컬 데이터로 둔다.
IMPRECISE_MATCH_CODES = frozenset({"ENG001"})

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
    # 면허명을 읽지 못해 요건에서 빠진 license_limits 행 수. 전부 못 읽으면
    # unknown 이지만, 일부만 못 읽으면 판정은 **줄어든 요건**으로 내려진다
    # (요건이 빠지는 방향 = eligible 쪽으로 관대해짐)는 사실을 노출한다.
    unparsable_rows: int = 0

    @property
    def has_eligibility_data(self) -> bool:
        """판정 근거가 되는 면허요건 데이터가 있었는지."""
        return bool(self.required_any)

    @property
    def has_unparsable_rows(self) -> bool:
        """면허명을 읽지 못한 행이 하나라도 있었는지(판정 신뢰도 저하 신호)."""
        return self.unparsable_rows > 0

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
            "unparsable_rows": self.unparsable_rows,
            "group_semantics_assumption": GROUP_SEMANTICS_ASSUMPTION,
            "eligible_precision_caveat": ELIGIBLE_PRECISION_CAVEAT,
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


def _matchable_license_codes(text: str | None) -> set[str]:
    """면허 대조용 canonical 코드 — 포괄 코드(:data:`IMPRECISE_MATCH_CODES`)는 배제.

    classifier 의 :func:`extract_license_tokens` 로 명시 코드·구체 별칭을 뽑되,
    전문분야를 collapse 시키는 포괄 코드(ENG001)는 제거한다. 배제로 코드가 비면
    호출부가 원문 정규화 키로 비교하므로(전문분야 구분 보존) 배제된 이름이 대조에서
    사라지지 않는다. taxonomy 자체는 건드리지 않아 추천/게이트 경로는 불변이다.
    """
    return (
        set(extract_license_tokens(text, require_context=False)) - IMPRECISE_MATCH_CODES
    )


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

    codes = _matchable_license_codes(text)
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

    keys: set[str] = _matchable_license_codes(license_codes)
    for entry in split_multi_value_text(license_codes):
        normalized = normalize_license_key(entry)
        if normalized:
            keys.add(normalized)
    return frozenset(keys)


# --- 그룹 파싱 (순수) ---------------------------------------------------------


def _parse_rows(eligibility_raw: dict | None) -> tuple[list[LicenseGroup], int]:
    """면허제한 행을 그룹으로 묶고, 면허명을 읽지 못한 행 수를 함께 돌려준다.

    읽지 못한 행을 세는 이유: 조용히 버리면 요건이 줄어들어 판정이 eligible 쪽으로
    관대해지는데, 그 사실이 결과에서 보이지 않는다. 호출부가 신뢰도를 판단할 수
    있도록 개수를 노출한다.
    """
    if not isinstance(eligibility_raw, dict):
        return [], 0

    rows = eligibility_raw.get(LICENSE_LIMITS_KEY)
    if not isinstance(rows, list):
        return [], 0

    grouped: dict[str, list[LicenseRequirement]] = {}
    unparsable = 0
    for row in rows:
        if not isinstance(row, dict):
            unparsable += 1
            continue
        requirement = _requirement_from_name(str(row.get(LICENSE_NAME_FIELD) or ""))
        if requirement is None:
            unparsable += 1
            continue
        group_no = str(row.get(GROUP_NO_FIELD) or "").strip() or UNGROUPED_KEY
        grouped.setdefault(group_no, []).append(requirement)

    groups = [
        LicenseGroup(group_no=group_no, requirements=tuple(requirements))
        for group_no, requirements in grouped.items()
        if requirements
    ]
    return groups, unparsable


def parse_license_limit_groups(eligibility_raw: dict | None) -> list[LicenseGroup]:
    """``eligibility_raw`` 의 면허제한 행을 ``lmtGrpNo`` 기준 그룹으로 묶는다.

    그룹 번호가 비었거나 없는 행은 :data:`UNGROUPED_KEY` 하나로 모여 단일 그룹
    (AND)으로 취급된다. 그룹 순서는 처음 등장 순서를 유지한다. IO 접근 없음.
    """
    return _parse_rows(eligibility_raw)[0]


def license_limit_names(eligibility_raw: dict | None) -> tuple[str, ...]:
    """``license_limits`` 의 면허명(코드 접미 제거)을 등장순·중복 제거로 돌려준다.

    표시/프로필 저장용 소비자(예: 온보딩 후보 역추천)를 위한 얇은 표면. 그룹
    의미론(OR/AND)과 무관하게 요건 면허명만 평탄화해 낸다. 코드 접미("/1253")
    제거·표시명 산출은 :func:`parse_license_limit_groups` 의 corpus-anchored
    파서를 재사용하므로 단일 출처다(복붙 없음).

    **왜 canonical 코드가 아니라 면허명인가**: ``taxonomy`` 의 ENG001 별칭은
    포괄 substring 이라 서로 다른 전문분야를 한 코드로 collapse 시키고
    (``엔지니어링사업(해양)``·``(전기설비)`` → ENG001), HYDRO001 도 해양조사
    하위 종목을 collapse 한다. 면허명(표시명)은 이 전문분야 구분을 보존하며,
    ``CompanyProfile.license_codes`` 가 실제로 저장하는 값 공간(한글 면허명)과
    ``assess_license_eligibility`` 의 원문 정규화 키 비교 경로에 정합한다.
    IO 접근 없음.
    """
    names: list[str] = []
    seen: set[str] = set()
    for group in parse_license_limit_groups(eligibility_raw):
        for requirement in group.requirements:
            if requirement.name and requirement.name not in seen:
                seen.add(requirement.name)
                names.append(requirement.name)
    return tuple(names)


# --- 판정 (순수) -------------------------------------------------------------


def _required_any(groups: list[LicenseGroup]) -> tuple[str, ...]:
    """모든 그룹의 요구 면허명을 등장 순서대로 중복 없이 모은다."""
    names: list[str] = []
    for group in groups:
        for name in group.names():
            if name not in names:
                names.append(name)
    return tuple(names)


def _unknown(
    evidence: str,
    required_any: tuple[str, ...] = (),
    unparsable_rows: int = 0,
) -> LicenseEligibility:
    """판정 불가 결과를 만든다(자격 데이터 부재 또는 보유 면허 미기재)."""
    return LicenseEligibility(
        verdict=VERDICT_UNKNOWN,
        matched_groups=(),
        missing_by_group={},
        required_any=required_any,
        evidence=(evidence,),
        unparsable_rows=unparsable_rows,
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
    groups, unparsable_rows = _parse_rows(eligibility_raw)
    if not groups:
        has_rows = isinstance(eligibility_raw, dict) and bool(
            eligibility_raw.get(LICENSE_LIMITS_KEY)
        )
        return _unknown(
            _UNPARSABLE_EVIDENCE if has_rows else _NO_DATA_EVIDENCE,
            unparsable_rows=unparsable_rows,
        )

    required_any = _required_any(groups)
    profile_keys = profile_license_keys(profile_license_codes)
    if not profile_keys:
        # 보유 면허 미기재는 "미보유"가 아니라 데이터 공백이다.
        return _unknown(_NO_PROFILE_EVIDENCE, required_any, unparsable_rows)

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

    if unparsable_rows:
        evidence.append(f"면허명을 읽지 못한 행 {unparsable_rows}건이 요건에서 빠졌다 — 판정이 관대할 수 있음")

    return LicenseEligibility(
        verdict=VERDICT_ELIGIBLE if matched_groups else VERDICT_INELIGIBLE,
        matched_groups=tuple(matched_groups),
        missing_by_group=missing_by_group,
        required_any=required_any,
        evidence=tuple(evidence),
        unparsable_rows=unparsable_rows,
    )
