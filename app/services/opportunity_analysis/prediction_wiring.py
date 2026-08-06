"""Price-prediction input assembly and menu attachment for opportunity analysis.

Owns the #260 predict wiring: it builds the calibration context, assembles the
prediction inputs (기초금액 base / title-inclusive text / published 낙찰하한 /
공사 tier) through the SINGLE ``prepare_prediction_inputs`` combiner, calls the
predictor port, and attaches the per-notice 투찰가 메뉴. Methods are moved
verbatim from the original ``OpportunityAnalysisService`` body — the predictor
call kwargs, the published-floor path, and the menu gating are byte-identical.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.bid_target import build_bid_target_menu
from app.ai.business_group import resolve_business_group
from app.ai.construction_scenario import is_construction_era_floor_resolved
from app.core.config import settings
from app.models.models import Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.bid_base import prepare_prediction_inputs
from app.services.bid_target_signals import resolve_bid_target_signals
from app.services.opportunity_analysis.base import _OpportunityAnalysisBase


class _PredictionWiringMixin(_OpportunityAnalysisBase):
    """Predict-port input assembly, calibration wiring, and 투찰가 메뉴 attachment."""

    def _build_price_prediction(
        self,
        db: Session,
        *,
        project: Project,
        request: OpportunityAnalysisRequest,
        operator_id: int,
    ) -> tuple[dict, str | None]:
        feedback_calibration = self.feedback_service.build_calibration_context(
            db,
            operator_id=operator_id,
            category=project.category,
            agency_name=request.agency_name,
        )
        business_type_code = getattr(project, "business_type_code", None)
        business_group = resolve_business_group(business_type_code)
        # 예측 전처리(기초금액 base / title 포함 text / published 낙찰하한 / 공사 tier
        # 입력)는 모든 검증 경로가 이 라이브 경로와 동일해야 하므로 단일 조합 헬퍼로
        # 묶어 해석한다. 개별 필드를 흩어 호출하면 검증 경로가 published floor 등을
        # 조용히 빠뜨릴 수 있어(감사 실측) 부분 채택이 불가능하도록 한 묶음으로 받는다.
        inputs = prepare_prediction_inputs(
            db, project, request_legal_floor_bid_rate=request.legal_floor_bid_rate
        )
        bid_base = inputs.bid_base
        estimation_amount = inputs.estimation_amount
        reference_date = inputs.reference_date
        prediction = self.price_prediction_port.predict_price(
            budget=bid_base,
            category=project.category or "other",
            # Predictor input = title+description+requirements via the shared
            # assembler so the live path feeds the SAME text the backtest/smoke/
            # holdout paths validate. The title carries regulatory mechanism cues
            # (2단계/가격입찰/협상/수의) the price-band and regime detection need.
            description=inputs.text,
            historical_records=self._load_price_history(db, project),
            agency_name=request.agency_name,
            feedback_calibration=feedback_calibration,
            business_type_code=business_type_code,
            business_group=business_group,
            # 공고 자신의 published 낙찰하한율(award_floor_rate, #201). guardrail_core
            # 가 max() 로만 폴드하므로 floor 를 올리기만 한다(red line).
            legal_floor_bid_rate=inputs.legal_floor_bid_rate,
            estimation_amount=estimation_amount,
            reference_date=reference_date,
        )
        # 3종 투찰가 메뉴(추천/공격/안전)를 additive 레이어로 첨부한다. 우선순위를
        # 선언적으로 고정한다: (1) 발주처(agency) 밴드가 있으면 공고별 신호로 밴드 내
        # 위치 조정(더 특이적), (2) 밴드가 없고 공사 era-correct 법정 하한(#197)이
        # 해석되면 floor+백분위수 앵커. 넓은 업종(category) 밴드만 있는 경우는 발주처별
        # 정밀도가 없어 메뉴/recommended_amount 오버라이드를 유발하면 안 되므로 제외한다.
        agency_band = bool(
            prediction.get("floor_from_agency") or prediction.get("ceiling_from_agency")
        )
        construction_anchor = not agency_band and is_construction_era_floor_resolved(
            project.category, estimation_amount, reference_date
        )
        if agency_band or construction_anchor:
            menu = build_bid_target_menu(
                floor_bid_rate=prediction.get("floor_bid_rate"),
                ceiling_bid_rate=prediction.get("ceiling_bid_rate"),
                budget=bid_base,
                signals=(
                    resolve_bid_target_signals(
                        db, agency_name=request.agency_name, category=project.category
                    )
                    if agency_band
                    else None
                ),
                floor_anchor_offsets=(
                    None
                    if agency_band
                    else settings.PREDICTION_CONSTRUCTION_SCENARIO_FLOOR_OFFSETS
                ),
            )
            if menu is not None:
                prediction["bid_target_menu"] = menu
        # 이 예측이 투찰율을 곱한 기준금액과 그 출처를 함께 싣는다. API 경로
        # (prediction_workflow)와 같은 계약이라 OpportunityAnalysisResponse.price_prediction
        # 에서도 두 필드가 채워진다 — 싣지 않으면 그 자리가 영구 null 이 되어 화면이
        # 예산(추정가격)과 기초금액을 다시 한 이름으로 뭉갠다(#350 축).
        # 표시 전용이 아니다. 소비자: ① 스코어링의 expected_margin 분모(#355,
        # _resolve_margin_bid_base) ② 추천가의 하한 탈출 게이트(#356,
        # _enforceable_floor_price 가 base/추정가격 비를 여기서 읽는다).
        # 빼면 ①은 추정가격 폴백으로, ②는 게이트가 닫혀 legacy 경계로 조용히 되돌아간다.
        prediction["bid_base"] = float(bid_base)
        prediction["bid_base_source"] = inputs.bid_base_source
        return (prediction, business_group)

    def _load_price_history(self, db: Session, project: Project, *, limit: int = 40) -> list[dict[str, object]]:
        """Load recent historical bid-rate samples for price prediction."""
        return self.dataset_service.load_historical_series(
            db,
            category=project.category,
            limit=limit,
            explicit_bid_rate_only=True,
        )
