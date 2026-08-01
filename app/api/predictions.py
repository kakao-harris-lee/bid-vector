"""AI Prediction routes"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

# provider 는 ``app/api/providers.py`` 단일 출처에 모여 있다. 여기서 import 하므로 기존
# 참조 경로(``app.api.predictions.get_prediction_workflow``)와 override 키는 그대로다.
from app.api.providers import get_prediction_workflow
from app.core.database import get_db
from app.schemas.schemas import (
    DocumentAnalysisRequest,
    DocumentAnalysisResponse,
    PricePredictionRequest,
    PricePredictionResponse,
)
from app.services.prediction_workflow import PredictionWorkflowService

router = APIRouter()


@router.post("/price", response_model=PricePredictionResponse)
def predict_project_price(
    request: PricePredictionRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.predict_project_price(db, request)


@router.post("/analyze-document", response_model=DocumentAnalysisResponse)
def analyze_project_document(
    request: DocumentAnalysisRequest,
    db: Session = Depends(get_db),
    workflow: PredictionWorkflowService = Depends(get_prediction_workflow),
):
    return workflow.analyze_project_document(db, request)
