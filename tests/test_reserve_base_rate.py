"""Tests for the reserve-price (복수예비가격) prior wired into base-rate selection.

PR4 / item 2b: the reserve mechanism statistics that were previously summarized
but never consumed are now blended into ``select_competitive_base_rate`` as a
small, sample-scaled prior. These tests pin four properties:

(a) a low reserve-implied rate pulls the base rate toward it, but the final
    category floor guardrail still blocks anything below the bidding floor;
(b) ``reserve_context=None`` reproduces the legacy output byte-for-byte
    (no-drift for every existing caller / fixture);
(c) leakage: the predictor only sees the records it is handed — the reserve
    context is derived from those same prior records, never from the target's
    own opening;
(d) the blend weight scales with the reserve sample_count (thin evidence nudges
    only slightly).
"""

from __future__ import annotations

import pytest

from app.ai.predictors.historical import (
    blend_reserve_prior,
    build_historical_prediction,
    build_heuristic_prediction,
    resolve_reserve_prior_rate,
    select_competitive_base_rate,
    summarize_historical_records,
)
from app.ai.price_prediction import _resolve_floor_bid_rate, predict_price


def _base_rate_kwargs(**overrides):
    """Deep-history construction scenario with a stable ~0.95 statistical base."""
    kwargs = dict(
        category="construction",
        description="OO 토목공사",  # no rate-band keyword for construction
        sample_size=20,
        mean_rate=0.95,
        median_rate=0.95,
        recent_median_rate=0.95,
        competitive_quantile_rate=0.95,
        heuristic_rate=0.95,
        business_group=None,
    )
    kwargs.update(overrides)
    return kwargs


def _reserve_context(
    *,
    implied_rate: float,
    sample_count: int,
    estimated_count: int | None = None,
    estimated_price_rate: float = 1.0,
):
    """Build a reserve context.

    ``implied_rate`` is ``median_bid_to_estimated_price_rate`` (bid/예가);
    ``estimated_price_rate`` is ``median_estimated_price_rate`` (예가율 = 예가/budget).
    With the default ``estimated_price_rate=1.0`` the bid/예가 → bid/budget
    conversion is the identity, so ``implied_rate`` doubles as the bid/budget
    value for tests that do not exercise the unit conversion.
    """
    return {
        "sample_count": sample_count,
        "estimated_price_sample_count": estimated_count
        if estimated_count is not None
        else sample_count,
        "average_reserve_span_rate": 0.03,
        "average_estimated_price_rate": estimated_price_rate,
        "median_estimated_price_rate": estimated_price_rate,
        "median_bid_to_estimated_price_rate": implied_rate,
        "average_selected_number": 7.5,
        "frequent_selected_numbers": [1, 4, 7, 12],
    }


# ---------------------------------------------------------------------------
# (b) no-drift: reserve_context=None reproduces legacy output exactly
# ---------------------------------------------------------------------------


def test_reserve_context_none_is_byte_identical_to_legacy():
    """Default reserve_context=None must not change the existing base rate at all."""
    kwargs = _base_rate_kwargs()
    # Call without the new parameter (legacy signature usage).
    legacy = select_competitive_base_rate(**kwargs)
    # Call explicitly with None.
    explicit_none = select_competitive_base_rate(**kwargs, reserve_context=None)
    assert explicit_none == legacy


@pytest.mark.parametrize(
    "scenario",
    [
        _base_rate_kwargs(sample_size=20),
        _base_rate_kwargs(sample_size=8),
        _base_rate_kwargs(sample_size=3),
        _base_rate_kwargs(sample_size=1),
        _base_rate_kwargs(business_group="construction", sample_size=20),
        _base_rate_kwargs(
            business_group="service",
            category="service",
            description="OO 청소용역",
            sample_size=20,
        ),
        _base_rate_kwargs(category="service", description="OO 청소용역", sample_size=20),
    ],
)
def test_reserve_context_none_no_drift_across_branches(scenario):
    """Every branch of select_competitive_base_rate is unchanged when no reserve context."""
    assert select_competitive_base_rate(**scenario) == select_competitive_base_rate(
        **scenario, reserve_context=None
    )


