"""Phase 2 Experiment Lab: settlement breakdown aggregation + polling exposure.

Covers the pure ``compute_breakdown`` aggregation (category / budget-band win
rate math, band boundary classification) and that the breakdown surfaces in the
run-detail polling response after an eager (memory://) run.
"""

from __future__ import annotations

import pytest

from app.services.synthetic_backtest import SyntheticBacktestService
from app.services.synthetic_experiment import (
    BUDGET_BAND_TOP_KEY,
    _budget_band_key,
    compute_breakdown,
)


# --- band classification (boundary values) ------------------------------------


@pytest.mark.parametrize(
    "budget,expected",
    [
        (0.0, "lt_1eok"),
        (99_999_999.0, "lt_1eok"),
        (100_000_000.0, "1eok_5eok"),  # boundary -> next band
        (499_999_999.0, "1eok_5eok"),
        (500_000_000.0, "5eok_10eok"),  # boundary -> next band
        (999_999_999.0, "5eok_10eok"),
        (1_000_000_000.0, "10eok_50eok"),  # boundary -> next band
        (4_999_999_999.0, "10eok_50eok"),
        (5_000_000_000.0, BUDGET_BAND_TOP_KEY),  # final boundary -> top band
        (50_000_000_000.0, BUDGET_BAND_TOP_KEY),
    ],
)
def test_budget_band_classification_boundaries(budget, expected):
    assert _budget_band_key(budget) == expected


# --- compute_breakdown aggregation --------------------------------------------


def _settlement(category, budget, verdict, error):
    return {
        "category": category,
        "budget_estimate": budget,
        "would_have_won_price_only": verdict,
        "absolute_bid_rate_error": error,
    }


def _settlement_full(
    category,
    budget,
    *,
    price_verdict="unlikely",
    final_verdict="unknown",
    error=0.01,
    result_time=None,
):
    """A settlement carrying BOTH the price-only verdict and the PR3 eligibility
    gate verdict (+ optional freshness timestamp)."""
    return {
        "category": category,
        "budget_estimate": budget,
        "would_have_won_price_only": price_verdict,
        "would_have_won_final": final_verdict,
        "absolute_bid_rate_error": error,
        "result_time": result_time,
    }


def test_compute_breakdown_empty_returns_empty_structure():
    assert compute_breakdown([]) == {"by_category": [], "by_budget_band": []}
    assert compute_breakdown(None) == {"by_category": [], "by_budget_band": []}


def test_compute_breakdown_category_win_rate_and_error():
    items = [
        # 공사: 2 settled, 1 plausible win -> win_rate 0.5
        _settlement("공사", 50_000_000, "plausible", 0.002),
        _settlement("공사", 60_000_000, "unlikely", 0.02),
        # 용역: 1 settled, 0 wins -> win_rate 0.0
        _settlement("용역", 200_000_000, "competitive", 0.008),
    ]
    breakdown = compute_breakdown(items)

    by_cat = {row["category"]: row for row in breakdown["by_category"]}
    assert set(by_cat) == {"공사", "용역"}

    gongsa = by_cat["공사"]
    assert gongsa["settled_count"] == 2
    assert gongsa["would_have_won_count"] == 1
    assert gongsa["win_rate"] == 0.5
    # avg of 0.002 and 0.02 == 0.011
    assert gongsa["avg_abs_bid_rate_error"] == 0.011

    yongyeok = by_cat["용역"]
    assert yongyeok["settled_count"] == 1
    assert yongyeok["would_have_won_count"] == 0
    assert yongyeok["win_rate"] == 0.0
    assert yongyeok["avg_abs_bid_rate_error"] == 0.008


def test_compute_breakdown_emits_honest_est_price_close_rate_alias():
    """``win_rate`` and ``est_price_close_rate`` are the SAME price-only value;
    the honest alias must always accompany the legacy key."""
    items = [
        _settlement("공사", 50_000_000, "plausible", 0.002),
        _settlement("공사", 60_000_000, "unlikely", 0.02),
    ]
    gongsa = compute_breakdown(items)["by_category"][0]
    assert gongsa["win_rate"] == 0.5
    assert gongsa["est_price_close_rate"] == 0.5
    assert gongsa["win_rate"] == gongsa["est_price_close_rate"]


