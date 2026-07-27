"""Value-table + regression tests for the price-regime title-semantic guards.

WHY THESE MATTER
----------------
``_detect_price_regime_signals`` reads regulatory mechanism keywords out of the
assembled notice text (title+description+requirements, aligned by #239). Three
title cues produced false positives once the title reached this shared path:

  1. bare "2단계" from construction phase-2 ("○○ 2단계 증설공사") — NOT 규격·가격
     동시 2단계 입찰;
  2. "2단계" substring-matched inside "12단계"/"22단계";
  3. bare 견적/견적제출 inside a 수의(negotiated) context ("수의견적 제출") wrongly
     raising a price-competition signal → false near_100 ∧ floor_bound conflict.

A fourth guard (section 4) covers the same false-positive family on the 수의/
direct_negotiated cue itself, but scoped to 공사: a construction 수의 견적제출 is
priced off the 적격심사 낙찰하한율, not off a near-100 negotiation.

The guards live entirely in the DESCRIPTIVE metadata layer (regime label / signals
/ review_required), which is attached AFTER the price is finalized and has no
pricing consumer — so they must never move the recommended price. These tests lock
both the label semantics and that price invariance.
"""

import pytest

from app.ai.price_prediction import (
    _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL,
    _build_price_regime_features,
    _detect_price_regime_signals,
    _has_resolved_award_floor,
    _has_two_stage_cue,
    predict_price,
)
from app.ai.predictors import PricePredictionContext


def _flags(text, *, category="service", rate_band=None, award_floor_resolved=False):
    return _detect_price_regime_signals(
        category=category,
        text=text.lower(),
        rate_band=rate_band,
        award_floor_resolved=award_floor_resolved,
    )


def _two_stage(flags):
    return "two_stage_or_separated" in flags["signals"]


# --------------------------------------------------------------------------- #
# 1. two-stage guard: explicit markers fire; bare "2단계" needs a spec/price cue;
#    a digit immediately before "2단계" (12단계) is rejected.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text, expected",
    [
        # explicit 규격·가격 / 동시입찰 markers always fire
        ("수학여행 위탁용역 규격·가격 동시입찰", True),
        ("성능평가 용역 규격 가격 분리", True),
        ("물품 구매 동시 평가 방식", True),
        # bare "2단계" WITH a spec/price co-occurrence cue → genuine
        ("농산물 구매 2단계 규격 가격 분리 입찰", True),
        ("2단계 가격 경쟁 입찰", True),
        # bare "2단계" WITHOUT a spec/price cue → construction phase-2 노이즈
        ("정수장 2단계 증설공사", False),
        ("태봉근린공원(2단계) 통신공사", False),
        ("기초생활거점조성사업 2단계 지역역량강화용역", False),
        # digit-boundary guard: "12단계"/"22단계" are not "2단계"
        ("중독 12단계 회복 프로그램 앱 제작", False),
        ("22단계 확장 사업", False),
        # digit-boundary + a later co-occurrence cue must still reject the 12단계
        ("제12단계 정비 규격 검토", False),
    ],
)
def test_two_stage_cue_value_table(text, expected):
    assert _has_two_stage_cue(text.lower()) is expected
    assert _two_stage(_flags(text)) is expected


def test_two_stage_phase_noise_is_descriptive_only_for_construction():
    """A construction phase-2 notice drops the two_stage signal but keeps its
    label (the four label keys are untouched by two_stage for non-goods)."""
    flags = _flags("정수장 2단계 증설공사", category="construction", rate_band=None)
    assert _two_stage(flags) is False
    # none of the four label keys are raised by a phase-2 title
    assert flags["floor_bound"] is False
    assert flags["near_100"] is False
    assert flags["deep_discount"] is False
    assert flags["conflicting"] is False


# --------------------------------------------------------------------------- #
# 2. 수의견적 guard: a bare 견적 inside a 수의/negotiated context does not raise a
#    false conflict; genuine 가격입찰/적격심사/pq keep firing; goods 소액수의 견적
#    stays price-competitive (its goods band settles at the competitive rate).
# --------------------------------------------------------------------------- #


def test_negotiated_quote_suppresses_false_conflict_via_direct_context():
    """service_direct_negotiated band + bare 견적 제출 → near_100 (not ambiguous)."""
    flags = _flags(
        "살수차 임차 용역 견적 제출",
        category="service",
        rate_band="service_direct_negotiated",
    )
    assert flags["near_100"] is True
    assert flags["floor_bound"] is False
    assert flags["conflicting"] is False


