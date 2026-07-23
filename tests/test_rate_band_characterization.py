"""Characterization tests for the procurement rate-band / keyword / blend logic.

CHARACTERIZATION, not correctness: every expected value is the *current*
implementation's actual output, captured so the upcoming refactors
(rate_band spec table, keyword-rule data-ization, blend-weight table) can be
proven behavior-preserving byte-for-byte. Do NOT "fix" a surprising expectation
here — if a value looks wrong, that is the frozen current behavior; change it
only alongside a deliberate implementation change.

Sections:
  A. keyword resolver (_resolve_service / _resolve_goods_procurement_rate_band)
  B. band application matrices (apply_procurement_rate_band / _candidate_band)
     + the :509 partial inline application inside select_competitive_base_rate
  C. explanation byte-equality (historical vs ensemble modules)
  D. select_competitive_base_rate routing matrix + high-rate tail adjustment
  F. price_prediction signal flags + price-regime label routing

``probability_score`` is a price-fit estimate (정직 명세); nothing here is a
literal win probability.
"""

from __future__ import annotations

import pytest

from app.ai.predictors.ensemble import _build_ensemble_explanation
from app.ai.predictors.historical import (
    _is_goods_deep_discount_notice,
    _is_goods_narrow_control_price_competitive,
    _resolve_goods_procurement_rate_band,
    _resolve_service_procurement_rate_band,
    apply_high_rate_distribution_adjustment,
    apply_procurement_candidate_band,
    apply_procurement_rate_band,
    build_historical_explanation,
    resolve_procurement_rate_band,
    select_competitive_base_rate,
)
from app.ai.price_prediction import (
    _build_price_regime_features,
    _detect_price_regime_signals,
)
from app.ai.predictors.base import PricePredictionContext


# ===========================================================================
# A. Keyword resolver — one case per keyword in every tuple
# ===========================================================================

# service keywords that resolve to service_direct_negotiated.
# explicit_direct match anywhere; title_direct match only in the first line (title).
_SERVICE_DIRECT_NEGOTIATED_KEYWORDS = [
    "수의시담", "수의 시담",            # explicit (whole text)
    "수의계약", "수의 계약", "(수의)", "[수의]",  # title-scoped
]

# service keywords that resolve to service_price_competitive (rules 2-5, all collapse).
_SERVICE_PRICE_COMPETITIVE_KEYWORDS = [
    # engineering_competitive_keywords
    "해양이용협의", "간이해양이용협의", "해양환경영향", "환경영향", "사전영향조사",
    "사후영향조사", "사전·사후영향조사", "영향조사", "적지조사", "해저지형조사", "수심측량",
    "지형 및 수심측량", "실시설계", "기본 및 실시설계", "기본설계", "서식환경", "바다숲",
    "인공어초", "어초어장", "해상풍력", "pq 후 지명경쟁",
    # service_low_tail_price_competitive_keywords
    "건축기획", "정밀안전진단", "정밀안전점검", "내진성능평가", "석면조사", "성능점검",
    "시설 및 안전관리", "경비용역", "예초 용역", "비용분석",
    # price_competitive_keywords
    "폐기물", "기술지도", "사후환경", "pq", "가격입찰", "설계용역",
    "감리", "건설사업관리", "처리용역", "재활용",
    # service_exception_price_competitive_keywords
    "2단계", "규격·가격", "규격 가격", "규격가격", "가격분리", "가격 분리", "동시입찰",
    "수학여행", "현장체험학습", "해외문화체험", "버스", "차량임차", "차량 임차",
    "임차용역", "보험", "자동차보험", "재산종합보험",
]

_SERVICE_HIGH_NEGOTIATED_KEYWORDS = [
    "협상에 의한 계약", "협상", "제안", "위탁", "운영", "홍보", "영상", "콘텐츠",
    "시스템", "개발", "출판", "제작", "마스터플랜", "플랫폼", "sns", "서포터",
]

# every listed goods keyword collapses to goods_price_competitive.
_GOODS_PRICE_COMPETITIVE_KEYWORDS = [
    "소액수의 견적", "견적 제출", "견적제출", "2단계", "규격·가격", "규격 가격", "규격가격",
    "가격분리", "가격 분리", "동시입찰", "국내도서", "도서 구매", "운영장비 구입",
    "데이터로거 구매", "모니터 확장", "상토 구입", "원액주입설비", "인명구조함", "주방기구",
]


@pytest.mark.parametrize("keyword", _SERVICE_DIRECT_NEGOTIATED_KEYWORDS)
def test_service_keyword_maps_to_direct_negotiated(keyword):
    # single-line description => the keyword sits in the title, satisfying the
    # title-scoped rule for 수의계약/(수의)/[수의] as well as the whole-text rule.
    assert resolve_procurement_rate_band(category="service", description=f"{keyword} 용역") == "service_direct_negotiated"