def test_build_historical_prediction_without_reserve_is_unchanged():
    """A history summary with no reserve evidence yields the same prediction as before."""
    heuristic = build_heuristic_prediction(
        budget=100_000_000.0, category="construction", description="OO 공사"
    )
    summary = {
        "sample_size": 12,
        "mean_bid_rate": 0.95,
        "median_bid_rate": 0.95,
        "recent_median_bid_rate": 0.95,
        "competitive_quantile_bid_rate": 0.95,
        "std_bid_rate": 0.01,
        "agency_match_sample_size": 0,
        "reserve_price_context": None,
    }
    prediction = build_historical_prediction(
        budget=100_000_000.0,
        category="construction",
        description="OO 공사",
        heuristic_prediction=heuristic,
        historical_summary=summary,
        business_group=None,
    )
    # base rate stays at the pure statistical target (0.95) — no reserve nudge.
    assert prediction["predicted_bid_rate"] == pytest.approx(0.95, abs=1e-4)


# ---------------------------------------------------------------------------
# (a) reserve prior pulls base rate but the category floor still wins
# ---------------------------------------------------------------------------


def test_reserve_prior_pulls_base_rate_toward_implied_rate():
    """A lower reserve-implied rate should pull the base rate below the pure base."""
    kwargs = _base_rate_kwargs()
    pure = select_competitive_base_rate(**kwargs)
    with_reserve = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(implied_rate=0.88, sample_count=10),
    )
    assert with_reserve < pure
    # but it must not overshoot below the reserve-implied rate itself
    assert with_reserve >= 0.88


def test_reserve_prior_never_breaches_category_floor_via_guardrail():
    """Even an extreme low reserve-implied rate cannot push the recommendation below floor."""
    floor = _resolve_floor_bid_rate("construction")
    assert floor is not None and floor > 0

    # 30 construction openings whose 예가-대비 투찰률 is far below the floor.
    reserve_prices = [99_000_000.0 + (index * 100_000.0) for index in range(15)]
    history = [
        {
            "bid_rate": 0.72,  # well below the 0.87 construction floor
            "base_amount": 100_000_000.0,
            "reserve_prices": reserve_prices,
            "selected_numbers": [1, 4, 7, 12],
        }
        for _ in range(30)
    ]
    prediction = predict_price(
        budget=100_000_000.0,
        category="construction",
        description="복수예가 floor 검증 토목공사",
        historical_records=history,
    )
    # final guardrail must clamp every scenario at/above the category floor.
    assert prediction["predicted_bid_rate"] >= floor - 1e-9
    for candidate in prediction["bid_rate_candidates"]:
        assert candidate["bid_rate"] >= floor - 1e-9
    # guardrail should have fired (base would otherwise have been pulled under floor).
    assert prediction["floor_bid_rate"] == pytest.approx(floor, abs=1e-4)


def test_reserve_prior_blend_is_upstream_of_band_clamp():
    """A high reserve-implied rate cannot push a price-competitive band above its 0.90 cap."""
    rate = select_competitive_base_rate(
        category="service",
        description="폐기물 처리용역",  # service_price_competitive band → 0.90 cap
        sample_size=20,
        mean_rate=0.95,
        median_rate=0.95,
        recent_median_rate=0.95,
        competitive_quantile_rate=0.95,
        heuristic_rate=0.95,
        business_group="service",
        reserve_context=_reserve_context(implied_rate=1.05, sample_count=10),
    )
    assert rate <= 0.90


# ---------------------------------------------------------------------------
# (d) sample_count scales the prior weight
# ---------------------------------------------------------------------------


def test_reserve_prior_weight_scales_with_sample_count():
    """Thin reserve evidence nudges less than deep evidence (monotonic toward implied)."""
    kwargs = _base_rate_kwargs()
    pure = select_competitive_base_rate(**kwargs)
    implied = 0.88

    thin = select_competitive_base_rate(
        **kwargs, reserve_context=_reserve_context(implied_rate=implied, sample_count=1)
    )
    medium = select_competitive_base_rate(
        **kwargs, reserve_context=_reserve_context(implied_rate=implied, sample_count=4)
    )
    deep = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(implied_rate=implied, sample_count=16),
    )

    # all pull below the pure base toward the (lower) implied rate
    assert pure > thin > medium > deep >= implied
    # thin evidence barely moves it; deep evidence moves it much more
    assert (pure - thin) < (pure - deep)


