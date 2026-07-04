"""AI Prediction routes"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.schemas import (
    BidRecommendationRequest,
    BidRecommendationResponse,
    DocumentAnalysisRequest,
    DocumentAnalysisResponse,
    PricePredictionRequest,
    PricePredictionResponse,
)
from app.services.prediction_workflow import PredictionWorkflowService

router = APIRouter()


def get_prediction_workflow() -> PredictionWorkflowService:
    return PredictionWorkflowService()


@router.post("/price", response_model=PricePredictionResponse)
def predict_project_price(
    request: PricePredictionRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.predict_project_price(db, request)


@router.post("/bid-recommendation", response_model=BidRecommendationResponse)
def get_bid_recommendation_endpoint(
    request: BidRecommendationRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.build_bid_recommendation(db, request)


@router.post("/analyze-document", response_model=DocumentAnalysisResponse)
def analyze_project_document(
    request: DocumentAnalysisRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.analyze_project_document(db, request)
