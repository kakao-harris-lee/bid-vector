from app.models.models import HistoricalData, Project
from app.schemas.schemas import PricePredictionRequest
from app.services.prediction_workflow import PredictionWorkflowService


def test_workflow_prediction_includes_menu_on_agency_band(test_db):
    p = Project(
        title="동해바다숲",
        category="service",
        issuing_agency="한국수산자원공단동해본부",
        budget_estimate=80_038_182,
    )
    test_db.add(p)
    test_db.flush()
    test_db.add(
        HistoricalData(
            project_id=p.id,
            base_amount=88_042_000,
            predicted_price=80_038_182,
            bid_rate=0.0,
        )
    )
    test_db.commit()
    req = PricePredictionRequest(
        project_id=p.id,
        category="service",
        description="바다숲",
        agency_name="한국수산자원공단동해본부",
        budget_estimate=80_038_182,
    )
    out = PredictionWorkflowService().predict_project_price(test_db, req)
    menu = out.get("bid_target_menu")
    assert menu is not None
    opts = {o["label"]: o for o in menu["options"]}
    # 사업금액(기초금액) base(88,042,000)로 가격 산정, 추정가격(est)이 아님.
    assert opts["aggressive"]["bid_price"] == round(88_042_000 * menu["band_floor_rate"], 2)


def test_workflow_prediction_no_menu_on_category_only_band(test_db):
    # 넓은 업종(category) 밴드만 있는 발주처는 메뉴를 첨부하지 않는다.
    p = Project(
        title="일반용역",
        category="service",
        issuing_agency="서울특별시",
        budget_estimate=80_038_182,
    )
    test_db.add(p)
    test_db.flush()
    test_db.add(
        HistoricalData(
            project_id=p.id,
            base_amount=88_042_000,
            predicted_price=80_038_182,
            bid_rate=0.0,
        )
    )
    test_db.commit()
    req = PricePredictionRequest(
        project_id=p.id,
        category="service",
        description="일반",
        agency_name="서울특별시",
        budget_estimate=80_038_182,
    )
    out = PredictionWorkflowService().predict_project_price(test_db, req)
    assert out.get("bid_target_menu") is None
