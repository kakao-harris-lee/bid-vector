from app.ai.bid_target import BidTargetSignals, build_bid_target_menu
from app.schemas.schemas import BidTargetMenu, PricePredictionResponse


def test_menu_dict_validates_as_schema():
    menu = build_bid_target_menu(
        floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
        signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
    )
    model = BidTargetMenu.model_validate(menu)
    assert len(model.options) == 3
    assert model.caveat


def test_price_prediction_response_accepts_menu():
    resp = PricePredictionResponse(
        predicted_price=1.0, price_range_min=1.0, price_range_max=1.0,
        confidence_score=0.5, model_version="v1",
        bid_target_menu=build_bid_target_menu(
            floor_bid_rate=0.8806, ceiling_bid_rate=0.882, budget=88_042_000,
            signals=BidTargetSignals(win_rate_dispersion=None, data_sufficient=False),
        ),
    )
    assert resp.bid_target_menu is not None
    assert resp.bid_target_menu.band_floor_rate == 0.8806