def test_negotiated_quote_keyword_marks_direct_context():
    """A 수의견적(no space) title reads as a 수의 quote → near_100, quote suppressed.

    Scoped to a 용역 notice: for 공사 the same cue is re-routed by the section-4
    guard (공사 수의 견적은 적격심사 하한 경쟁이라 near_100 이 아니다).
    """
    flags = _flags("사무실 청소 용역 수의견적 제출 공고", category="service", rate_band=None)
    assert flags["near_100"] is True
    assert flags["floor_bound"] is False
    assert flags["conflicting"] is False
    assert "direct_negotiated" in flags["signals"]


def test_soaek_suui_quote_stays_price_competitive_for_goods():
    """Goods 소액수의 견적 keeps floor_bound — the goods band settles it competitively,
    so the "수의 견적"(space) substring must NOT flip it to near_100."""
    flags = _flags(
        "냉난방기 구매 소액수의 견적 제출", category="goods", rate_band="goods_price_competitive"
    )
    assert flags["floor_bound"] is True
    assert flags["near_100"] is False
    assert flags["conflicting"] is False


def test_soaek_suui_quote_service_competitive_band_not_forced_to_conflict():
    """A service whose band is competitive (e.g. 폐기물) with 소액수의 견적 must stay
    floor_bound — the space-substring collision that produced a false conflict is
    guarded by the no-space 수의견적 cue."""
    flags = _flags(
        "일반폐기물 처리 용역 소액수의 견적 제출",
        category="service",
        rate_band="service_price_competitive",
    )
    assert flags["floor_bound"] is True
    assert flags["near_100"] is False
    assert flags["conflicting"] is False


def test_genuine_price_competition_still_fires():
    """가격입찰 / 적격심사 / pq are strong cues: always raise floor_bound."""
    for text in ("pq 후 가격입찰", "적격심사 대상 용역", "가격입찰 방식"):
        flags = _flags(text, category="service", rate_band=None)
        assert flags["floor_bound"] is True, text


def test_genuine_conflict_preserved():
    """협상 + 가격입찰 in one notice is genuinely conflicting → stays ambiguous-worthy."""
    flags = _flags(
        "연구용역 협상에 의한 계약 및 가격입찰 동시 평가", category="service", rate_band=None
    )
    assert flags["near_100"] is True
    assert flags["floor_bound"] is True
    assert flags["conflicting"] is True


# --------------------------------------------------------------------------- #
# 2b. Coverage-gap locks (ml-reviewer, non-blocking): pin behavior that already
#     holds so future edits cannot silently drift it.
# --------------------------------------------------------------------------- #


def test_goods_food_special_case_gated_by_two_stage_cue():
    """The goods 급식/농산물 special case (line ~399: forces deep_discount, clears
    floor_bound) is gated on has_two_stage. With the phase-noise guard, a bare "2단계"
    급식/농산물 notice that lacks a 규격/가격/동시 cue no longer trips has_two_stage, so
    the special case does NOT fire — the label follows the (competitive) band. The
    positive counterpart with a 규격·가격 cue still fires the special case."""
    # negative: bare "2단계" + 급식/농산물, competitive band, NO spec/price cue →
    # special case does NOT force deep_discount; floor_bound follows the band.
    negative = _flags(
        "학교 급식 농산물 구매 2단계 입찰", category="goods", rate_band="goods_price_competitive"
    )
    assert negative == {
        "floor_bound": True,
        "near_100": False,
        "deep_discount": False,
        "conflicting": False,
        "signals": ["price_competitive"],
    }
    assert _two_stage(negative) is False

    # positive: SAME 급식/농산물 goods notice WITH a 규격·가격 cue → has_two_stage=True →
    # special case fires → deep_discount label (floor_bound cleared).
    positive = _flags(
        "규격·가격 2단계 급식 농산물 구매", category="goods", rate_band="goods_price_competitive"
    )
    assert positive == {
        "floor_bound": False,
        "near_100": False,
        "deep_discount": True,
        "conflicting": False,
        "signals": ["deep_discount", "price_competitive", "two_stage_or_separated"],
    }
    assert _two_stage(positive) is True


