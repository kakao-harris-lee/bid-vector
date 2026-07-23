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

The guards live entirely in the DESCRIPTIVE metadata layer (regime label / signals
/ review_required), which is attached AFTER the price is finalized and has no
pricing consumer — so they must never move the recommended price. These tests lock
both the label semantics and that price invariance.
"""

import pytest

from app.ai.price_prediction import (
    _detect_price_regime_signals,
    _has_two_stage_cue,
    predict_price,
)


def _flags(text, *, category="service", rate_band=None):
    return _detect_price_regime_signals(
        category=category, text=text.lower(), rate_band=rate_band
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
    """A 수의견적(no space) title reads as a 수의 quote → near_100, quote suppressed."""
    flags = _flags("보행로 정비공사 수의견적 제출 공고", category="construction", rate_band=None)
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