@pytest.mark.parametrize("keyword", _SERVICE_PRICE_COMPETITIVE_KEYWORDS)
def test_service_keyword_maps_to_price_competitive(keyword):
    assert resolve_procurement_rate_band(category="service", description=f"{keyword} 용역") == "service_price_competitive"


@pytest.mark.parametrize("keyword", _SERVICE_HIGH_NEGOTIATED_KEYWORDS)
def test_service_keyword_maps_to_high_negotiated(keyword):
    assert resolve_procurement_rate_band(category="service", description=f"{keyword} 용역") == "service_high_negotiated"


@pytest.mark.parametrize("keyword", _GOODS_PRICE_COMPETITIVE_KEYWORDS)
def test_goods_keyword_maps_to_price_competitive(keyword):
    assert resolve_procurement_rate_band(category="goods", description=f"OO {keyword} 구매") == "goods_price_competitive"


def test_direct_resolver_functions_match_orchestrator():
    """The private resolvers are what the orchestrator dispatches to (lowercased text)."""
    assert _resolve_service_procurement_rate_band("폐기물 용역") == "service_price_competitive"
    assert _resolve_goods_procurement_rate_band("oo 견적제출 구매") == "goods_price_competitive"


# --- title-vs-body scope -----------------------------------------------------
def test_title_direct_keyword_only_matches_in_title_line():
    """수의계약 in the TITLE line => direct_negotiated."""
    assert resolve_procurement_rate_band(
        category="service", description="수의계약 용역"
    ) == "service_direct_negotiated"


def test_title_direct_keyword_in_body_does_not_trigger_direct():
    """수의계약 only in the body (line 2) does NOT trigger direct; the title's
    engineering keyword (실시설계) wins instead — proving the title-scope."""
    assert resolve_procurement_rate_band(
        category="service", description="실시설계 용역\n수의계약 예정"
    ) == "service_price_competitive"


def test_explicit_direct_keyword_matches_anywhere():
    """수의시담 is whole-text scoped, so it binds even mixed with a price keyword."""
    assert resolve_procurement_rate_band(
        category="service", description="수의시담 폐기물"
    ) == "service_direct_negotiated"


# --- priority conflicts (>=3 rule-order pairs) -------------------------------
@pytest.mark.parametrize(
    "description, expected",
    [
        # explicit_direct (rule 1) beats engineering (rule 2)
        ("수의시담 실시설계 용역", "service_direct_negotiated"),
        # explicit_direct (rule 1) beats high_negotiated (rule 6)
        ("수의시담 협상 용역", "service_direct_negotiated"),
        # explicit_direct (rule 1) beats exception price-competitive (rule 5)
        ("수의시담 버스 용역", "service_direct_negotiated"),
        # engineering (rule 2) beats high_negotiated (rule 6)
        ("실시설계 협상 용역", "service_price_competitive"),
        # low_tail (rule 3) beats high_negotiated (rule 6)
        ("정밀안전진단 협상 용역", "service_price_competitive"),
        # price_competitive (rule 4) beats high_negotiated (rule 6)
        ("폐기물 협상 용역", "service_price_competitive"),
        # exception (rule 5) beats high_negotiated (rule 6)
        ("버스 협상 용역", "service_price_competitive"),
        # title 수의계약 (rule 1 title) beats body engineering
        ("수의계약 용역\n실시설계 포함", "service_direct_negotiated"),
    ],
)
def test_service_keyword_priority_conflicts(description, expected):
    assert resolve_procurement_rate_band(category="service", description=description) == expected


# --- goods deep-discount / narrow-control predicates -------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("2단계 급식", True),
        ("규격가격 농산물", True),
        ("규격·가격 급식", True),
        ("2단계", False),        # no food keyword
        ("급식", False),         # no two-stage keyword
        ("일반 물품 구매", False),
    ],
)
def test_is_goods_deep_discount_notice(text, expected):
    assert _is_goods_deep_discount_notice(text) is expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("(계측제어)", True),
        ("(계측제어) 판넬", True),
        ("(계측제어) 관급자재", False),
        ("(계측제어) 프로세스", False),
        ("(계측제어) 계장", False),
        ("계측제어 without paren", False),  # requires the parenthesized token
    ],
)
def test_is_goods_narrow_control_price_competitive(text, expected):
    assert _is_goods_narrow_control_price_competitive(text) is expected


def test_goods_deep_discount_takes_precedence_over_price_competitive():
    """2단계 alone => goods_price_competitive, but 2단계 + 급식 => goods_deep_discount."""
    assert resolve_procurement_rate_band(category="goods", description="2단계") == "goods_price_competitive"
    assert resolve_procurement_rate_band(category="goods", description="2단계 급식") == "goods_deep_discount"