def test_compute_breakdown_eligible_favorable_rate_excludes_unknown():
    """``eligible_favorable_rate`` is favorable / (settled - unknown): the
    eligibility-gate ESTIMATE computed only over judgeable settlements."""
    items = [
        # 3 settled in 공사: 1 favorable, 1 outbid, 1 unknown (excluded from denom).
        _settlement_full("공사", 50_000_000, final_verdict="eligible_favorable"),
        _settlement_full("공사", 50_000_000, final_verdict="eligible_but_outbid"),
        _settlement_full("공사", 50_000_000, final_verdict="unknown"),
    ]
    gongsa = compute_breakdown(items)["by_category"][0]
    assert gongsa["settled_count"] == 3
    assert gongsa["eligible_favorable_count"] == 1
    assert gongsa["eligibility_unknown_count"] == 1
    # judged = settled - unknown = 2; rate = 1/2.
    assert gongsa["eligibility_judged_count"] == 2
    assert gongsa["eligible_favorable_rate"] == 0.5


def test_compute_breakdown_eligible_rate_none_when_all_unknown():
    """All-``unknown`` group -> judged denominator 0 -> rate is None (honest)."""
    items = [
        _settlement_full("물품", 30_000_000, final_verdict="unknown"),
        _settlement_full("물품", 40_000_000, final_verdict="unknown"),
    ]
    goods = compute_breakdown(items)["by_category"][0]
    assert goods["category"] == "물품"
    assert goods["settled_count"] == 2
    assert goods["eligibility_judged_count"] == 0
    assert goods["eligible_favorable_rate"] is None


def test_compute_breakdown_surfaces_latest_result_time_freshness():
    """Each group reports the newest award time (freshness health signal)."""
    items = [
        _settlement_full("공사", 50_000_000, result_time="2026-01-10T00:00:00+00:00"),
        _settlement_full("공사", 60_000_000, result_time="2026-03-15T00:00:00+00:00"),
        _settlement_full("공사", 70_000_000, result_time=None),  # ignored
    ]
    gongsa = compute_breakdown(items)["by_category"][0]
    assert gongsa["latest_result_time"] == "2026-03-15T00:00:00+00:00"
    # settled_count doubles as the sample-size health signal.
    assert gongsa["settled_count"] == 3


def test_compute_breakdown_latest_result_time_none_when_absent():
    items = [_settlement("용역", 200_000_000, "plausible", 0.001)]
    yongyeok = compute_breakdown(items)["by_category"][0]
    assert yongyeok["latest_result_time"] is None


def test_compute_breakdown_budget_bands_grouping_and_order():
    items = [
        _settlement("a", 50_000_000, "plausible", 0.001),  # lt_1eok
        _settlement("b", 300_000_000, "plausible", 0.001),  # 1eok_5eok
        _settlement("c", 300_000_000, "unlikely", 0.05),  # 1eok_5eok
        _settlement("d", 6_000_000_000, "plausible", 0.0),  # gte_50eok
    ]
    breakdown = compute_breakdown(items)

    bands = breakdown["by_budget_band"]
    # Canonical ascending order, only populated bands present.
    assert [row["budget_band"] for row in bands] == [
        "lt_1eok",
        "1eok_5eok",
        BUDGET_BAND_TOP_KEY,
    ]

    by_band = {row["budget_band"]: row for row in bands}
    assert by_band["lt_1eok"]["settled_count"] == 1
    assert by_band["lt_1eok"]["win_rate"] == 1.0

    mid = by_band["1eok_5eok"]
    assert mid["settled_count"] == 2
    assert mid["would_have_won_count"] == 1
    assert mid["win_rate"] == 0.5

    top = by_band[BUDGET_BAND_TOP_KEY]
    assert top["settled_count"] == 1
    assert top["win_rate"] == 1.0


def test_compute_breakdown_unknown_category_bucket():
    items = [
        _settlement(None, 10_000_000, "plausible", 0.001),
        _settlement("", 10_000_000, "unlikely", 0.02),
    ]
    breakdown = compute_breakdown(items)
    by_cat = {row["category"]: row for row in breakdown["by_category"]}
    assert "unknown" in by_cat
    assert by_cat["unknown"]["settled_count"] == 2
    assert by_cat["unknown"]["would_have_won_count"] == 1