def test_reserve_prior_ignored_without_estimated_price_evidence():
    """A reserve context with no estimated-price samples leaves the base rate untouched."""
    kwargs = _base_rate_kwargs()
    pure = select_competitive_base_rate(**kwargs)
    no_estimate = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(
            implied_rate=0.88, sample_count=10, estimated_count=0
        ),
    )
    assert no_estimate == pure


def test_resolve_reserve_prior_rate_rejects_out_of_band_rates():
    """Implausible converted bid/budget rates are discarded so they never skew base."""
    assert resolve_reserve_prior_rate(None) == (0.0, 0)
    assert resolve_reserve_prior_rate({}) == (0.0, 0)
    # out-of-band bid/예가 rate (converts to 2.0 at 예가율=1.0)
    assert resolve_reserve_prior_rate(
        _reserve_context(implied_rate=2.0, sample_count=10)
    ) == (0.0, 0)
    # usable — at 예가율=1.0 the conversion is the identity.
    implied, count = resolve_reserve_prior_rate(
        _reserve_context(implied_rate=0.9, sample_count=6)
    )
    assert implied == pytest.approx(0.9)
    assert count == 6


# ---------------------------------------------------------------------------
# unit-mismatch fix: bid/예가 must be converted to bid/budget before blending,
# otherwise the prior over-bids systematically because 예가율 < 1 on KONEPS.
# ---------------------------------------------------------------------------


def test_resolve_reserve_prior_rate_converts_to_bid_over_budget_units():
    """resolve_reserve_prior_rate returns bid/budget, i.e. bid/예가 × 예가율."""
    # operator bid/예가 = 0.946, 예가율 = 0.93 → bid/budget ≈ 0.880
    implied, count = resolve_reserve_prior_rate(
        _reserve_context(
            implied_rate=0.946, sample_count=10, estimated_price_rate=0.93
        )
    )
    assert implied == pytest.approx(0.946 * 0.93, abs=1e-6)  # ≈ 0.8798
    assert count == 10


def test_reserve_prior_does_not_overbid_when_yega_rate_below_one():
    """The real bug: at 예가율 0.93 a 0.88 (bid/budget) operator must not be pulled up.

    Before the fix, blending the raw bid/예가 ratio (0.946) against a base of
    0.88 dragged the recommendation UP toward 0.946 — a systematic over-bid.
    After converting to bid/budget (0.946 × 0.93 ≈ 0.880), the prior agrees with
    the operator's true bid/budget and does not push the base above 0.88.
    """
    kwargs = _base_rate_kwargs(
        mean_rate=0.88,
        median_rate=0.88,
        recent_median_rate=0.88,
        competitive_quantile_rate=0.88,
        heuristic_rate=0.88,
    )
    pure = select_competitive_base_rate(**kwargs)
    assert pure == pytest.approx(0.88, abs=1e-4)

    # bid/예가 = 0.946 looks like an over-bid in raw units, but 예가율 0.93 maps it
    # back to ≈ 0.880 in bid/budget.
    with_reserve = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(
            implied_rate=0.946, sample_count=16, estimated_price_rate=0.93
        ),
    )
    # The recommendation must NOT be pulled above the operator's 0.88 base.
    assert with_reserve <= 0.88 + 1e-4
    # And it should stay essentially at the operator's true bid/budget level.
    assert with_reserve == pytest.approx(0.88, abs=2e-3)


def test_reserve_prior_unconverted_would_have_overbid_regression_guard():
    """Direct contrast: the converted prior is far below the raw bid/예가 ratio.

    Documents the magnitude of the corrected bias — the raw ratio (0.946) sits
    well above the operator base (0.88), while the converted prior (≈0.880) does
    not, so the blend can no longer drag the base upward.
    """
    raw_ratio = 0.946
    yega_rate = 0.93
    converted, _ = resolve_reserve_prior_rate(
        _reserve_context(
            implied_rate=raw_ratio, sample_count=10, estimated_price_rate=yega_rate
        )
    )
    assert raw_ratio > 0.88  # raw ratio would have over-bid
    assert converted < raw_ratio  # conversion strictly lowers it
    assert converted == pytest.approx(0.8798, abs=1e-3)


