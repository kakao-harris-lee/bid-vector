"""협회/엔지니어링 자격조건 룰 기반 라벨 추출 (순수 해석기).

``Project.eligibility_raw`` 구조(backfill 스크립트가 유일 writer, 실측 2026-07-19):
``{"flags": {...참가자격 플래그...}, "license_limits": [{"lcnsLmtNm": ...,
"permsnIndstrytyList": ..., "lmtGrpNo": ..., "lmtSno": ...}, ...]}``. 자격 판정
소스는 ``license_limits`` 행의 ``lcnsLmtNm``(면허제한 한글 원문)이며,
``permsnIndstrytyList``(허용 업종 목록)는 비어있지 않을 때 함께 본다. flags는
Y/N 플래그라 라벨 근거가 아니고 ``has_eligibility_data`` 존재 판정에만 쓰인다.
로드맵(docs/roadmap.md 216·227행)이 요구하는 구조화 자격 라벨을 뽑는다.

설계 원칙:
- 규칙은 **선언 데이터**(아래 상수 테이블), 코드는 **해석기**만 유지한다(§4.5.3).
  테이블이 커지면 YAML/DSL descriptor + 얇은 로더로 승격한다.
- 순수 함수 — IO/DB/네트워크 접근 없음. 저장·추천 반영은 후속 PR의 의도적 결정
  이며 이 모듈에는 소비자가 없다(on-demand 계산만).
- provenance 필수(§2 정직): 어느 필드의 어떤 원문 조각이 어떤 용어로 매칭됐는지
  ``matches`` 로 남긴다. ``license_limits.lcnsLmtNm``·``license_limits.
  permsnIndstrytyList`` 는 **참가자격 맥락**이므로 매칭을 자격 조건으로 간주하지만,
  ``title`` 매칭은 기관명/과업명 오탐 축이라 참고 증거로만 기록하고
  (``source_field="title"``) bool/tech_fields 판정에는 넣지 않는다.
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "FLAG_ASSOCIATION",
    "FLAG_ENGINEERING_BUSINESS",
    "FLAGS_KEY",
    "LICENSE_LIMITS_KEY",
    "LICENSE_LIMIT_SOURCE_FIELDS",
    "LABEL_SOURCE_FIELDS",
    "TITLE_SOURCE",
    "LABEL_POSITIVE",
    "LABEL_NEGATIVE",
    "LABEL_AMBIGUOUS",
    "LABEL_VALUES",
    "SOURCE_RULE",
    "SOURCE_LLM",
    "SOURCE_OPERATOR",
    "LABEL_SOURCES",
    "LABELER_VERSION",
    "VERDICT_FIT",
    "VERDICT_UNFIT",
    "VERDICT_HOLD",
    "OPERATOR_VERDICT_TO_LABEL",
    "OPERATOR_VERDICTS",
    "OPERATOR_LABELER_VERSION",
    "QualificationTerm",
    "TechField",
    "ASSOCIATION_QUALIFICATION_TERMS",
    "ASSOCIATION_MEMBERSHIP_CANONICALS",
    "TECH_FIELD_TERMS",
    "match_association_terms",
    "required_association_memberships",
    "EligibilityMatch",
    "EligibilityLabels",
    "EligibilityVerdict",
    "extract_eligibility_labels",
    "classify_eligibility",
]

# 자격 라벨 flag 종류 — 어떤 bool 라벨을 켜는지 선언에 쓴다.
FLAG_ASSOCIATION = "association"
FLAG_ENGINEERING_BUSINESS = "engineering_business"

# 영속화 라벨 값(§2 정직): positive=협회/엔지니어링 자격 조건이 참가자격에 명시,
# negative=자격 데이터가 있고 해당 조건 없음, ambiguous=자격 데이터 부재/판정 불가.
# 매직값 금지(§4.5.1) — 라벨/소스 값의 단일 출처이며 모델·스크립트·리포트가 참조한다.
LABEL_POSITIVE = "positive"
LABEL_NEGATIVE = "negative"
LABEL_AMBIGUOUS = "ambiguous"
LABEL_VALUES = (LABEL_POSITIVE, LABEL_NEGATIVE, LABEL_AMBIGUOUS)

# 라벨 소스: rule=이 룰 해석기, llm=후속 LLM 하이브리드, operator=운영자 정답.
# 소스별 1라벨(NoticeEligibilityLabel 의 (project_id, source) 유니크)이며 이 PR 은
# rule 만 생성한다(llm/operator 는 후속 단계에서 채운다).
SOURCE_RULE = "rule"
SOURCE_LLM = "llm"
SOURCE_OPERATOR = "operator"
LABEL_SOURCES = (SOURCE_RULE, SOURCE_LLM, SOURCE_OPERATOR)

# 룰 라벨러 버전 — 규칙 테이블/매핑 로직이 바뀌면 올려 라벨 재생성을 추적한다.
# rule-v2: TECH_FIELD_TERMS 에 해양 엔지니어링/기술사 면허 용어 추가(캘리브레이션,
# #207 후속). 라벨 재생성 시 버전으로 갱신분을 추적한다.
LABELER_VERSION = "rule-v2"

# 운영자 식별 피드백 verdict(UI 한글) → 저장 라벨 매핑(§4.5.2 룩업 디스패치).
# operator 소스 라벨의 어휘 단일 출처(매직값 금지 §4.5.1) — 스키마 검증과 저장
# 서비스(eligibility_feedback)가 이 매핑을 공유한다. 적합→positive / 부적합→
# negative / 보류→ambiguous 로 rule/llm 라벨과 같은 값 집합에 정렬된다.
VERDICT_FIT = "적합"
VERDICT_UNFIT = "부적합"
VERDICT_HOLD = "보류"
OPERATOR_VERDICT_TO_LABEL: dict[str, str] = {
    VERDICT_FIT: LABEL_POSITIVE,
    VERDICT_UNFIT: LABEL_NEGATIVE,
    VERDICT_HOLD: LABEL_AMBIGUOUS,
}
# 허용 verdict 값 집합(스키마 검증의 단일 출처). 매핑 키에서 파생해 드리프트 방지.
OPERATOR_VERDICTS: tuple[str, ...] = tuple(OPERATOR_VERDICT_TO_LABEL)

# operator 라벨러 버전 — 운영자 피드백 캡처 경로 버전 추적(라벨 provenance).
OPERATOR_LABELER_VERSION = "operator-v1"

# eligibility_raw 구조 키(openapi.build_eligibility_raw 와 단일 출처).
FLAGS_KEY = "flags"
LICENSE_LIMITS_KEY = "license_limits"

# ``license_limits`` 행에서 자격 라벨의 **판정 근거로 삼는** 필드. lcnsLmtNm(면허
# 제한 원문)이 주 소스이고, permsnIndstrytyList(허용 업종 목록)는 비면 함께 본다.
# lmtGrpNo/lmtSno 는 식별자라 매칭 소스에서 제외한다.
LICENSE_LIMIT_SOURCE_FIELDS = ("lcnsLmtNm", "permsnIndstrytyList")

# 매칭 provenance 의 source_field 값(리포트의 eligibility-hit 판정과 단일 출처).
# ``license_limits.lcnsLmtNm`` / ``license_limits.permsnIndstrytyList`` 형태.
LABEL_SOURCE_FIELDS = tuple(
    f"{LICENSE_LIMITS_KEY}.{field}" for field in LICENSE_LIMIT_SOURCE_FIELDS
)

# title 은 참고 증거로만 기록하는 별도 소스(오탐 축 분리).
TITLE_SOURCE = "title"

# evidence 원문은 길 수 있으므로 상한을 둔다(로그/리포트 가독성).
_EVIDENCE_MAX_CHARS = 160

_WHITESPACE_RE = re.compile(r"\s+")


# --- 선언 규칙 스키마 --------------------------------------------------------


@dataclass(frozen=True)
class QualificationTerm:
    """협회/엔지니어링사업자 등 자격 용어 하나(선언 규칙 행)."""

    canonical: str  # 표준 표기 (matches.term 에 그대로 노출)
    flag: str  # FLAG_ASSOCIATION | FLAG_ENGINEERING_BUSINESS
    variants: tuple[str, ...]  # 표면 변형(전체명칭 등). 공백/괄호는 정규화가 흡수.


@dataclass(frozen=True)
class TechField:
    """기술부문/전문분야 사전 한 행 — 표준명 + 코드 + 별칭."""

    name: str  # 표준명 (tech_fields 에 노출)
    code: str  # classifier LICENSE_ALIASES canonical 코드
    aliases: tuple[str, ...]


# --- 선언 규칙 테이블 (룰=데이터) --------------------------------------------
# 로드맵 216·227행이 명시한 자격 용어군. 각 용어는 어느 bool 라벨을 켜는지 flag
# 로 선언한다. variants 는 전체명칭 등 표면 변형이며, 추가 공백·괄호는
# ``_normalize`` 가 흡수하므로 조합을 나열하지 않는다.
ASSOCIATION_QUALIFICATION_TERMS: tuple[QualificationTerm, ...] = (
    # "엔지니어링협회" — 한국엔지니어링협회 가입 조건 (로드맵 227행 "엔지니어링협회").
    QualificationTerm(
        canonical="엔지니어링협회",
        flag=FLAG_ASSOCIATION,
        variants=("엔지니어링협회", "한국엔지니어링협회"),
    ),
    # "엔지니어링사업자" — 엔지니어링산업 진흥법상 신고 사업자 (로드맵 216·227행).
    QualificationTerm(
        canonical="엔지니어링사업자",
        flag=FLAG_ENGINEERING_BUSINESS,
        variants=("엔지니어링사업자",),
    ),
    # "엔지니어링 활동주체" — 같은 법상 활동주체 (로드맵 227행).
    QualificationTerm(
        canonical="엔지니어링활동주체",
        flag=FLAG_ENGINEERING_BUSINESS,
        variants=("엔지니어링활동주체",),
    ),
)

# 협회 **가입**(FLAG_ASSOCIATION) canonical 집합 — ``association_memberships`` 필드
# 의미와 정합하는 자격 용어만 담는다(엔지니어링사업자/활동주체 = 사업 신고라 제외).
# 온보딩 후보 제안(``onboarding.suggestions``)과 classifier 협회 축
# (``classification.association``)이 이 **단일 출처**를 공유한다(§4.5.1, 복붙 금지).
ASSOCIATION_MEMBERSHIP_CANONICALS: frozenset[str] = frozenset(
    term.canonical
    for term in ASSOCIATION_QUALIFICATION_TERMS
    if term.flag == FLAG_ASSOCIATION
)

# 기술부문·전문분야 사전. classifier ``LICENSE_ALIASES``(해양 기술용역 면허군
# PORT001/MAR001/HYDRO001, app/services/classification/taxonomy.py)와
# docs/marine-engineering-gate.md 3절에서 근거를 두고 시드했다. 실데이터(내일부터
# 적재)로 report 스크립트가 미매칭 원문을 surface 하면 여기에 출처를 달아
# 확장한다(추측 용어 남발 금지). 표준명은 해당 면허군의 대표 한글명이다.
#
# 별칭 정밀화 원칙(ENG001 포괄어 오탐 교훈): bare "엔지니어링"·"해양"·"항만"
# 같은 포괄어는 과매칭이라 넣지 않고, **전문분야가 붙은 구체 형태**만 둔다.
# ``_normalize`` 는 공백만 제거하고 괄호/쉼표/가운뎃점은 남기므로(paren-stripped
# "엔지니어링사업해양" 은 원문 "엔지니어링사업(해양)" 에 substring 으로 안 잡힌다),
# 코퍼스 실측 면허명은 **여는 괄호까지 포함한 앵커**로 선언한다. 여는 괄호 뒤
# 구분자(", " → 정규화 후 ","·"·")가 형태별로 달라도 앵커가 그 앞에서 끊겨
# ``엔지니어링사업(항만, 해안)``·``엔지니어링사업(항만·해안)`` 를 모두 잡고,
# 비해양 전문분야 ``엔지니어링사업(전기설비)``·``(정보통신)`` 은 앵커에 해양/항만
# 도메인어가 박혀 있어 오탐되지 않는다(회귀 가드 테스트로 고정).
TECH_FIELD_TERMS: tuple[TechField, ...] = (
    # PORT001: 항만및해안, 항만설계 (taxonomy.py:74, marine gate 3절).
    # ``엔지니어링사업(항만, 해안)`` (코퍼스 실측 면허 3579) 을 항만·해안
    # 엔지니어링으로 매핑한다. 여는 괄호 앵커라 comma/가운뎃점 구분자 무관.
    TechField(
        name="항만및해안",
        code="PORT001",
        aliases=("항만및해안", "항만설계", "엔지니어링사업(항만"),
    ),
    # MAR001: 해양엔지니어링 (taxonomy.py:75, marine gate 3절).
    # ``엔지니어링사업(해양)`` (코퍼스 실측 면허 3599) 을 해양엔지니어링으로 매핑.
    TechField(
        name="해양엔지니어링",
        code="MAR001",
        aliases=("해양엔지니어링", "엔지니어링사업(해양"),
    ),
    # MAR001(해양 기술용역군): ``기술사사무소(해양)`` (코퍼스 실측 면허 7383) —
    # 해양 기술사 사무소. 해양엔지니어링과 구분되는 표준명으로 provenance 를
    # 보존하되 면허군 코드는 MAR001(해양엔지니어링 계열)을 공유한다.
    TechField(
        name="해양기술사",
        code="MAR001",
        aliases=("기술사사무소(해양",),
    ),
    # HYDRO001: 수로조사/수로측량/해양조사 (taxonomy.py:76, marine gate 3절).
    # 코퍼스 ``해양조사정보업(수로측량업/…)`` 은 "수로측량"·"해양조사" 별칭이 흡수.
    TechField(
        name="수로조사",
        code="HYDRO001",
        aliases=("수로조사", "수로측량", "해양조사"),
    ),
)


# --- 결과 스키마 -------------------------------------------------------------


@dataclass(frozen=True)
class EligibilityMatch:
    """한 라벨 매칭의 provenance — 어느 필드의 어떤 원문이 어떤 용어로 잡혔나."""

    term: str
    source_field: str
    evidence: str


@dataclass(frozen=True)
class EligibilityLabels:
    """추출된 자격 라벨 + provenance. ``to_dict`` 로 직렬화한다."""

    association_required: bool
    engineering_business_required: bool
    tech_fields: tuple[str, ...]
    matches: tuple[EligibilityMatch, ...]
    has_eligibility_data: bool

    def to_dict(self) -> dict:
        return {
            "association_required": self.association_required,
            "engineering_business_required": self.engineering_business_required,
            "tech_fields": list(self.tech_fields),
            "matches": [
                {
                    "term": m.term,
                    "source_field": m.source_field,
                    "evidence": m.evidence,
                }
                for m in self.matches
            ],
            "has_eligibility_data": self.has_eligibility_data,
        }


# --- 해석기 (순수) -----------------------------------------------------------


def _normalize(text: str) -> str:
    """매칭용 정규화 — 공백 제거 + 소문자.

    공백을 제거하므로 "엔지니어링 협회"·"엔지니어링 사업자" 같은 변형이 canonical
    무공백 표기와 함께 잡히고, 괄호는 substring 매칭이 자연히 흡수한다.
    """
    return _WHITESPACE_RE.sub("", text).lower()


def _clip_evidence(value: str) -> str:
    """provenance evidence 원문을 가독 상한으로 자른다."""
    text = value.strip()
    if len(text) <= _EVIDENCE_MAX_CHARS:
        return text
    return text[:_EVIDENCE_MAX_CHARS] + "…"


def _match_qualifications(normalized: str) -> list[tuple[str, str]]:
    """정규화 텍스트에서 자격 용어를 찾아 (canonical, flag) 목록을 돌려준다."""
    hits: list[tuple[str, str]] = []
    for term in ASSOCIATION_QUALIFICATION_TERMS:
        if any(_normalize(variant) in normalized for variant in term.variants):
            hits.append((term.canonical, term.flag))
    return hits


def _match_tech_fields(normalized: str) -> list[str]:
    """정규화 텍스트에서 기술부문을 찾아 표준명 목록을 돌려준다."""
    hits: list[str] = []
    for tech_field in TECH_FIELD_TERMS:
        if any(_normalize(alias) in normalized for alias in tech_field.aliases):
            hits.append(tech_field.name)
    return hits


def _match_text(
    text: str, source_field: str
) -> tuple[list[EligibilityMatch], set[str], list[str]]:
    """한 원문 조각을 매칭해 (matches, flag 집합, 기술부문 표준명) 을 돌려준다."""
    normalized = _normalize(text)
    evidence = _clip_evidence(text)
    matches: list[EligibilityMatch] = []
    flags: set[str] = set()
    tech_names: list[str] = []
    for canonical, flag in _match_qualifications(normalized):
        matches.append(EligibilityMatch(canonical, source_field, evidence))
        flags.add(flag)
    for name in _match_tech_fields(normalized):
        matches.append(EligibilityMatch(name, source_field, evidence))
        tech_names.append(name)
    return matches, flags, tech_names


def _has_flag_content(flags: object) -> bool:
    """flags dict 에 비어있지 않은 값이 하나라도 있는지."""
    return isinstance(flags, dict) and any(
        str(value).strip() for value in flags.values() if value is not None
    )


def _has_license_limit_content(license_limits: object) -> bool:
    """license_limits 리스트에 비어있지 않은 행이 하나라도 있는지."""
    return isinstance(license_limits, list) and any(
        isinstance(row, dict)
        and any(str(value).strip() for value in row.values() if value is not None)
        for row in license_limits
    )


def _iter_license_limit_texts(license_limits: object) -> Iterator[tuple[str, str]]:
    """license_limits 행에서 (source_field, 원문) 매칭 소스를 순회한다(순수).

    ``LICENSE_LIMIT_SOURCE_FIELDS`` 만 소스로 삼고 비어있지 않은 값만 내보낸다.
    source_field 는 ``license_limits.<field>`` 형태 provenance.
    """
    if not isinstance(license_limits, list):
        return
    for row in license_limits:
        if not isinstance(row, dict):
            continue
        for field in LICENSE_LIMIT_SOURCE_FIELDS:
            raw_value = row.get(field)
            if raw_value is None:
                continue
            text = str(raw_value).strip()
            if text:
                yield f"{LICENSE_LIMITS_KEY}.{field}", text


def extract_eligibility_labels(
    eligibility_raw: dict | None,
    *,
    title: str | None = None,
) -> EligibilityLabels:
    """참가자격 원문에서 협회/엔지니어링 자격 라벨을 룰로 추출한다(순수 함수).

    ``eligibility_raw["license_limits"][]`` 의 ``lcnsLmtNm``/``permsnIndstrytyList``
    매칭만 bool·tech_fields 판정에 반영하고, ``title`` 매칭은 참고 증거로만
    ``matches`` 에 남긴다(기관명/과업명 오탐 축 분리). ``has_eligibility_data`` 는
    flags 또는 license_limits 존재 여부만 반영하며, 없으면 라벨은 전부 기본값이다.
    IO/DB 접근 없음.
    """
    association = False
    engineering = False
    tech_fields: list[str] = []
    matches: list[EligibilityMatch] = []
    has_data = False

    if isinstance(eligibility_raw, dict):
        flags_raw = eligibility_raw.get(FLAGS_KEY)
        license_limits = eligibility_raw.get(LICENSE_LIMITS_KEY)
        has_data = _has_flag_content(flags_raw) or _has_license_limit_content(
            license_limits
        )
        for source_field, text in _iter_license_limit_texts(license_limits):
            hit_matches, hit_flags, hit_tech = _match_text(text, source_field)
            matches.extend(hit_matches)
            if FLAG_ASSOCIATION in hit_flags:
                association = True
            if FLAG_ENGINEERING_BUSINESS in hit_flags:
                engineering = True
            for name in hit_tech:
                if name not in tech_fields:
                    tech_fields.append(name)

    # title 은 참고 증거로만 — bool/tech_fields 에 영향 주지 않는다.
    if title and title.strip():
        title_matches, _flags, _tech = _match_text(title.strip(), TITLE_SOURCE)
        matches.extend(title_matches)

    return EligibilityLabels(
        association_required=association,
        engineering_business_required=engineering,
        tech_fields=tuple(tech_fields),
        matches=tuple(matches),
        has_eligibility_data=has_data,
    )


# --- 라벨 판정 (룰 라벨 → positive/negative/ambiguous, 순수) -------------------

# rationale 에 나열할 자격 매칭 최대 개수(리포트/감사 가독성).
_RATIONALE_MAX_MATCHES = 5

# 라벨별 고정 근거 문구(매칭이 없는 negative/ambiguous 는 사유가 고정이다).
_NEGATIVE_RATIONALE = "참가자격 데이터 존재, 협회/엔지니어링/기술부문 매칭 0"
_AMBIGUOUS_RATIONALE = "참가자격 데이터 부재 — 판정 불가"


@dataclass(frozen=True)
class EligibilityVerdict:
    """룰 라벨 판정 결과 — 저장할 라벨 + 감사용 근거 요약(§2 정직)."""

    label: str  # LABEL_POSITIVE | LABEL_NEGATIVE | LABEL_AMBIGUOUS
    rationale: str


def _positive_rationale(labels: EligibilityLabels) -> str:
    """positive 판정 근거를 자격-소스 매칭의 term/필드/원문으로 요약한다(순수).

    title 참고 매칭(``TITLE_SOURCE``)은 라벨 근거가 아니므로 제외하고, 자격
    소스(``LABEL_SOURCE_FIELDS``) 매칭만 중복 없이 상한 개수까지 나열한다.
    """
    parts: list[str] = []
    seen: set[tuple[str, str]] = set()
    for match in labels.matches:
        if match.source_field not in LABEL_SOURCE_FIELDS:
            continue
        key = (match.term, match.source_field)
        if key in seen:
            continue
        seen.add(key)
        parts.append(f"{match.term}[{match.source_field}]: {match.evidence}")
        if len(parts) >= _RATIONALE_MAX_MATCHES:
            break
    return "; ".join(parts)


def classify_eligibility(labels: EligibilityLabels) -> EligibilityVerdict:
    """추출된 라벨을 저장용 판정(positive/negative/ambiguous)으로 매핑한다(순수).

    - positive: 협회/엔지니어링 자격 조건 또는 기술부문이 참가자격에 명시.
    - negative: 참가자격 데이터는 있으나 위 조건이 하나도 없음.
    - ambiguous: 참가자격 데이터 자체가 없어 판정 불가.

    ``association_required``·``engineering_business_required``·``tech_fields`` 는
    자격-소스(license_limits) 매칭에서만 세워지므로(title 은 참고 증거) 이 매핑은
    자격 조건 명시 여부에만 의존한다. IO/DB 접근 없음.
    """
    if (
        labels.association_required
        or labels.engineering_business_required
        or labels.tech_fields
    ):
        return EligibilityVerdict(LABEL_POSITIVE, _positive_rationale(labels))
    if labels.has_eligibility_data:
        return EligibilityVerdict(LABEL_NEGATIVE, _NEGATIVE_RATIONALE)
    return EligibilityVerdict(LABEL_AMBIGUOUS, _AMBIGUOUS_RATIONALE)


# --- 협회 가입 매칭 (classifier 협회 축 · 온보딩 후보 공용, 순수) --------------


def match_association_terms(text: str | None) -> frozenset[str]:
    """자유 텍스트에서 협회 가입(FLAG_ASSOCIATION) canonical 용어를 매칭한다(순수).

    ``_normalize`` (공백 제거+소문자) 후 variant substring 매칭으로 "한국엔지니어링
    협회"·"엔지니어링 협회" 같은 표면 변형을 canonical("엔지니어링협회")로 정규화한다.
    협회 **가입** flag 용어만 반환하므로 엔지니어링사업자/활동주체(사업 신고)는
    제외된다 — 프로필 ``association_memberships`` 정규화와 공고 요건 추출이 같은
    매칭 규칙을 공유한다. IO/DB 접근 없음.
    """
    if not text or not text.strip():
        return frozenset()
    normalized = _normalize(text)
    return frozenset(
        canonical
        for canonical, flag in _match_qualifications(normalized)
        if flag == FLAG_ASSOCIATION
    )


def required_association_memberships(
    eligibility_raw: dict | None,
) -> frozenset[str]:
    """공고 참가자격이 요구하는 협회 가입 canonical 집합을 추출한다(순수).

    ``extract_eligibility_labels`` 의 룰 해석기를 재사용하며(복붙 금지, §4.6),
    license_limits 소스(``LABEL_SOURCE_FIELDS``) 매칭 중 협회 가입 canonical
    (``ASSOCIATION_MEMBERSHIP_CANONICALS``)만 취한다. ``title`` 매칭은 기관명/과업명
    오탐 축이라 애초에 넘기지 않아 제외된다(#207 교훈). 자격 데이터가 없거나 협회
    요건이 없으면 빈 집합을 돌려준다. IO/DB 접근 없음.
    """
    if not isinstance(eligibility_raw, dict):
        return frozenset()
    labels = extract_eligibility_labels(eligibility_raw)
    return frozenset(
        match.term
        for match in labels.matches
        if match.source_field in LABEL_SOURCE_FIELDS
        and match.term in ASSOCIATION_MEMBERSHIP_CANONICALS
    )
