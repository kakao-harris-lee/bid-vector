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
    "QualificationTerm",
    "TechField",
    "ASSOCIATION_QUALIFICATION_TERMS",
    "TECH_FIELD_TERMS",
    "EligibilityMatch",
    "EligibilityLabels",
    "extract_eligibility_labels",
]

# 자격 라벨 flag 종류 — 어떤 bool 라벨을 켜는지 선언에 쓴다.
FLAG_ASSOCIATION = "association"
FLAG_ENGINEERING_BUSINESS = "engineering_business"

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

# 기술부문·전문분야 사전. classifier ``LICENSE_ALIASES``(해양 기술용역 면허군
# PORT001/MAR001/HYDRO001, app/services/classification/taxonomy.py)와
# docs/marine-engineering-gate.md 3절에서 근거를 두고 시드했다. 실데이터(내일부터
# 적재)로 report 스크립트가 미매칭 원문을 surface 하면 여기에 출처를 달아
# 확장한다(추측 용어 남발 금지). 표준명은 해당 면허군의 대표 한글명이다.
TECH_FIELD_TERMS: tuple[TechField, ...] = (
    # PORT001: 항만및해안, 항만설계 (taxonomy.py:74, marine gate 3절).
    TechField(name="항만및해안", code="PORT001", aliases=("항만및해안", "항만설계")),
    # MAR001: 해양엔지니어링 (taxonomy.py:75, marine gate 3절).
    TechField(name="해양엔지니어링", code="MAR001", aliases=("해양엔지니어링",)),
    # HYDRO001: 수로조사/수로측량/해양조사 (taxonomy.py:76, marine gate 3절).
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
