from app.ai.bid_target import BidTargetSignals, build_bid_target_menu, CAVEAT


def _labels(menu):
    return [o["label"] for o in menu["options"]]


def test_menu_has_three_options_floor_and_ceiling_fixed():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert _labels(menu) == ["recommended", "aggressive", "safe"]
    opts = {o["label"]: o for o in menu["options"]}
    assert opts["aggressive"]["bid_rate"] == 0.8806          # floor
    assert opts["safe"]["bid_rate"] == 0.882                 # ceiling
    assert 0.8806 <= opts["recommended"]["bid_rate"] <= 0.882


def test_prices_use_business_amount_base():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    opts = {o["label"]: o for o in menu["options"]}
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * 0.8806, 2)
    assert opts["safe"]["bid_price"] == round(88_042_000 * 0.882, 2)


def test_insufficient_signal_anchors_recommended_near_floor():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    rec = {o["label"]: o for o in menu["options"]}["recommended"]["bid_rate"]
    # base adjustment 0.15 → near floor, well below mid (0.8813)
    assert 0.8806 < rec < 0.8813


def test_high_dispersion_moves_recommended_toward_safe():
    low = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=0.001, data_sufficient=True),
    )
    high = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=0.05, data_sufficient=True),
    )
    def rec(m):
        return {o["label"]: o for o in m["options"]}["recommended"]["bid_rate"]

    assert rec(high) > rec(low)


def test_no_band_returns_none():
    assert build_bid_target_menu(
        floor_bid_rate=None, ceiling_bid_rate=None, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    ) is None


def test_collapsed_band_marks_collapsed_and_equal_rates():
    menu = build_bid_target_menu(
        floor_bid_rate=0.90, ceiling_bid_rate=0.90, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert menu["collapsed"] is True
    assert {o["bid_rate"] for o in menu["options"]} == {0.90}


def test_zero_budget_yields_none_prices_but_rates_present():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=0,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert all(o["bid_price"] is None for o in menu["options"])
    assert all(o["bid_rate"] is not None for o in menu["options"])


def test_honesty_no_probability_fields_and_caveat_present():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    assert menu["caveat"] == CAVEAT
    blob = str(menu).lower()
    assert "확률" not in blob and "probability" not in blob and "승률" not in blob
    for o in menu["options"]:
        assert o["basis"] and o["stance"] and o["risk_note"]
