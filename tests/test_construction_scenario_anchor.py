"""공사 시나리오 era-correct 법정 하한 앵커(공격/추천/안전) 회귀 테스트.

앵커 규칙(2026-07-17 캘리브레이션, n=2,051): construction 이고 era-correct tier
floor(#197)가 해석될 때, 3종 시나리오를 무변환 floor + 선언 offset(p25/p50/p75)으로
앵커한다. 발주처 밴드가 있으면 밴드가 우선(앵커 미적용). guardrail floor/ceiling 자체는
불변(red line) — 앵커 결과는 항상 [floor, ceiling] 안이다.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.ai.bid_target import CAVEAT, build_bid_target_menu
from app.ai.construction_scenario import (
    is_construction_era_floor_resolved,
    resolve_scenario_anchor_rates,
)
from app.ai.price_prediction import predict_price

# 캘리브레이션 offset(Settings 기본값과 일치). 테스트는 이 값을 명시적으로 주입해
# 호스트 .env 오버라이드로부터 hermetic 하게 유지한다.
_OFFSETS = {"aggressive": 0.0016, "recommended": 0.0053, "safe": 0.0148}
# 신율(2026-01-30+) <10억 tier floor(예정가-basis, 무변환 E[사정률]=1.0).
_NEW_ERA_SUB_10EOK_FLOOR = 0.89745
_CONSTRUCTION_CEILING = 0.93


@pytest.fixture(autouse=True)
def _pin_settings(monkeypatch):
    """앵커/가드레일 관련 knob 을 기본값으로 고정(호스트 .env 무관 재현성)."""
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS", dict(_OFFSETS)
    )
    monkeypatch.setattr(
        settings,
        "PREDICTION_CATEGORY_MINIMUM_BID_RATES",
        {"construction": 0.87, "service": 0.87, "goods": 0.84},
    )
    monkeypatch.setattr(
        settings,
        "PREDICTION_CATEGORY_MAXIMUM_BID_RATES",
        {"construction": _CONSTRUCTION_CEILING, "service": 1.0, "goods": 1.0},
    )
    monkeypatch.setattr(settings, "PREDICTION_FLOOR_SAFETY_MARGIN_RATE", 0.001)
    monkeypatch.setattr(settings, "PREDICTION_DEFAULT_BAND_ASSESSMENT_RATE", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MINIMUM_BID_RATES", {})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_MAXIMUM_BID_RATES", {})
    monkeypatch.setattr(settings, "PREDICTION_AGENCY_BAND_ASSESSMENT_RATES", {})


def _predict_construction(
    *,
    budget=500_000_000.0,
    estimation_amount=500_000_000.0,
    reference_date=date(2026, 2, 1),
    category="construction",
    agency_name=None,
):
    return predict_price(
        budget=budget,
        category=category,
        description="OO 토목공사 법정하한 검증",
        historical_records=[],
        agency_name=agency_name,
        estimation_amount=estimation_amount,
        reference_date=reference_date,
    )


def _candidates(prediction):
    return {
        c["label"]: round(float(c["bid_rate"]), 5)
        for c in prediction["bid_rate_candidates"]
    }


# ---------------------------------------------------------------------------
# 값 테이블 — 신율 <10억(floor 0.89745)
# ---------------------------------------------------------------------------
def test_value_table_new_era_sub_10eok_candidates():
    """공격=floor+0.0016 / 추천=floor+0.0053 / 안전=floor+0.0148 (후보 매핑)."""
    prediction = _predict_construction()
    cands = _candidates(prediction)
    # 후보 taxonomy: conservative=공격(p25), base=추천(p50), aggressive=안전(p75).
    assert cands["conservative"] == 0.89905  # 공격
    assert cands["base"] == 0.90275  # 추천
    assert cands["aggressive"] == 0.91225  # 안전
    assert round(float(prediction["predicted_bid_rate"]), 5) == 0.90275


def test_value_table_new_era_sub_10eok_menu():
    """메뉴 공격/추천/안전 라벨이 정확한 앵커 rate 를 낸다."""
    menu = build_bid_target_menu(
        floor_bid_rate=_NEW_ERA_SUB_10EOK_FLOOR,
        ceiling_bid_rate=_CONSTRUCTION_CEILING,
        budget=500_000_000.0,
        signals=None,
        floor_anchor_offsets=_OFFSETS,
    )
    rates = {o["label"]: round(float(o["bid_rate"]), 5) for o in menu["options"]}
    assert rates["aggressive"] == 0.89905
    assert rates["recommended"] == 0.90275
    assert rates["safe"] == 0.91225
    # 라벨/순서 계약 유지.
    assert [o["label"] for o in menu["options"]] == [
        "recommended",
        "aggressive",
        "safe",
    ]


# ---------------------------------------------------------------------------
# era/구간 변화 시 앵커가 해당 floor 를 따라 이동
# ---------------------------------------------------------------------------
def test_anchor_tracks_old_era_floor():
    """구율(2026-01-30 이전) <10억 floor 0.87745 위로 같은 offset 이 앵커된다."""
    prediction = _predict_construction(reference_date=date(2025, 6, 1))
    assert round(float(prediction["floor_bid_rate"]), 5) == 0.87745
    cands = _candidates(prediction)
    assert cands["conservative"] == round(0.87745 + 0.0016, 5)
    assert cands["base"] == round(0.87745 + 0.0053, 5)
    assert cands["aggressive"] == round(0.87745 + 0.0148, 5)


def test_anchor_tracks_10_to_50eok_bracket():
    """신율 10~50억 구간 floor 0.88745 위로 앵커가 이동한다."""
    prediction = _predict_construction(
        budget=2_000_000_000.0, estimation_amount=2_000_000_000.0
    )
    assert round(float(prediction["floor_bid_rate"]), 5) == 0.88745
    assert _candidates(prediction)["base"] == round(0.88745 + 0.0053, 5)


# ---------------------------------------------------------------------------
# 게이트 — tier 미해석/비공사이면 앵커 미적용(기존 동작)
# ---------------------------------------------------------------------------
def test_no_anchor_without_estimation_amount():
    prediction = _predict_construction(estimation_amount=None, budget=100_000_000.0)
    assert "앵커" not in prediction.get("explanation", "")


def test_no_anchor_without_reference_date():
    prediction = _predict_construction(reference_date=None)
    assert "앵커" not in prediction.get("explanation", "")


def test_no_anchor_for_jongsimje_over_100eok():
    """추정가격 100억 이상은 종심제 — tier 표 대상 아님 → 앵커 미적용."""
    prediction = _predict_construction(
        budget=12_000_000_000.0, estimation_amount=12_000_000_000.0
    )
    assert "앵커" not in prediction.get("explanation", "")


def test_no_anchor_for_non_construction():
    prediction = _predict_construction(category="service")
    assert "앵커" not in prediction.get("explanation", "")


# ---------------------------------------------------------------------------
# red line — guardrail floor/ceiling 불변, 앵커는 항상 [floor, ceiling] 안
# ---------------------------------------------------------------------------
def test_red_line_anchor_within_guardrail_band():
    prediction = _predict_construction()
    floor = float(prediction["floor_bid_rate"])
    ceiling = float(prediction["ceiling_bid_rate"])
    assert floor == _NEW_ERA_SUB_10EOK_FLOOR  # floor 자체 불변
    assert ceiling == _CONSTRUCTION_CEILING  # ceiling 자체 불변(0.93)
    for c in prediction["bid_rate_candidates"]:
        assert floor <= float(c["bid_rate"]) <= ceiling


def test_red_line_ceiling_clamps_oversized_offset(monkeypatch):
    """offset 이 ceiling 을 넘겨도 결과는 ceiling 이하로 clamp — ceiling 은 불변."""
    huge = {"aggressive": 0.5, "recommended": 0.6, "safe": 0.7}
    anchored = resolve_scenario_anchor_rates(
        floor_bid_rate=_NEW_ERA_SUB_10EOK_FLOOR,
        ceiling_bid_rate=_CONSTRUCTION_CEILING,
        offsets=huge,
    )
    assert all(v <= _CONSTRUCTION_CEILING for v in anchored.values())
    assert max(anchored.values()) == _CONSTRUCTION_CEILING


def test_red_line_anchor_never_below_floor():
    """offset 이 음수여도 결과는 floor 이상(공격 낙하 하한 보호)."""
    anchored = resolve_scenario_anchor_rates(
        floor_bid_rate=_NEW_ERA_SUB_10EOK_FLOOR,
        ceiling_bid_rate=_CONSTRUCTION_CEILING,
        offsets={"aggressive": -0.05, "recommended": 0.0053, "safe": 0.0148},
    )
    assert anchored["aggressive"] == _NEW_ERA_SUB_10EOK_FLOOR


# ---------------------------------------------------------------------------
# basis 일관성 — 앵커는 무변환 floor 위(이중 보정 금지)
# ---------------------------------------------------------------------------
def test_basis_consistency_anchor_on_unconverted_floor():
    """기본 E[사정률]=1.0 에서 base 후보 == 무변환 tier floor + recommended offset."""
    prediction = _predict_construction()
    assert round(float(prediction["floor_bid_rate"]), 5) == _NEW_ERA_SUB_10EOK_FLOOR
    assert _candidates(prediction)["base"] == round(
        _NEW_ERA_SUB_10EOK_FLOOR + _OFFSETS["recommended"], 5
    )


# ---------------------------------------------------------------------------
# 발주처 밴드 우선 — agency 밴드가 있으면 floor 앵커 미적용
# ---------------------------------------------------------------------------
def test_agency_band_takes_priority_over_anchor(monkeypatch):
    """agency floor 밴드가 있으면(공사라도) 앵커 대신 밴드가 우선한다."""
    from app.core.config import settings

    monkeypatch.setattr(
        settings, "PREDICTION_AGENCY_MINIMUM_BID_RATES", {"테스트기관": 0.905}
    )
    prediction = _predict_construction(agency_name="테스트기관")
    assert prediction.get("floor_from_agency") is True
    # 앵커 문구 없음(밴드 경로) — floor 는 agency 밴드 0.905.
    assert "앵커" not in prediction.get("explanation", "")


# ---------------------------------------------------------------------------
# 정직 명세 — 낙찰 확률 표현 금지, 공격 낙하 경고, percentile 근거 노출
# ---------------------------------------------------------------------------
def test_honesty_menu_no_probability_and_caveat():
    menu = build_bid_target_menu(
        floor_bid_rate=_NEW_ERA_SUB_10EOK_FLOOR,
        ceiling_bid_rate=_CONSTRUCTION_CEILING,
        budget=500_000_000.0,
        signals=None,
        floor_anchor_offsets=_OFFSETS,
    )
    assert menu["caveat"] == CAVEAT
    blob = str(menu).lower()
    assert "확률" not in blob and "probability" not in blob and "승률" not in blob
    # percentile 근거가 드러남(단정 아님).
    assert "백분위수" in menu["signals_summary"]
    aggressive = next(o for o in menu["options"] if o["label"] == "aggressive")
    assert "낙" in aggressive["risk_note"]  # 낙하(실격) 위험 경고 유지


def test_honesty_prediction_explanation_reveals_percentile_basis():
    prediction = _predict_construction()
    explanation = prediction.get("explanation", "")
    assert "백분위수" in explanation and "앵커" in explanation
    assert "확률" not in explanation


# ---------------------------------------------------------------------------
# 순수 헬퍼 단위 테스트
# ---------------------------------------------------------------------------
def test_is_construction_era_floor_resolved_gate():
    assert is_construction_era_floor_resolved(
        "construction", 500_000_000.0, date(2026, 2, 1)
    )
    assert not is_construction_era_floor_resolved(
        "service", 500_000_000.0, date(2026, 2, 1)
    )
    assert not is_construction_era_floor_resolved(
        "construction", None, date(2026, 2, 1)
    )
    assert not is_construction_era_floor_resolved("construction", 500_000_000.0, None)
    # 100억+ 종심제 → 미해석.
    assert not is_construction_era_floor_resolved(
        "construction", 12_000_000_000.0, date(2026, 2, 1)
    )


def test_resolve_scenario_anchor_rates_none_when_no_floor():
    assert (
        resolve_scenario_anchor_rates(
            floor_bid_rate=None, ceiling_bid_rate=0.93, offsets=_OFFSETS
        )
        is None
    )
    assert (
        resolve_scenario_anchor_rates(
            floor_bid_rate=0.89745, ceiling_bid_rate=0.93, offsets=None
        )
        is None
    )
