"""AI Prediction routes"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.single_user import ensure_operator_account
from app.models.models import Project, PricePrediction
from app.schemas.schemas import (
    PricePredictionRequest,
    PricePredictionResponse,
    BidRecommendationRequest,
    BidRecommendationResponse,
    DocumentAnalysisRequest,
    DocumentAnalysisResponse,
)
from app.ai.price_prediction import predict_price
from app.ai.bid_recommendation import get_bid_recommendation
from app.ai.document_analyzer import analyze_document
from app.services.prediction_dataset import PredictionDatasetService
from app.services.prediction_feedback import PredictionFeedbackService

router = APIRouter()


def _load_price_history(db: Session, *, category: str | None, limit: int = 80) -> list[dict[str, object]]:
    """Load recent historical price samples for the given project category."""
    return PredictionDatasetService().load_historical_series(
        db,
        category=category,
        limit=limit,
        explicit_bid_rate_only=True,
    )


@router.post("/price", response_model=PricePredictionResponse)
def predict_project_price(
    request: PricePredictionRequest,
    db: Session = Depends(get_db)
):
    """Predict project price for the singleton operator."""
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    operator = ensure_operator_account(db)
    feedback_calibration = PredictionFeedbackService().build_calibration_context(
        db,
        operator_id=operator.id,
        category=request.category or project.category,
        agency_name=request.agency_name,
    )

    prediction = predict_price(
        budget=request.budget_estimate,
        category=request.category,
        description=request.description,
        historical_records=_load_price_history(db, category=request.category or project.category),
        agency_name=request.agency_name,
        feedback_calibration=feedback_calibration,
        legal_floor_bid_rate=request.legal_floor_bid_rate,
    )

    db_prediction = PricePrediction(
        user_id=operator.id,
        project_id=request.project_id,
        predicted_price=prediction["predicted_price"],
        price_range_min=prediction["price_range_min"],
        price_range_max=prediction["price_range_max"],
        confidence_score=prediction["confidence_score"],
        model_version=prediction["model_version"],
        predictor_name=prediction.get("predictor_name", "historical_statistical"),
        predictor_family=prediction.get("predictor_family", "statistical"),
        fallback_reason=prediction.get("fallback_reason"),
        selector_name=prediction.get("selector_name", "configured_preference"),
        selection_reason=prediction.get("selection_reason"),
        backtest_sample_count=int(prediction.get("backtest_sample_count", 0) or 0),
        backtest_average_absolute_error_rate=prediction.get("backtest_average_absolute_error_rate"),
        training_window_size=int(prediction.get("training_window_size", 0) or 0),
        pricing_mode=prediction.get("pricing_mode", "heuristic"),
        historical_sample_size=int(prediction.get("historical_sample_size", 0) or 0),
        agency_match_sample_size=int(prediction.get("agency_match_sample_size", 0) or 0),
        predicted_bid_rate=float(prediction.get("predicted_bid_rate", 0.0) or 0.0),
        guardrail_applied=bool(prediction.get("guardrail_applied", False)),
        guardrail_reason=prediction.get("guardrail_reason"),
        floor_bid_rate=prediction.get("floor_bid_rate"),
        floor_price=prediction.get("floor_price"),
    )
    db.add(db_prediction)
    db.commit()

    return prediction


@router.post("/bid-recommendation", response_model=BidRecommendationResponse)
def get_bid_recommendation_endpoint(
    request: BidRecommendationRequest,
    db: Session = Depends(get_db)
):
    """Get AI-powered bid recommendation for the singleton operator."""
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    recommendation = get_bid_recommendation(
        project_data={
            "budget": project.budget_estimate,
            "category": project.category,
            "description": project.description,
        },
        user_data=request.user_historical_data or {},
    )

    return recommendation


@router.post("/analyze-document", response_model=DocumentAnalysisResponse)
def analyze_project_document(
    request: DocumentAnalysisRequest,
    db: Session = Depends(get_db)
):
    """Analyze a project document within the singleton operator workflow."""
    project = db.query(Project).filter(Project.id == request.project_id).first()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )

    analysis = analyze_document(
        content=request.document_content,
        document_type=request.document_type,
    )

    return analysis