def test_compute_breakdown_accepts_sliced_item_shape():
    """The sliced dashboard item uses ``would_have_won`` bool instead of the
    rich verdict string; the breakdown must still count wins correctly."""
    items = [
        {"category": "공사", "budget_estimate": 50_000_000, "would_have_won": True,
         "absolute_bid_rate_error": 0.001},
        {"category": "공사", "budget_estimate": 50_000_000, "would_have_won": False,
         "absolute_bid_rate_error": 0.02},
    ]
    breakdown = compute_breakdown(items)
    gongsa = breakdown["by_category"][0]
    assert gongsa["category"] == "공사"
    assert gongsa["would_have_won_count"] == 1
    assert gongsa["win_rate"] == 0.5


# --- polling exposure (eager run -> breakdown in response) --------------------


def _fake_result_with_breakdown(**kwargs):
    return {
        "operator_count": 1,
        "category": kwargs.get("category"),
        "start_at": None,
        "end_at": None,
        "limit": kwargs.get("limit", 100),
        "scenario": kwargs.get("scenario", "base"),
        "results": [
            {
                "slug": "aggressive",
                "settled_count": 2,
                "would_have_won_count": 1,
                "win_rate_on_settled": 0.5,
                "settlement_items": [
                    {"project_id": 1, "category": "공사", "would_have_won": True},
                ],
                "breakdown": {
                    "by_category": [
                        {
                            "category": "공사",
                            "settled_count": 2,
                            "would_have_won_count": 1,
                            "win_rate": 0.5,
                            "avg_abs_bid_rate_error": 0.004,
                        }
                    ],
                    "by_budget_band": [
                        {
                            "budget_band": "1eok_5eok",
                            "settled_count": 2,
                            "would_have_won_count": 1,
                            "win_rate": 0.5,
                            "avg_abs_bid_rate_error": 0.004,
                        }
                    ],
                },
            },
        ],
    }


@pytest.fixture
def patched_engine_breakdown(monkeypatch):
    def fake_run_for_all(self, db, **kwargs):
        return _fake_result_with_breakdown(**kwargs)

    monkeypatch.setattr(SyntheticBacktestService, "run_for_all", fake_run_for_all)


def _create_experiment(client):
    return client.post(
        "/api/v1/synthetic/experiments",
        json={
            "name": "breakdown sweep",
            "params": {"limit": 10, "scenario": "base"},
            "operator_slugs": ["aggressive"],
        },
    )


def test_poll_run_exposes_breakdown(client, patched_engine_breakdown):
    created = _create_experiment(client).json()
    run = client.post(
        f"/api/v1/synthetic/experiments/{created['id']}/runs"
    ).json()
    response = client.get(
        f"/api/v1/synthetic/experiments/{created['id']}/runs/{run['id']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    result = body["results"][0]
    assert "breakdown" in result
    by_cat = result["breakdown"]["by_category"]
    assert by_cat and by_cat[0]["category"] == "공사"
    assert by_cat[0]["win_rate"] == 0.5
    by_band = result["breakdown"]["by_budget_band"]
    assert by_band and by_band[0]["budget_band"] == "1eok_5eok"


def test_poll_run_breakdown_fallback_when_engine_omits_it(client, monkeypatch):
    """Stubbed payloads without a precomputed ``breakdown`` still yield a valid
    (empty-or-computed) breakdown via the mark_completed fallback."""

    def fake_run_for_all(self, db, **kwargs):
        return {
            "operator_count": 1,
            "category": None,
            "start_at": None,
            "end_at": None,
            "limit": 10,
            "scenario": "base",
            "results": [
                {
                    "slug": "aggressive",
                    "settled_count": 1,
                    "would_have_won_count": 1,
                    "win_rate_on_settled": 1.0,
                    "settlement_items": [
                        {
                            "category": "용역",
                            "budget_estimate": 200_000_000,
                            "would_have_won": True,
                            "absolute_bid_rate_error": 0.001,
                        }
                    ],
                    # no "breakdown" key -> fallback path
                },
            ],
        }

    monkeypatch.setattr(
        SyntheticBacktestService, "run_for_all", fake_run_for_all
    )
    created = _create_experiment(client).json()
    run = client.post(
        f"/api/v1/synthetic/experiments/{created['id']}/runs"
    ).json()
    response = client.get(
        f"/api/v1/synthetic/experiments/{created['id']}/runs/{run['id']}"
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    by_cat = result["breakdown"]["by_category"]
    assert by_cat[0]["category"] == "용역"
    assert by_cat[0]["win_rate"] == 1.0