# --- None / no-match cases ---------------------------------------------------
@pytest.mark.parametrize(
    "category, description, expected",
    [
        ("service", "OO 청소용역", None),          # no service keyword
        ("construction", "OO 토목공사", None),       # construction category => None
        ("goods", "일반 물품 구매", None),           # no goods keyword
        ("goods", "", None),                        # empty text
        ("service", "   ", None),                   # whitespace-only text
        ("technical-service", "폐기물", "service_price_competitive"),  # alias routes to service resolver
        ("software", "시스템 개발", "service_high_negotiated"),        # software routes to service resolver
        ("unknown-category", "폐기물", None),        # non-goods/non-service => None
    ],
)
def test_resolve_band_none_and_alias_cases(category, description, expected):
    assert resolve_procurement_rate_band(category=category, description=description) == expected


# ===========================================================================
# B. Band application matrices
# ===========================================================================
_BAND_DESC = {
    "service_high_negotiated": ("service", "협상에 의한 계약 용역"),
    "service_direct_negotiated": ("service", "수의시담 용역"),
    "service_price_competitive": ("service", "폐기물 처리용역"),
    "goods_deep_discount": ("goods", "2단계 급식"),
    "goods_price_competitive": ("goods", "OO 견적제출 구매"),
    "none": ("construction", "OO 토목공사"),
}

# (band_key, base_rate) -> current apply_procurement_rate_band output.
_APPLY_BAND_MATRIX = {
    ("service_high_negotiated", 0.70): 1.0,
    ("service_high_negotiated", 0.90): 1.0,
    ("service_high_negotiated", 1.00): 1.0,
    ("service_high_negotiated", 1.05): 1.05,
    ("service_direct_negotiated", 0.70): 0.95,
    ("service_direct_negotiated", 0.95): 0.95,
    ("service_direct_negotiated", 1.00): 1.0,
    ("service_direct_negotiated", 1.20): 1.2,
    ("service_price_competitive", 0.70): 0.70,
    ("service_price_competitive", 0.90): 0.90,
    ("service_price_competitive", 0.95): 0.90,
    ("service_price_competitive", 1.20): 0.90,
    ("goods_deep_discount", 0.70): 0.70,
    ("goods_deep_discount", 0.85): 0.78,
    ("goods_deep_discount", 1.00): 0.78,
    ("goods_price_competitive", 0.70): 0.70,
    ("goods_price_competitive", 0.90): 0.90,
    ("goods_price_competitive", 0.95): 0.90,
    ("none", 0.70): 0.70,
    ("none", 0.95): 0.95,
    ("none", 1.20): 1.2,
}


@pytest.mark.parametrize("key, expected", list(_APPLY_BAND_MATRIX.items()))
def test_apply_procurement_rate_band_matrix(key, expected):
    band_name, base_rate = key
    category, description = _BAND_DESC[band_name]
    assert apply_procurement_rate_band(base_rate, category=category, description=description) == expected


# (band, (conservative, base, aggressive)) -> current candidate-band output tuple.
_CANDIDATE_BAND_MATRIX = {
    ("service_high_negotiated", (0.80, 0.85, 0.90)): (0.98, 1.0, 1.0),
    ("service_high_negotiated", (0.95, 1.00, 1.05)): (0.98, 1.0, 1.05),
    ("service_high_negotiated", (0.70, 0.78, 0.86)): (0.98, 1.0, 1.0),
    # GAP D upper boundary: a conservative ALREADY above the 0.98 floor is a max()
    # floor, not a cap, so it is preserved (not pulled down to 0.98).
    ("service_high_negotiated", (0.99, 1.00, 1.02)): (0.99, 1.0, 1.02),
    ("service_direct_negotiated", (0.80, 0.85, 0.90)): (0.93, 0.95, 0.97),
    ("service_direct_negotiated", (0.95, 1.00, 1.05)): (0.95, 1.0, 1.05),
    ("service_direct_negotiated", (0.70, 0.78, 0.86)): (0.93, 0.95, 0.97),
    ("service_price_competitive", (0.80, 0.85, 0.90)): (0.80, 0.85, 0.90),
    ("service_price_competitive", (0.95, 1.00, 1.05)): (0.90, 0.90, 0.90),
    ("service_price_competitive", (0.70, 0.78, 0.86)): (0.70, 0.78, 0.86),
    ("goods_deep_discount", (0.80, 0.85, 0.90)): (0.76, 0.78, 0.84),
    ("goods_deep_discount", (0.95, 1.00, 1.05)): (0.76, 0.78, 0.84),
    ("goods_deep_discount", (0.70, 0.78, 0.86)): (0.70, 0.78, 0.84),
    ("goods_price_competitive", (0.80, 0.85, 0.90)): (0.80, 0.85, 0.90),
    ("goods_price_competitive", (0.95, 1.00, 1.05)): (0.88, 0.90, 0.91),
    ("goods_price_competitive", (0.70, 0.78, 0.86)): (0.70, 0.78, 0.86),
    (None, (0.80, 0.85, 0.90)): (0.80, 0.85, 0.90),
    (None, (0.95, 1.00, 1.05)): (0.95, 1.0, 1.05),
    ("unknown_band", (0.95, 1.00, 1.05)): (0.95, 1.0, 1.05),
}