def test_negotiated_quote_plus_strong_cue_errs_toward_review():
    """When 수의견적 (near_100 context) AND a strong price cue (적격심사/가격입찰/pq)
    genuinely co-occur, the strong cue is never suppressed → floor_bound stays True
    alongside near_100 → conflicting=True → ambiguous/review. The guard deliberately
    errs toward MORE review (safe side) on a real dual signal, never fewer."""
    flags = _flags(
        "청소 용역 수의견적 제출 적격심사 대상", category="service", rate_band=None
    )
    assert flags == {
        "floor_bound": True,
        "near_100": True,
        "deep_discount": False,
        "conflicting": True,
        "signals": ["direct_negotiated", "price_competitive"],
    }


# --------------------------------------------------------------------------- #
# 3. price invariance: the guards are descriptive-only and must not move the
#    recommended price. A phase-noise notice and a genuine two-stage notice with
#    the same base amount/history produce identical price fields; only the regime
#    metadata differs.
# --------------------------------------------------------------------------- #


_HISTORY = [
    {"bid_rate": r, "base_amount": 100_000_000.0}
    for r in [0.90, 0.905, 0.91, 0.915, 0.92, 0.925, 0.93, 0.935, 0.94, 0.945]
]

_PRICE_FIELDS = (
    "predicted_bid_rate",
    "floor_bid_rate",
    "safe_floor_bid_rate",
    "ceiling_bid_rate",
    "procurement_rate_band",
)


def test_phase_noise_two_stage_does_not_move_price():
    """Construction phase-2 title vs a plain construction title → identical price
    fields (the dropped two_stage signal is metadata, not a price input)."""
    phase = predict_price(
        budget=100_000_000.0,
        category="construction",
        description="정수장 2단계 증설공사",
        historical_records=_HISTORY,
    )
    plain = predict_price(
        budget=100_000_000.0,
        category="construction",
        description="정수장 증설공사",
        historical_records=_HISTORY,
    )
    assert _two_stage(
        _detect_price_regime_signals(
            category="construction", text="정수장 2단계 증설공사", rate_band=None
        )
    ) is False
    for field in _PRICE_FIELDS:
        assert phase.get(field) == plain.get(field), field


def test_negotiated_quote_label_flip_keeps_guardrail_floor():
    """A 수의견적 service still respects the guardrail floor (미하회 없음)."""
    pred = predict_price(
        budget=100_000_000.0,
        category="service",
        description="살수차 임차 용역 수의견적 제출 공고",
        historical_records=_HISTORY,
        legal_floor_bid_rate=87.995,
    )
    rate = pred["predicted_bid_rate"]
    floor = pred.get("floor_bid_rate")
    if floor is not None:
        assert rate >= floor - 1e-9


# --------------------------------------------------------------------------- #
# 4. 공사 수의 견적 guard: in 공사, a 수의 cue on its own is NOT a near-100 pricing
#    mechanism — 공사 소액수의 견적제출 prices off the 적격심사 낙찰하한율 ("하한 이상
#    최저가"). Audit (2026-07-27 holdout clean): all 470 construction near_100 rows
#    fired on the lone direct_negotiated signal, yet only 9.1% were awarded at
#    >=0.97 while 76.8% landed within +100bp of the award floor (median 0.90).
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "category, text, award_floor_resolved, expected_near_100, expected_floor_bound, expected_guard",
    [
        # 공사 + 수의 단서 단독 + 해석된 낙찰하한 → floor_bound 로 재라우팅
        ("construction", "보행로 정비공사 수의견적 제출 공고", True, False, True, True),
        ("construction", "○○ 배수로 정비공사 수의계약 안내", True, False, True, True),
        # 하한 근거가 없으면 floor_bound 로 올리지 않는다 → 기존 fallback(ambiguous)
        ("construction", "보행로 정비공사 수의견적 제출 공고", False, False, False, True),
        # 공사라도 협상 단서가 함께면 near_100 증거가 수의 단독이 아니다 → 기존 유지
        ("construction", "○○ 정비공사 수의계약 협상에 의한 계약", True, True, False, False),
        # 용역/물품 수의시담은 near_100 정의가 유효 → 불변
        ("service", "사무실 청소 용역 수의견적 제출", True, True, False, False),
        ("service", "수의시담 실시설계 용역", True, True, False, False),
        ("goods", "제설자재 구매 수의계약 안내", True, True, False, False),
        # 수의 단서가 없는 공사 공고는 가드와 무관
        ("construction", "정수장 증설공사", True, False, False, False),
    ],
)
def test_construction_negotiated_quote_guard_value_table(
    category, text, award_floor_resolved, expected_near_100, expected_floor_bound, expected_guard
):
    flags = _flags(text, category=category, award_floor_resolved=award_floor_resolved)
    assert flags["near_100"] is expected_near_100
    assert flags["floor_bound"] is expected_floor_bound
    assert flags["conflicting"] is False
    assert (
        _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL in flags["signals"]
    ) is expected_guard
    # the 수의 fact itself stays on the record whenever the cue is present
    if expected_guard:
        assert "direct_negotiated" in flags["signals"]


