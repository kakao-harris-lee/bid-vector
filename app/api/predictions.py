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

router = APIRouter()


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

    prediction = predict_price(
        budget=request.budget_estimate,
        category=request.category,
        description=request.description,
    )

    operator = ensure_operator_account(db)
    db_prediction = PricePrediction(
        user_id=operator.id,
        project_id=request.project_id,
        predicted_price=prediction["predicted_price"],
        price_range_min=prediction["price_range_min"],
        price_range_max=prediction["price_range_max"],
        confidence_score=prediction["confidence_score"],
        model_version="v1.0",
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