@pytest.mark.parametrize("key, expected", list(_CANDIDATE_BAND_MATRIX.items()))
def test_apply_procurement_candidate_band_matrix(key, expected):
    band, triple = key
    conservative, base, aggressive = triple
    assert apply_procurement_candidate_band(
        conservative_rate=conservative,
        base_rate=base,
        aggressive_rate=aggressive,
        rate_band=band,
    ) == expected


# --- :509 partial inline band application ------------------------------------
def _scr(**overrides):
    kwargs = dict(
        mean_rate=0.85, median_rate=0.85, recent_median_rate=0.85,
        competitive_quantile_rate=0.85, heuristic_rate=0.85, reserve_context=None,
    )
    kwargs.update(overrides)
    return select_competitive_base_rate(**kwargs)


def test_group_branch_applies_only_two_of_five_bands():
    """AUDIT LOCK: the inline block inside select_competitive_base_rate's
    construction/service group branches applies ONLY service_high_negotiated and
    service_price_competitive — NOT service_direct_negotiated / goods_* bands.
    This partial application is the current behavior and must be preserved
    verbatim until the refactor deliberately unifies it.
    """
    # service_direct_negotiated is NOT applied inline in the service group branch:
    # the 0.95 floor never fires, so the blended 0.85 survives.
    group_direct = _scr(
        category="service", description="수의시담 용역", sample_size=20, business_group="service"
    )
    # float-exact: the blend lands at 0.8499999999999999, and the 0.95 direct
    # floor never fires (partial application), so it survives unchanged.
    assert group_direct == 0.8499999999999999

    # service_high_negotiated IS applied inline (max -> 1.0).
    group_high = _scr(
        category="service", description="협상에 의한 계약", sample_size=20, business_group="service"
    )
    assert group_high == 1.0

    # service_price_competitive IS applied inline (min -> 0.90).
    group_pc = _scr(
        category="service", description="폐기물 처리용역", sample_size=20, business_group="service",
        mean_rate=0.95, median_rate=0.95, recent_median_rate=0.95,
        competitive_quantile_rate=0.95, heuristic_rate=0.95,
    )
    assert group_pc == 0.90


def test_nongroup_branch_applies_direct_negotiated_band():
    """Contrast: the non-group (category-keyed) branch DOES apply the 0.95
    direct-negotiated floor via apply_procurement_rate_band — so the group branch
    skipping it is a genuine behavioral difference, not a no-op."""
    nogroup_direct = _scr(
        category="service", description="수의시담 용역", sample_size=20, business_group=None
    )
    assert nogroup_direct == 0.95


# ===========================================================================
# C. Explanation byte-equality (정직 명세 문구 고정)
# ===========================================================================
# The band clause text shared by BOTH explanation builders (no leading space,
# no trailing period). Historical appends it with a leading space; ensemble
# appends the SAME text with a leading space AND a trailing period.
_BAND_CLAUSE = {
    "service_high_negotiated": "협상/위탁형 용역으로 보고 100% 근접 목표율을 적용했습니다",
    "service_direct_negotiated": "수의시담/수의계약형 용역으로 보고 95% 이상 목표율을 적용했습니다",
    "service_price_competitive": "가격경쟁형 용역으로 보고 90% 상한 목표율을 적용했습니다",
    "goods_deep_discount": "2단계 급식/농산물형 물품으로 보고 78% 기준 저가 목표율을 적용했습니다",
    "goods_price_competitive": "견적/2단계/구매설치형 물품으로 보고 90% 기준, 91% 상한 목표율을 적용했습니다",
}

_HIST_BASE_SENTENCE = (
    "최근 히스토리컬 데이터 20건의 사정률 분포를 반영해 기준 사정률 0.9012와 "
    "보수/기준/공격 시나리오를 계산했습니다."
)
_ENS_BASE_SENTENCE = (
    "artifact v1 ensemble이 historical(0.9012 × 100%) 조합으로 기준 사정률을 계산했습니다."
)


def _hist_explanation(band):
    return build_historical_explanation(
        sample_size=20, base_rate=0.9012, agency_match_sample_size=0,
        reserve_pattern=None, rate_band=band, high_rate_adjustment=None,
    )


def _ens_explanation(band):
    return _build_ensemble_explanation(
        components={"historical": 0.9012},
        component_weights={"historical": 1.0},
        artifact={"artifact_version": "1"},
        agency_match_sample_size=0,
        rate_band=band,
        high_rate_adjustment=None,
    )


@pytest.mark.parametrize("band, clause", list(_BAND_CLAUSE.items()))
def test_historical_explanation_band_clause_byte_equal(band, clause):
    assert _hist_explanation(band) == f"{_HIST_BASE_SENTENCE} {clause}"


@pytest.mark.parametrize("band, clause", list(_BAND_CLAUSE.items()))
def test_ensemble_explanation_band_clause_byte_equal(band, clause):
    assert _ens_explanation(band) == f"{_ENS_BASE_SENTENCE} {clause}."


