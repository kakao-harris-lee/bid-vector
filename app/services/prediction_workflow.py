"""Prediction workflow orchestration outside FastAPI route handlers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.ai.factory import (
    build_bid_recommendation_port,
    build_document_analysis_port,
    build_price_prediction_port,
)
from app.ai.service_interfaces import (
    BidRecommendationPort,
    DocumentAnalysisPort,
    PricePredictionPort,
)
from app.ai.bid_target import build_bid_target_menu
from app.core.single_user import ensure_operator_account
from app.models.models import PricePrediction, Project
from app.schemas.schemas import (
    BidRecommendationRequest,
    DocumentAnalysisRequest,
    PricePredictionRequest,
)
from app.services.bid_base import resolve_notice_bid_base
from app.services.bid_target_signals import resolve_bid_target_signals
from app.services.prediction_dataset import PredictionDatasetService
from app.services.prediction_feedback import PredictionFeedbackService


class PredictionWorkflowService:
    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
        bid_recommendation_port: BidRecommendationPort | None = None,
        document_analysis_port: DocumentAnalysisPort | None = None,
        dataset_service: PredictionDatasetService | None = None,
        feedback_service: PredictionFeedbackService | None = None,
    ) -> None:
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
        self.bid_recommendation_port = bid_recommendation_port or build_bid_recommendation_port()
        self.document_analysis_port = document_analysis_port or build_document_analysis_port()
        self.dataset_service = dataset_service or PredictionDatasetService()
        self.feedback_service = feedback_service or PredictionFeedbackService()

    def predict_project_price(self, db: Session, request: PricePredictionRequest) -> dict[str, Any]:
        """Predict project price for the singleton operator."""
        project = self._load_project(db, request.project_id)

        operator = ensure_operator_account(db)
        feedback_calibration = self.feedback_service.build_calibration_context(
            db,
            operator_id=operator.id,
            category=request.category or project.category,
            agency_name=request.agency_name,
        )

        # 투찰가는 추정가격(ex-VAT)이 아니라 기초금액/사업금액(배정예산) 기준으로
        # 산정한다. 프로젝트의 최신 HistoricalData.base_amount 를 해석하되, 이를
        # 얻지 못하면(0/falsy) 요청에 실린 명시적 budget_estimate 로 폴백한다.
        resolved_bid_base = resolve_notice_bid_base(db, project)
        prediction = self.price_prediction_port.predict_price(
            budget=resolved_bid_base or request.budget_estimate,
            category=request.category,
            description=request.description,
            historical_records=self._load_price_history(db, category=request.category or project.category),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
            legal_floor_bid_rate=request.legal_floor_bid_rate,
        )

        # 발주처 밴드(floor/ceiling) 위에 공고별 신호로 위치를 조정한 3종 투찰가
        # 메뉴를 additive 레이어로 첨부한다. 밴드가 발주처(agency) 밴드에서 왔을
        # 때만 첨부한다. 넓은 업종(category) 밴드는 발주처별 정밀도가 없어 메뉴를
        # 유발하면 안 되므로 제외한다.
        if prediction.get("floor_from_agency") or prediction.get("ceiling_from_agency"):
            menu = build_bid_target_menu(
                floor_bid_rate=prediction.get("floor_bid_rate"),
                ceiling_bid_rate=prediction.get("ceiling_bid_rate"),
                budget=resolved_bid_base or request.budget_estimate,
                signals=resolve_bid_target_signals(
                    db,
                    agency_name=request.agency_name,
                    category=request.category or project.category,
                ),
            )
            if menu is not None:
                prediction["bid_target_menu"] = menu

        db_prediction = self._build_price_prediction_row(
            operator_id=operator.id,
            project_id=request.project_id,
            prediction=prediction,
        )
        db.add(db_prediction)
        db.commit()

        return prediction

    def build_bid_recommendation(self, db: Session, request: BidRecommendationRequest) -> dict[str, Any]:
        """Get AI-powered bid recommendation for the singleton operator."""
        project = self._load_project(db, request.project_id)

        return self.bid_recommendation_port.recommend(
            project_data={
                "budget": project.budget_estimate,
                "category": project.category,
                "description": project.description,
            },
            user_data=request.user_historical_data or {},
        )

    def analyze_project_document(self, db: Session, request: DocumentAnalysisRequest) -> dict[str, Any]:
        """Analyze a project document within the singleton operator workflow."""
        self._load_project(db, request.project_id)

        return self.document_analysis_port.analyze(
            request.document_content,
            document_type=request.document_type,
        )

    def _load_project(self, db: Session, project_id: int) -> Project:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        return project

    def _load_price_history(
        self,
        db: Session,
        *,
        category: str | None,
        limit: int = 80,
    ) -> list[dict[str, object]]:
        """Load recent historical price samples for the given project category."""
        return self.dataset_service.load_historical_series(
            db,
            category=category,
            limit=limit,
            explicit_bid_rate_only=True,
        )

    def _build_price_prediction_row(
        self,
        *,
        operator_id: int,
        project_id: int,
        prediction: dict[str, Any],
    ) -> PricePrediction:
        return PricePrediction(
            user_id=operator_id,
            project_id=project_id,
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
