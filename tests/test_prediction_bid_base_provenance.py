"""The price-prediction response must say WHICH amount the bid rate was applied to.

``PredictionWorkflowService.predict_project_price`` used to resolve its predictor
budget as ``resolved_bid_base or request.budget_estimate``. When the left side is
0/falsy (no 기초금액 on record AND no ``Project.budget_estimate``), the UNVALIDATED
client-supplied ``budget_estimate`` — a bare ``float`` with no declared basis —
silently became the bid base. On a 과세 공고 that produces a ~10% lower 투찰가 with
no signal anywhere in the response, which is the failure class behind this repo's
실투찰 실격 history (#162/#195/#220).

The fallback is kept (it is the only way to answer for a notice with no stored
budget) but it is no longer silent: the response carries ``bid_base`` and
``bid_base_source``, reusing the provenance vocabulary the display boundary
already speaks (#350, ``AmountWithBasis``).
"""

from __future__ import annotations

import pytest

from app.schemas.schemas import PricePredictionRequest
from app.services.bid_base import (
    BID_BASE_SOURCE_BUDGET_FALLBACK,
    BID_BASE_SOURCE_CLIENT_ESTIMATE,
)
from app.services.prediction_workflow import PredictionWorkflowService
from tests.test_bid_base import (
    _BUDGET_ESTIMATE,
    _VAT_BASE_AMOUNT,
    _add_base_row,
    _make_project,
    _seed_price_history,
)


def _predict(test_db, project, *, budget_estimate: float = _BUDGET_ESTIMATE) -> dict:
    return PredictionWorkflowService().predict_project_price(
        test_db,
        PricePredictionRequest(
            project_id=project.id,
            budget_estimate=budget_estimate,
            category=project.category,
            description="투찰 기준금액 provenance 검증",
        ),
    )


def _fed_base(prediction: dict) -> float:
    """Recover the base the rate was applied to: predicted_price ≈ rate × base."""
    return float(prediction["predicted_price"]) / float(prediction["predicted_bid_rate"])


def test_prediction_reports_collected_base_as_its_source(test_db):
    """기초금액 on record → response reports that amount and a non-client source."""
    _seed_price_history(test_db)
    project = _make_project(test_db)
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    prediction = _predict(test_db, project)

    assert prediction["bid_base"] == pytest.approx(_VAT_BASE_AMOUNT)
    assert prediction["bid_base_source"] not in {
        BID_BASE_SOURCE_CLIENT_ESTIMATE,
        BID_BASE_SOURCE_BUDGET_FALLBACK,
    }
    assert _fed_base(prediction) == pytest.approx(_VAT_BASE_AMOUNT, rel=1e-3)


def test_prediction_reports_project_budget_fallback(test_db):
    """No 기초금액 row → the project's own 추정가격 is used and labelled as the fallback."""
    _seed_price_history(test_db)
    project = _make_project(test_db)
    test_db.commit()

    prediction = _predict(test_db, project)

    assert prediction["bid_base"] == pytest.approx(_BUDGET_ESTIMATE)
    assert prediction["bid_base_source"] == BID_BASE_SOURCE_BUDGET_FALLBACK


def test_client_budget_fallback_is_labelled_not_silent(test_db):
    """Project carries no budget at all → the client's number is used AND disclosed.

    This is the exact branch the old ``or`` expression took without a word: the
    number came from the request body, its basis is unverified, and the operator
    previously had no way to tell that from a collected 기초금액.
    """
    _seed_price_history(test_db)
    project = _make_project(test_db, budget_estimate=0.0)
    test_db.commit()

    prediction = _predict(test_db, project, budget_estimate=_VAT_BASE_AMOUNT)

    assert prediction["bid_base"] == pytest.approx(_VAT_BASE_AMOUNT)
    assert prediction["bid_base_source"] == BID_BASE_SOURCE_CLIENT_ESTIMATE
    # The client value really is what the rate was applied to (no silent 0 base).
    assert _fed_base(prediction) == pytest.approx(_VAT_BASE_AMOUNT, rel=1e-3)


def test_client_budget_fallback_is_exposed_through_the_api(client, test_db):
    """The provenance survives the response model — this endpoint IS the display boundary."""
    _seed_price_history(test_db)
    project = _make_project(test_db, budget_estimate=0.0)
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project.id,
            "budget_estimate": _VAT_BASE_AMOUNT,
            "category": project.category,
            "description": "투찰 기준금액 provenance 검증",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["bid_base"] == pytest.approx(_VAT_BASE_AMOUNT)
    assert payload["bid_base_source"] == BID_BASE_SOURCE_CLIENT_ESTIMATE


def test_opportunity_analysis_price_prediction_carries_the_bid_base(test_db):
    """The live analysis path fills the same two fields, not a permanent null.

    ``OpportunityAnalysisResponse.price_prediction`` reuses ``PricePredictionResponse``,
    so if the live wiring omits them the operator-facing analysis silently reports
    ``bid_base: null`` while the API-only endpoint reports the amount — the two
    surfaces would disagree about what the 투찰율 was applied to.
    """
    from app.schemas.schemas import OpportunityAnalysisRequest
    from app.services.opportunity_analysis import OpportunityAnalysisService

    _seed_price_history(test_db)
    project = _make_project(test_db)
    _add_base_row(test_db, project, _VAT_BASE_AMOUNT)
    test_db.commit()

    analysis = OpportunityAnalysisService().analyze_project(
        test_db,
        project,
        OpportunityAnalysisRequest(project_id=project.id),
    )
    price_prediction = analysis["price_prediction"]

    assert price_prediction["bid_base"] == pytest.approx(_VAT_BASE_AMOUNT)
    assert price_prediction["bid_base_source"] not in {
        None,
        BID_BASE_SOURCE_CLIENT_ESTIMATE,
        BID_BASE_SOURCE_BUDGET_FALLBACK,
    }