def test_construction_negotiated_quote_with_strong_price_cue_is_floor_bound_not_conflict():
    """공사 수의견적 + 적격심사: the strong cue and the guard AGREE (both floor-bound),
    so this is no longer the false near_100 ∧ floor_bound conflict it produces for a
    용역 notice (see ``test_negotiated_quote_plus_strong_cue_errs_toward_review``)."""
    flags = _flags(
        "보행로 정비공사 수의견적 제출 적격심사 대상",
        category="construction",
        award_floor_resolved=True,
    )
    assert flags == {
        "floor_bound": True,
        "near_100": False,
        "deep_discount": False,
        "conflicting": False,
        "signals": [
            _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL,
            "direct_negotiated",
            "price_competitive",
        ],
    }


def _features(description, prediction):
    context = PricePredictionContext(
        budget=100_000_000.0,
        category="construction",
        description=description,
        historical_records=(),
        business_group="construction",
    )
    return _build_price_regime_features(prediction, context=context)


def test_construction_negotiated_quote_label_follows_resolved_award_floor():
    """End-to-end label routing: with a resolved 낙찰하한 the guarded row reads
    floor_bound; without one it falls through to the ambiguous fallback (review)."""
    described = _features(
        "보행로 정비공사 수의견적 제출 공고",
        {"procurement_rate_band": None, "floor_bid_rate": 0.89745},
    )
    assert described["price_regime_label"] == "floor_bound"
    assert described["review_required"] is False
    # audit trail: guard reason + the 수의 contract method both survive
    assert _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL in described["regime_signals"]
    assert described["contract_method"] == "direct_negotiated"
    assert described["award_method"] == "price_competition"

    floorless = _features("보행로 정비공사 수의견적 제출 공고", {"procurement_rate_band": None})
    assert floorless["price_regime_label"] == "ambiguous"
    assert floorless["price_regime_confidence"] == 0.45
    assert floorless["review_required"] is True
    assert _CONSTRUCTION_NEGOTIATED_QUOTE_GUARD_SIGNAL in floorless["regime_signals"]


@pytest.mark.parametrize(
    "prediction, expected",
    [
        ({"legal_floor_bid_rate": 0.89745, "floor_bid_rate": 0.89745}, True),
        ({"legal_floor_bid_rate": None, "floor_bid_rate": 0.88}, True),
        ({"legal_floor_bid_rate": 0.87995}, True),
        ({"legal_floor_bid_rate": None, "floor_bid_rate": None}, False),
        ({}, False),
    ],
)
def test_has_resolved_award_floor_value_table(prediction, expected):
    assert _has_resolved_award_floor(prediction) is expected


def test_construction_guard_does_not_move_price(monkeypatch):
    """PRICE INVARIANCE (the #240 obligation): disabling the guard changes ONLY the
    regime metadata keys — every other field of the prediction, including every
    price/rate/candidate field, is byte-identical."""
    from app.ai.price_prediction import price_regime

    kwargs = dict(
        budget=100_000_000.0,
        category="construction",
        description="보행로 정비공사 수의견적 제출 공고",
        historical_records=_HISTORY,
        legal_floor_bid_rate=89.745,
    )
    guarded = predict_price(**kwargs)

    monkeypatch.setattr(
        price_regime, "_is_construction_negotiated_quote_only", lambda **_: False
    )
    unguarded = predict_price(**kwargs)

    regime_keys = {
        "price_regime_features",
        "price_regime_label",
        "price_regime_confidence",
        "review_required",
        "recommended_candidate_label",
        "recommended_selector_reason",
    }
    assert guarded.keys() == unguarded.keys()
    for key in guarded.keys() - regime_keys:
        assert guarded[key] == unguarded[key], key

    # the guard did fire (otherwise this test would prove nothing)
    assert unguarded["price_regime_label"] == "near_100"
    assert guarded["price_regime_label"] == "floor_bound"
    # and the recommended price still respects the guardrail floor (미하회 없음)
    assert guarded["predicted_bid_rate"] >= guarded["floor_bid_rate"] - 1e-9