def test_explanation_no_band_is_base_sentence_only():
    assert _hist_explanation(None) == _HIST_BASE_SENTENCE
    assert _ens_explanation(None) == _ENS_BASE_SENTENCE


@pytest.mark.parametrize("band, clause", list(_BAND_CLAUSE.items()))
def test_band_clause_wording_is_shared_between_modules(band, clause):
    """Relationship lock: the two modules use BYTE-IDENTICAL band wording; the
    only difference is that the ensemble builder appends a trailing period. A
    refactor that data-izes these strings must keep them shared (or this test
    documents the divergence if they ever split)."""
    hist = _hist_explanation(band)
    ens = _ens_explanation(band)
    assert hist.endswith(" " + clause)
    assert ens.endswith(" " + clause + ".")
    # Strip the trailing period from the ensemble tail => identical to historical tail.
    assert ens[len(_ENS_BASE_SENTENCE):] == hist[len(_HIST_BASE_SENTENCE):] + "."


def test_historical_explanation_full_detail_composition():
    """agency + reserve + high-rate details compose in a fixed order (no periods
    between details in the historical builder)."""
    result = build_historical_explanation(
        sample_size=20, base_rate=0.9012, agency_match_sample_size=3,
        reserve_pattern={"sample_count": 5}, rate_band="service_price_competitive",
        high_rate_adjustment={"reason": "x"},
    )
    assert result == (
        "최근 히스토리컬 데이터 20건의 사정률 분포를 반영해 기준 사정률 0.9012와 "
        "보수/기준/공격 시나리오를 계산했습니다. 동일 기관 이력 3건에 추가 가중치를 적용했고 "
        "예비가격 패턴 5건도 함께 참고했습니다 가격경쟁형 용역으로 보고 90% 상한 목표율을 적용했습니다 "
        "최근 고율 낙찰 분포를 반영해 기준 사정률을 상향 보정했습니다"
    )


def test_ensemble_explanation_full_detail_composition():
    """agency + band + high-rate details compose as period-terminated sentences."""
    result = _build_ensemble_explanation(
        components={"historical": 0.9012},
        component_weights={"historical": 1.0},
        artifact={"artifact_version": "1"},
        agency_match_sample_size=3,
        rate_band="service_price_competitive",
        high_rate_adjustment={"reason": "x"},
    )
    assert result == (
        "artifact v1 ensemble이 historical(0.9012 × 100%) 조합으로 기준 사정률을 계산했습니다. "
        "동일 기관 이력 3건도 함께 반영했습니다. 가격경쟁형 용역으로 보고 90% 상한 목표율을 적용했습니다. "
        "최근 고율 낙찰 분포를 반영해 기준 사정률을 상향 보정했습니다."
    )


# ===========================================================================
# D. select_competitive_base_rate routing matrix + high-rate tail adjustment
# ===========================================================================
def _scr_matrix(*, business_group, sample_size, category, description):
    return select_competitive_base_rate(
        category=category,
        description=description,
        sample_size=sample_size,
        mean_rate=0.90,
        median_rate=0.903,
        recent_median_rate=0.905,
        competitive_quantile_rate=0.900,
        heuristic_rate=0.88,
        business_group=business_group,
        reserve_context=None,
    )


# category representatives used across the routing matrix.
_CAT_NONE = ("construction", "OO 토목공사")           # resolves to band=None
_CAT_SVC_NONE = ("service", "OO 청소용역")            # service category, band=None
_CAT_SVC_PC = ("service", "폐기물 처리용역")           # service_price_competitive
_CAT_SVC_HN = ("service", "협상에 의한 계약")          # service_high_negotiated
_CAT_GOODS_NONE = ("goods", "OO 물품")                # goods category, band=None


@pytest.mark.parametrize("sample_size", [10, 15])
@pytest.mark.parametrize(
    "cat_desc, expected",
    [
        (_CAT_NONE, 0.9019),
        (_CAT_SVC_NONE, 0.9019),
        (_CAT_SVC_PC, 0.9),        # inline price-competitive cap fires
        (_CAT_SVC_HN, 1.0),        # inline high-negotiated floor fires
        (_CAT_GOODS_NONE, 0.9019),
    ],
)
def test_scr_construction_group_deep(sample_size, cat_desc, expected):
    """construction group, sample>=10: recent_target*0.6 + robust_median*0.3 + heuristic*0.1."""
    category, description = cat_desc
    assert _scr_matrix(
        business_group="construction", sample_size=sample_size,
        category=category, description=description,
    ) == expected


@pytest.mark.parametrize("sample_size", [10, 15])
@pytest.mark.parametrize(
    "cat_desc, expected",
    [
        (_CAT_NONE, 0.89805),
        (_CAT_SVC_NONE, 0.89805),
        (_CAT_SVC_PC, 0.89805),    # 0.90 cap does not bind (already below)
        (_CAT_SVC_HN, 1.0),
        (_CAT_GOODS_NONE, 0.89805),
    ],
)
def test_scr_service_group_deep(sample_size, cat_desc, expected):
    """service group, sample>=10: quantile*0.5 + robust_median*0.35 + heuristic*0.15."""
    category, description = cat_desc
    assert _scr_matrix(
        business_group="service", sample_size=sample_size,
        category=category, description=description,
    ) == expected