def test_reserve_prior_falls_back_without_valid_yega_rate():
    """Missing / zero / out-of-band 예가율 disables the prior (base unchanged)."""
    kwargs = _base_rate_kwargs()
    pure = select_competitive_base_rate(**kwargs)

    # 예가율 = 0 → cannot convert → no prior.
    zero_yega = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(
            implied_rate=0.946, sample_count=16, estimated_price_rate=0.0
        ),
    )
    assert zero_yega == pure

    # 예가율 out of band (>1.5) → no prior.
    high_yega = select_competitive_base_rate(
        **kwargs,
        reserve_context=_reserve_context(
            implied_rate=0.946, sample_count=16, estimated_price_rate=1.6
        ),
    )
    assert high_yega == pure

    # 예가율 key entirely absent → no prior.
    context = _reserve_context(implied_rate=0.946, sample_count=16)
    context.pop("median_estimated_price_rate")
    assert resolve_reserve_prior_rate(context) == (0.0, 0)


def test_resolve_reserve_prior_rate_handles_non_numeric_payload():
    """Non-numeric reserve fields return (0.0, 0) — base-rate-unchanged contract."""
    assert resolve_reserve_prior_rate(
        {
            "sample_count": "many",
            "estimated_price_sample_count": 10,
            "median_bid_to_estimated_price_rate": 0.9,
            "median_estimated_price_rate": 0.93,
        }
    ) == (0.0, 0)
    assert resolve_reserve_prior_rate(
        {
            "sample_count": 10,
            "estimated_price_sample_count": 10,
            "median_bid_to_estimated_price_rate": "n/a",
            "median_estimated_price_rate": 0.93,
        }
    ) == (0.0, 0)
    assert resolve_reserve_prior_rate(
        {
            "sample_count": 10,
            "estimated_price_sample_count": 10,
            "median_bid_to_estimated_price_rate": 0.9,
            "median_estimated_price_rate": None,
        }
    ) == (0.0, 0)


# ---------------------------------------------------------------------------
# (c) leakage: the predictor only consumes the records it is handed; the
#     reserve context is derived from those same prior records, and the target
#     opening is never part of the input.
# ---------------------------------------------------------------------------


def test_reserve_context_built_only_from_supplied_prior_records():
    """summarize_historical_records derives reserve context purely from its inputs.

    There is no DB/IO/future read path: feeding only N prior records yields a
    reserve context whose sample_count reflects exactly those records, so a
    backtest harness that withholds the target opening cannot leak it here.
    """
    reserve_prices = [99_000_000.0 + (index * 100_000.0) for index in range(15)]
    prior_records = [
        {
            "bid_rate": 0.90 + (i * 0.001),
            "base_amount": 100_000_000.0,
            "reserve_prices": reserve_prices,
            "selected_numbers": [1, 4, 7, 12],
        }
        for i in range(5)
    ]
    summary = summarize_historical_records(tuple(prior_records))
    reserve_context = summary["reserve_price_context"]
    assert reserve_context is not None
    # exactly the 5 prior records contributed reserve spans — nothing extra.
    assert reserve_context["sample_count"] == 5
    assert reserve_context["estimated_price_sample_count"] == 5

    # The same context, when blended, only references its own numbers.
    blended = blend_reserve_prior(0.95, reserve_context=reserve_context)
    assert 0.85 <= blended <= 0.95


def test_target_self_opening_not_implicitly_added():
    """Passing a strict prefix produces a reserve sample_count equal to that prefix.

    Mirrors the backtest rolling-window discipline: the holdout (target) opening
    is appended only AFTER prediction, so it can never inflate the reserve
    context that informs the prior for that same target.
    """
    reserve_prices = [99_000_000.0 + (index * 100_000.0) for index in range(15)]

    def _record(rate: float):
        return {
            "bid_rate": rate,
            "base_amount": 100_000_000.0,
            "reserve_prices": reserve_prices,
            "selected_numbers": [1, 4, 7, 12],
        }

    full = [_record(0.90), _record(0.91), _record(0.92), _record(0.93)]
    prefix = full[:3]  # what the predictor sees when predicting the 4th opening

    prefix_summary = summarize_historical_records(tuple(prefix))
    full_summary = summarize_historical_records(tuple(full))

    # The prefix-only reserve context strictly excludes the withheld target.
    assert prefix_summary["reserve_price_context"]["sample_count"] == 3
    assert full_summary["reserve_price_context"]["sample_count"] == 4


