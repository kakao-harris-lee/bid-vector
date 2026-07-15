from app.core.single_user import ensure_operator_account
from app.models.models import HistoricalData, Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.opportunity_analysis import OpportunityAnalysisService


def _vat_notice(db, *, agency="한국수산자원공단동해본부", est=80_038_182, base=88_042_000):
    p = Project(title="동해바다숲", category="service", issuing_agency=agency, budget_estimate=est)
    db.add(p)
    db.flush()
    db.add(HistoricalData(project_id=p.id, base_amount=base, predicted_price=est, bid_rate=0.0))
    db.commit()
    return p


def test_analyze_project_attaches_bid_target_menu_on_business_amount_base(test_db, monkeypatch):
    # Ensure the agency band applies (한국수산자원공단 floor/ceiling from settings).
    p = _vat_notice(test_db)
    op = ensure_operator_account(test_db)
    req = OpportunityAnalysisRequest(project_id=p.id, agency_name=p.issuing_agency)
    res = OpportunityAnalysisService().analyze_project(test_db, p, req, operator=op)
    prediction = res.get("price_prediction") or {}
    menu = prediction.get("bid_target_menu")
    assert menu is not None
    # Agency-source flags surfaced onto the prediction dict drive the menu gate.
    assert prediction.get("floor_from_agency") or prediction.get("ceiling_from_agency")
    opts = {o["label"]: o for o in menu["options"]}
    # aggressive == floor price on the 사업금액 base (88,042,000), not est.
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * menu["band_floor_rate"], 2)
    # recommended_amount aligned to the menu recommended (★결정1).
    assert res["recommended_amount"] == opts["recommended"]["bid_price"]


def test_analyze_project_skips_menu_when_band_is_category_only(test_db, monkeypatch):
    # 서울특별시 is NOT in the agency band config, so a service-category notice gets
    # only the wide category band — no agency-tightened band, so no menu and no
    # floor-anchored recommended_amount override (fallback path governs).
    p = _vat_notice(test_db, agency="서울특별시")
    op = ensure_operator_account(test_db)
    req = OpportunityAnalysisRequest(project_id=p.id, agency_name=p.issuing_agency)
    res = OpportunityAnalysisService().analyze_project(test_db, p, req, operator=op)
    prediction = res.get("price_prediction") or {}
    # Category-only band: neither edge came from an agency band.
    assert not prediction.get("floor_from_agency")
    assert not prediction.get("ceiling_from_agency")
    # No menu attached, so no floor-anchored override.
    assert prediction.get("bid_target_menu") is None
    # recommended_amount is NOT the aggressive/floor-anchored menu value.
    floor_rate = prediction.get("floor_bid_rate")
    if floor_rate is not None:
        floor_anchored = round(88_042_000 * floor_rate, 2)
        assert res["recommended_amount"] != floor_anchored