@pytest.mark.parametrize("sample_size", [10, 15])
@pytest.mark.parametrize(
    "cat_desc, expected",
    [
        (_CAT_NONE, 0.9),             # construction category => quantile_target
        (_CAT_SVC_NONE, 0.905),       # service category => recent_target
        (_CAT_SVC_PC, 0.9),           # apply_procurement_rate_band caps at 0.90
        (_CAT_SVC_HN, 1.0),           # apply_procurement_rate_band floors at 1.0
        (_CAT_GOODS_NONE, 0.9031),    # generic blend: median*0.7 + recent*0.2 + mean*0.1
    ],
)
def test_scr_nogroup_deep_category_routing(sample_size, cat_desc, expected):
    """business_group=None, sample>=10: category-keyed routing inside the generic branch."""
    category, description = cat_desc
    assert _scr_matrix(
        business_group=None, sample_size=sample_size,
        category=category, description=description,
    ) == expected


@pytest.mark.parametrize("business_group", ["construction", "service", None])
@pytest.mark.parametrize(
    "sample_size, expected_non_hn, expected_hn",
    [
        (7, 0.89965, 1.0),
        (3, 0.8973500000000001, 1.0),
        (1, 0.891, 1.0),
    ],
)
def test_scr_below_group_threshold_is_group_independent(business_group, sample_size, expected_non_hn, expected_hn):
    """Below the sample>=10 group threshold, business_group is IGNORED: all three
    groups return identical rates. This group-independence is current behavior."""
    for cat_desc, expected in [
        (_CAT_NONE, expected_non_hn),
        (_CAT_SVC_NONE, expected_non_hn),
        (_CAT_SVC_PC, expected_non_hn),   # band cap does not bind at these levels
        (_CAT_GOODS_NONE, expected_non_hn),
        (_CAT_SVC_HN, expected_hn),        # high-negotiated 1.0 floor still applies
    ]:
        category, description = cat_desc
        assert _scr_matrix(
            business_group=business_group, sample_size=sample_size,
            category=category, description=description,
        ) == expected


# --- high-rate tail adjustment (per group, trigger fires / does not) ---------
@pytest.fixture()
def _pin_high_rate_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PREDICTION_HIGH_RATE_TAIL_ADJUSTMENT_ENABLED", True)
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_BUDGET_MAX", 50_000_000.0)
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_TARGET", 0.93)
    monkeypatch.setattr(settings, "PREDICTION_SMALL_BUDGET_HIGH_RATE_MIN_RATE", 0.925)


def _summary(**over):
    base = dict(
        sample_size=1000, recent_sample_size=20, recent_median_bid_rate=0.90,
        recent_upper_quantile_bid_rate=0.905, upper_quantile_bid_rate=0.905,
        recent_rate_ge_0_93_share=0.0, recent_rate_ge_0_95_share=0.0, recent_rate_ge_0_98_share=0.0,
    )
    base.update(over)
    return base


def test_high_rate_goods_tail_fires(_pin_high_rate_settings):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.85, category="goods", description="OO 물품 구매", business_group="goods",
        budget=100_000_000.0,
        historical_summary=_summary(recent_rate_ge_0_95_share=0.40, recent_median_bid_rate=0.96,
                                    recent_upper_quantile_bid_rate=0.98, upper_quantile_bid_rate=0.98),
    )
    assert rate == 0.9540000000000001
    assert ctx["reason"] == "goods_recent_high_rate_tail"
    assert ctx["business_group"] == "goods"
    assert ctx["original_bid_rate"] == 0.85
    assert ctx["adjusted_bid_rate"] == 0.954


def test_high_rate_service_tail_fires(_pin_high_rate_settings):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.90, category="service", description="OO 청소용역", business_group="service",
        budget=100_000_000.0,
        historical_summary=_summary(recent_median_bid_rate=0.94, recent_rate_ge_0_93_share=0.40,
                                    recent_upper_quantile_bid_rate=0.95, upper_quantile_bid_rate=0.95),
    )
    assert rate == 0.9309999999999999
    assert ctx["reason"] == "service_recent_high_rate_tail"


def test_high_rate_construction_small_budget_fires(_pin_high_rate_settings):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.9272, category="construction", description="원상복구 및 사무실 조성공사",
        business_group="construction", budget=40_250_000.0,
        historical_summary=_summary(recent_median_bid_rate=0.9028, recent_upper_quantile_bid_rate=0.9051,
                                    upper_quantile_bid_rate=0.9050),
    )
    assert rate == 0.93
    assert ctx["reason"] == "construction_small_budget_high_rate_target"


