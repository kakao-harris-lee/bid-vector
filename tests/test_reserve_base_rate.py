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
    *, implied_rate: float, sample_count: int, estimated_count: int | None = None
):
    return {
        "sample_count": sample_count,
        "estimated_price_sample_count": estimated_count
        if estimated_count is not None
        else sample_count,
        "average_reserve_span_rate": 0.03,
        "average_estimated_price_rate": 1.0,
        "median_estimated_price_rate": 1.0,
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
    """Implausible 예가-대비 투찰률 values are discarded so they never skew the base."""
    assert resolve_reserve_prior_rate(None) == (0.0, 0)
    assert resolve_reserve_prior_rate({}) == (0.0, 0)
    # out-of-band implied rate
    assert resolve_reserve_prior_rate(
        _reserve_context(implied_rate=2.0, sample_count=10)
    ) == (0.0, 0)
    # usable
    implied, count = resolve_reserve_prior_rate(
        _reserve_context(implied_rate=0.9, sample_count=6)
    )
    assert implied == pytest.approx(0.9)
    assert count == 6


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
