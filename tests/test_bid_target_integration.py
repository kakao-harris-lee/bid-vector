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
    menu = (res.get("price_prediction") or {}).get("bid_target_menu")
    assert menu is not None
    opts = {o["label"]: o for o in menu["options"]}
    # aggressive == floor price on the 사업금액 base (88,042,000), not est.
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * menu["band_floor_rate"], 2)
    # recommended_amount aligned to the menu recommended (★결정1).
    assert res["recommended_amount"] == opts["recommended"]["bid_price"]