def test_high_rate_service_high_negotiated_band(_pin_high_rate_settings):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.85, category="service", description="협상에 의한 계약 용역", business_group="service",
        budget=100_000_000.0, historical_summary=_summary(),
    )
    assert rate == 1.0
    assert ctx["reason"] == "service_high_negotiated"


def test_high_rate_preserve_historical_component(_pin_high_rate_settings):
    """No target fires, but historical_rate > base_rate => candidate rises to the
    historical component with a 'preserve_historical_component' context."""
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.85, category="construction", description="OO 공사", business_group="construction",
        budget=800_000_000.0, historical_summary=_summary(), historical_rate=0.92,
    )
    assert rate == 0.92
    assert ctx["reason"] == "preserve_historical_component"


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(base_rate=0.85, category="goods", description="OO 물품 구매", business_group="goods",
             budget=100_000_000.0, summary_over={}),
        dict(base_rate=0.90, category="service", description="OO 청소용역", business_group="service",
             budget=100_000_000.0, summary_over={}),
        dict(base_rate=0.9272, category="construction", description="OO 공사", business_group="construction",
             budget=800_000_000.0, summary_over={}),
        # price-competitive band is explicitly excluded from the tail lift
        dict(base_rate=0.90, category="service", description="폐기물 처리용역", business_group="service",
             budget=100_000_000.0, summary_over=dict(recent_rate_ge_0_95_share=0.40)),
        # too few samples => early return
        dict(base_rate=0.85, category="goods", description="OO 물품", business_group="goods",
             budget=100_000_000.0, summary_over=dict(sample_size=10, recent_sample_size=4)),
    ],
)
def test_high_rate_no_adjustment_returns_base_and_none(_pin_high_rate_settings, kwargs):
    summary_over = kwargs.pop("summary_over")
    base_rate = kwargs.pop("base_rate")
    rate, ctx = apply_high_rate_distribution_adjustment(
        base_rate, historical_summary=_summary(**summary_over), **kwargs
    )
    assert rate == base_rate
    assert ctx is None


def test_high_rate_disabled_returns_base_and_none(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "PREDICTION_HIGH_RATE_TAIL_ADJUSTMENT_ENABLED", False)
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.85, category="goods", description="OO 물품 구매", business_group="goods",
        budget=100_000_000.0,
        historical_summary=_summary(recent_rate_ge_0_95_share=0.40, recent_median_bid_rate=0.96),
    )
    assert rate == 0.85
    assert ctx is None


# --- tail trigger OR-branch isolation (mutation-kill) ------------------------
# Each case satisfies EXACTLY ONE sub-condition of the compound trigger, so if a
# refactor drops that sub-condition (or re-orders the OR into an AND) exactly this
# case breaks — the others still cover the surviving sub-conditions.

# goods trigger (:639-643):  ge_95_share>=0.35  OR  ge_98_share>=0.20  OR  recent_upper>=0.97
@pytest.mark.parametrize(
    "case_id, summary_over, expected_rate",
    [
        # only ge_95_share fires (ge_98=0, recent_upper=0.90 < 0.97)
        ("only_ge95", dict(recent_median_bid_rate=0.95, recent_upper_quantile_bid_rate=0.90,
                           upper_quantile_bid_rate=0.95, recent_rate_ge_0_95_share=0.40),
         0.9299999999999999),
        # only ge_98_share fires (ge_95=0, recent_upper=0.90 < 0.97)
        ("only_ge98", dict(recent_median_bid_rate=0.95, recent_upper_quantile_bid_rate=0.90,
                           upper_quantile_bid_rate=0.95, recent_rate_ge_0_98_share=0.25),
         0.9299999999999999),
        # only recent_upper>=0.97 fires (both share thresholds at 0)
        ("only_upper", dict(recent_median_bid_rate=0.95, recent_upper_quantile_bid_rate=0.98,
                            upper_quantile_bid_rate=0.95),
         0.9465),
    ],
)
def test_high_rate_goods_trigger_or_isolation(_pin_high_rate_settings, case_id, summary_over, expected_rate):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.85, category="goods", description="OO 물품 구매", business_group="goods",
        budget=100_000_000.0, historical_summary=_summary(**summary_over),
    )
    assert ctx is not None, case_id
    assert ctx["reason"] == "goods_recent_high_rate_tail", case_id
    assert rate == expected_rate, case_id