@pytest.mark.parametrize(
    "unusable_bid_rate",
    [
        # 하단 이탈. **운영 실측에서 도달 가능한 69행이 전부 이 형태다**(0 < rate < 0.5).
        # 상단 이탈은 수집 경로의 to_bid_rate_fraction 이 /100 으로 접어 사실상 남지
        # 않으므로, 이 케이스가 회귀를 지키는 본체다.
        0.45,
        # 상단 이탈(percent-스케일). 도달 사례는 없지만 밴드 양끝을 함께 고정한다 —
        # 한쪽만 고정하면 반대쪽을 다시 좁히는 회귀를 놓친다.
        87.5,
    ],
)
def test_summarize_survives_out_of_band_bid_rate_with_reserve_pattern(unusable_bid_rate):
    """회귀: 밴드 밖 bid_rate(None 해석) + 예비가·추첨번호 행에서 죽지 않아야 한다.

    ``resolve_record_bid_rate`` 는 0.5~1.5 밴드 밖을 ``None`` 으로 돌려준다. 예비가격
    패턴 블록이 그 ``None`` 을 float 밴드와 비교해 ``TypeError`` 로 죽던 실데이터
    크래시의 재현이다. 운영 실측(예비가+추첨번호+base>0 보유 행)에서 실제로 도달하는
    것은 **하단 이탈 69행**이고 상단 이탈은 0건이므로, 밴드 양끝을 모두 고정한다.
    """
    reserve_prices = [99_000_000.0 + (index * 200_000.0) for index in range(15)]
    out_of_band = {
        "bid_rate": unusable_bid_rate,  # 밴드 밖 → 사용 불가(None) 판정 대상
        "base_amount": 100_000_000.0,
        "reserve_prices": reserve_prices,
        "selected_numbers": [1, 2, 3, 4],
    }
    valid = {
        "bid_rate": 0.90,
        "base_amount": 100_000_000.0,
        "reserve_prices": reserve_prices,
        "selected_numbers": [1, 2, 3, 4],
    }

    summary = summarize_historical_records((valid, out_of_band))

    # 사용 불가 bid_rate 행은 표본·bid/예가 비에서 빠지고, 예비가 패턴만 남는다.
    assert summary["sample_size"] == 1
    reserve_context = summary["reserve_price_context"]
    assert reserve_context["sample_count"] == 2
    assert reserve_context["estimated_price_sample_count"] == 2
    # bid/예가 비는 유효 행 한 건에서만 나온다: 0.90 / (선두 4개 평균 / base).
    realized = (sum(reserve_prices[:4]) / 4) / 100_000_000.0
    assert reserve_context["median_bid_to_estimated_price_rate"] == pytest.approx(
        0.90 / realized, abs=1e-4
    )


def test_estimated_price_rate_band_endpoints_value_table():
    """사정률 개연 밴드(0.8~1.2, 경계 포함) 양끝 값표 — #361 이 bid_rate 밴드에 남긴
    것과 같은 형태의 고정.

    이 밴드는 app/core/constants.ASSESSMENT_RATE_PLAUSIBLE_* 선언이고, distribution
    엔진만이 아니라 historical 서빙 가격(reserve prior 경유)까지 닿는다(리뷰 M3).
    극단 변이(0.5/2.0)에도 울리는 테스트가 없던 구멍을 이 값표가 막는다 — 변이 시
    0.79·1.21 행이 관측에 편입돼 아래 카운트가 4 가 되며 실패한다.
    """
    base_amount = 100_000_000.0

    def _flat_reserve_record(rate: float) -> dict:
        return {
            "bid_rate": 0.9,
            "base_amount": base_amount,
            "reserve_prices": [base_amount * rate] * 15,
            "selected_numbers": [1, 2, 3, 4],
        }

    summary = summarize_historical_records(
        (
            _flat_reserve_record(0.79),  # 하한 밖 — 제외
            _flat_reserve_record(0.80),  # 하한 경계 — 포함
            _flat_reserve_record(1.20),  # 상한 경계 — 포함
            _flat_reserve_record(1.21),  # 상한 밖 — 제외
        )
    )

    reserve_context = summary["reserve_price_context"]
    assert reserve_context["estimated_price_sample_count"] == 2
    # 경계 두 값(0.80·1.20)만 남으므로 중앙값 산술도 함께 닫힌다.
    assert reserve_context["median_estimated_price_rate"] == pytest.approx(1.0)
    # span 축은 밴드와 무관하게 4행 전부에서 계산된다(대조군).
    assert reserve_context["sample_count"] == 4