# service trigger (:650-653):  recent_median>=0.93  AND  (ge_93_share>=0.30 OR ge_95_share>=0.20)
@pytest.mark.parametrize(
    "case_id, summary_over, expected_rate",
    [
        # only ge_93_share fires the inner OR (ge_95=0)
        ("only_ge93", dict(recent_median_bid_rate=0.94, recent_upper_quantile_bid_rate=0.95,
                           upper_quantile_bid_rate=0.95, recent_rate_ge_0_93_share=0.40),
         0.9309999999999999),
        # only ge_95_share fires the inner OR (ge_93=0)
        ("only_ge95", dict(recent_median_bid_rate=0.94, recent_upper_quantile_bid_rate=0.95,
                           upper_quantile_bid_rate=0.95, recent_rate_ge_0_95_share=0.25),
         0.9309999999999999),
    ],
)
def test_high_rate_service_trigger_or_isolation(_pin_high_rate_settings, case_id, summary_over, expected_rate):
    rate, ctx = apply_high_rate_distribution_adjustment(
        0.90, category="service", description="OO 청소용역", business_group="service",
        budget=100_000_000.0, historical_summary=_summary(**summary_over),
    )
    assert ctx is not None, case_id
    assert ctx["reason"] == "service_recent_high_rate_tail", case_id
    assert rate == expected_rate, case_id


# ===========================================================================
# F. price_prediction signal flags + price-regime label routing
# ===========================================================================
@pytest.mark.parametrize(
    "category, text, rate_band, expected",
    [
        # title-semantic guard: bare "2단계" needs a spec/price cue (규격/가격/동시) to
        # count; here there is none, so two_stage_or_separated no longer fires. The
        # deep_discount label is unaffected (the goods_deep_discount band drives it).
        ("goods", "2단계 급식 농산물 구매", "goods_deep_discount", {
            "floor_bound": False, "near_100": False, "deep_discount": True,
            "conflicting": False, "signals": ["deep_discount"]}),
        ("service", "폐기물 처리용역", "service_price_competitive", {
            "floor_bound": True, "near_100": False, "deep_discount": False,
            "conflicting": False, "signals": ["price_competitive"]}),
        ("service", "협상에 의한 계약", "service_high_negotiated", {
            "floor_bound": False, "near_100": True, "deep_discount": False,
            "conflicting": False, "signals": ["negotiated"]}),
        ("service", "수의시담 용역", "service_direct_negotiated", {
            "floor_bound": False, "near_100": True, "deep_discount": False,
            "conflicting": False, "signals": ["direct_negotiated"]}),
        # conflicting: near_100 (협상) AND floor_bound (폐기물) simultaneously. The bare
        # "2단계" here lacks a spec/price co-occurrence cue, so two_stage no longer
        # fires — but the conflict (협상 band-competitive) is genuine and preserved.
        ("service", "협상 폐기물 2단계", "service_price_competitive", {
            "floor_bound": True, "near_100": True, "deep_discount": False,
            "conflicting": True, "signals": ["negotiated", "price_competitive"]}),
        ("service", "oo 청소용역", None, {
            "floor_bound": False, "near_100": False, "deep_discount": False,
            "conflicting": False, "signals": []}),
        ("goods", "규격·가격 동시입찰 구매", "goods_price_competitive", {
            "floor_bound": True, "near_100": False, "deep_discount": False,
            "conflicting": False, "signals": ["price_competitive", "two_stage_or_separated"]}),
        # title-semantic guard: 제안서 평가 is a negotiation context (near_100), so the
        # bare "소액수의 견적" quote no longer raises a competing floor_bound → the false
        # near_100 ∧ floor_bound conflict is suppressed and the notice reads near_100.
        ("service", "제안서 평가 소액수의 견적", None, {
            "floor_bound": False, "near_100": True, "deep_discount": False,
            "conflicting": False, "signals": ["negotiated"]}),
    ],
)
def test_detect_price_regime_signals(category, text, rate_band, expected):
    # production lowercases the description before calling this; mirror that.
    assert _detect_price_regime_signals(category=category, text=text.lower(), rate_band=rate_band) == expected


@pytest.mark.parametrize(
    "rate_band, description, expected_label, expected_conf, expected_review",
    [
        ("goods_deep_discount", "OO 용역", "deep_discount", 0.86, False),
        ("service_high_negotiated", "OO 용역", "near_100", 0.84, False),
        ("service_price_competitive", "폐기물 처리용역", "floor_bound", 0.82, False),
        (None, "OO 용역", "ambiguous", 0.45, True),
    ],
)
def test_price_regime_label_routing(rate_band, description, expected_label, expected_conf, expected_review):
    context = PricePredictionContext(
        budget=100_000_000.0, category="service", description=description,
        historical_records=(), business_group="service",
    )
    features = _build_price_regime_features({"procurement_rate_band": rate_band}, context=context)
    assert features["price_regime_label"] == expected_label
    assert features["price_regime_confidence"] == expected_conf
    assert features["review_required"] is expected_review


def test_price_regime_features_signal_list_order_is_sorted():
    """The regime_signals list is emitted sorted — order lock for downstream diffs."""
    context = PricePredictionContext(
        budget=100_000_000.0, category="service", description="협상 폐기물 2단계",
        historical_records=(), business_group="service",
    )
    features = _build_price_regime_features(
        {"procurement_rate_band": "service_price_competitive"}, context=context
    )
    # (bare "2단계" without a spec/price cue no longer emits two_stage_or_separated)
    assert features["regime_signals"] == ["negotiated", "price_competitive"]
