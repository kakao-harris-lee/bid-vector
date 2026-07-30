"""``app.schemas.paper_bidding_runs`` 계약 테스트 (정상 + 거부 경로).

특히 **두 run mode 의 요청 스냅샷이 서로 배타적**이어야 한다: 한 모델이 다른 모드의
payload 를 조용히 받아들이면 응답의 ``request`` union 이 모호해지고, 없는 키가 ``null``
로 산출에 끼어들어 경계 계약이 바뀐다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.paper_bidding_items import PaperBiddingRunSummary
from app.schemas.paper_bidding_runs import (
    SETTLEMENT_BASIS,
    ForwardPaperBiddingRunRequestSnapshot,
    ForwardPaperRunParams,
    ForwardSettlementRunResult,
    HistoricalBacktestRunRequestSnapshot,
    PaperBiddingPaperBidRecord,
    PaperBiddingSettlementOverview,
    PaperBiddingSettlementRecord,
    PersistedForwardPaperBiddingRunRequestSnapshot,
    PersistedHistoricalBacktestRunRequestSnapshot,
)

HISTORICAL_PAYLOAD = {
    "category": "construction",
    "award_categories": ["construction"],
    "start_at": "2025-03-01T00:00:00+00:00",
    "end_at": "2025-03-31T00:00:00+00:00",
    "limit": 10,
    "scenario": "base",
    "strategy_version": "local-backtest",
    "model_version": "current",
    "cutoff_hours_before_deadline": 2,
    "history_limit": 80,
    "settle_actions": ["bid_now", "review"],
    "persist": True,
}
FORWARD_PAYLOAD = {
    "category": None,
    "limit": 10,
    "scenario": "base",
    "strategy_version": "forward-paper",
    "model_version": "current",
    "history_limit": 80,
    "persist": True,
    "data_cutoff_at": "2025-04-01T09:00:00+00:00",
}


class TestRunRequestSnapshots:
    def test_historical_payload_validates(self) -> None:
        snapshot = HistoricalBacktestRunRequestSnapshot.model_validate(
            HISTORICAL_PAYLOAD
        )
        assert snapshot.settle_actions == ["bid_now", "review"]
        assert snapshot.model_dump() == HISTORICAL_PAYLOAD

    def test_forward_payload_validates(self) -> None:
        snapshot = ForwardPaperBiddingRunRequestSnapshot.model_validate(FORWARD_PAYLOAD)
        assert snapshot.data_cutoff_at == "2025-04-01T09:00:00+00:00"
        assert snapshot.model_dump() == FORWARD_PAYLOAD

    def test_forward_payload_is_rejected_by_the_historical_model(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            HistoricalBacktestRunRequestSnapshot.model_validate(FORWARD_PAYLOAD)
        assert "data_cutoff_at" in str(excinfo.value)

    def test_historical_payload_is_rejected_by_the_forward_model(self) -> None:
        with pytest.raises(ValidationError):
            ForwardPaperBiddingRunRequestSnapshot.model_validate(HISTORICAL_PAYLOAD)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HistoricalBacktestRunRequestSnapshot.model_validate(
                {**HISTORICAL_PAYLOAD, "limitt": 5}
            )

    def test_type_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HistoricalBacktestRunRequestSnapshot.model_validate(
                {**HISTORICAL_PAYLOAD, "settle_actions": "bid_now"}
            )

    def test_forward_requires_data_cutoff_at(self) -> None:
        """생산 계약에서 실행 시각은 필수 — 배타성이 구조적으로 성립하는 근거다."""
        payload = {**FORWARD_PAYLOAD}
        del payload["data_cutoff_at"]
        with pytest.raises(ValidationError) as excinfo:
            ForwardPaperBiddingRunRequestSnapshot.model_validate(payload)
        assert "data_cutoff_at" in str(excinfo.value)

    def test_shared_keys_only_payload_can_only_be_historical(self) -> None:
        """공유 키만 있는 payload 가 두 모델 모두에 맞아떨어지는 우연을 막는다.

        forward 는 ``data_cutoff_at`` 을 요구하고 historical 은 그 키를 금지하므로,
        어떤 payload 도 두 모양에 동시에 유효할 수 없다(union 이 모호해지지 않는다).
        """
        shared_only = {
            "category": None,
            "limit": 10,
            "scenario": "base",
            "strategy_version": "local-backtest",
            "model_version": "current",
            "history_limit": 80,
            "persist": True,
        }
        assert HistoricalBacktestRunRequestSnapshot.model_validate(shared_only)
        with pytest.raises(ValidationError):
            ForwardPaperBiddingRunRequestSnapshot.model_validate(shared_only)

    def test_persisted_variants_drop_unknown_legacy_keys(self) -> None:
        historical = PersistedHistoricalBacktestRunRequestSnapshot.model_validate(
            {**HISTORICAL_PAYLOAD, "legacy_flag": True}
        )
        forward = PersistedForwardPaperBiddingRunRequestSnapshot.model_validate(
            {**FORWARD_PAYLOAD, "legacy_flag": True}
        )
        assert historical.limit == 10
        assert forward.limit == 10

    def test_persisted_variants_preserve_unrecorded_fields_as_null(self) -> None:
        """복원 전용 모델은 기록되지 않은 필드를 기본값으로 날조하지 않는다."""
        historical = PersistedHistoricalBacktestRunRequestSnapshot.model_validate(
            {"limit": 5}
        )
        assert historical.limit == 5
        assert historical.persist is None
        assert historical.scenario is None
        assert historical.award_categories is None
        assert historical.cutoff_hours_before_deadline is None

        forward = PersistedForwardPaperBiddingRunRequestSnapshot.model_validate({})
        assert forward.data_cutoff_at is None
        assert forward.limit is None

    def test_production_models_keep_their_own_defaults(self) -> None:
        """복원 경로의 느슨함이 생산 계약으로 새지 않는다(기본값 출처가 갈라져 있음)."""
        production = HistoricalBacktestRunRequestSnapshot.model_validate({"limit": 5})
        assert production.persist is False
        assert production.scenario == ""
        assert production.award_categories == []


class TestForwardPaperRunParams:
    def test_valid_params_validate(self) -> None:
        params = ForwardPaperRunParams(
            operator_id=None,
            category=None,
            limit=10,
            scenario="base",
            strategy_version="forward-paper",
            model_version="current",
            history_limit=80,
            persist=True,
        )
        assert params.limit == 10

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ForwardPaperRunParams.model_validate({"limit": 10})
        assert "scenario" in str(excinfo.value)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ForwardPaperRunParams.model_validate(
                {
                    "limit": 10,
                    "scenario": "base",
                    "strategy_version": "forward-paper",
                    "model_version": "current",
                    "history_limit": 80,
                    "persist": True,
                    "settle_actions": ["bid_now"],
                }
            )


class TestForwardSettlementRunResult:
    def test_valid_payload_validates(self) -> None:
        result = ForwardSettlementRunResult(
            operator_id=None,
            scanned_count=1,
            settled_count=1,
            skipped_count=0,
            limit=10,
            persist=True,
            summary=PaperBiddingRunSummary(settled_count=1),
            settlements=[],
        )
        assert result.summary.settled_count == 1

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ForwardSettlementRunResult.model_validate(
                {
                    "operator_id": None,
                    "scanned_count": 1,
                    "settled_count": 1,
                    "limit": 10,
                    "persist": True,
                    "summary": {},
                    "settlements": [],
                }
            )
        assert "skipped_count" in str(excinfo.value)


class TestSettlementOverview:
    def _payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": "settled",
            "label": "정산 완료",
            "detail": "1건 모두 최종 결과로 정산되었습니다.",
            "paper_bid_count": 1,
            "settled_count": 1,
            "unsettled_count": 0,
            "ready_to_settle_count": 0,
            "waiting_result_count": 0,
            "before_deadline_count": 0,
            "missing_deadline_count": 0,
            "next_confirmable_at": None,
            "next_deadline_at": None,
            "oldest_waiting_deadline_at": None,
            "latest_settled_at": datetime(2025, 3, 12, tzinfo=UTC),
        }
        payload.update(overrides)
        return payload

    def test_valid_payload_validates_with_default_basis(self) -> None:
        overview = PaperBiddingSettlementOverview.model_validate(self._payload())
        assert overview.settlement_basis == SETTLEMENT_BASIS

    def test_missing_required_count_is_rejected(self) -> None:
        payload = self._payload()
        del payload["unsettled_count"]
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingSettlementOverview.model_validate(payload)
        assert "unsettled_count" in str(excinfo.value)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingSettlementOverview.model_validate(
                self._payload(settled_countt=1)
            )


class TestPersistedRecords:
    def _paper_bid_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": 1,
            "run_id": 1,
            "project_id": 7,
            "project_title": "도로 보수 공사",
            "notice_number": None,
            "category": "construction",
            "action": "bid_now",
            "decision_status": "planned",
            "data_cutoff_at": datetime(2025, 3, 9, tzinfo=UTC),
            "paper_bid_amount": 88_000_000.0,
            "paper_bid_rate": 0.88,
            "scenario": "base",
            "priority_score": 0.7,
            "probability_score": 0.6,
            "matched_score": 0.9,
            "predicted_price": 88_000_000.0,
            "predicted_bid_rate": 0.88,
            "confidence_score": 0.75,
            "predictor_name": "historical_statistical",
            "input_snapshot_hash": "a" * 64,
            "created_at": None,
        }
        payload.update(overrides)
        return payload

    def test_paper_bid_record_validates(self) -> None:
        record = PaperBiddingPaperBidRecord.model_validate(self._paper_bid_payload())
        assert record.run_id == 1
        assert record.created_at is None

    def test_paper_bid_record_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingPaperBidRecord.model_validate(
                self._paper_bid_payload(settlement_reason="x")
            )

    def test_settlement_record_tolerates_legacy_verdict_vocabulary(self) -> None:
        """DB 행의 판정은 ``str`` 계약이다 — 어휘가 바뀌기 전 행도 읽혀야 한다."""
        record = PaperBiddingSettlementRecord.model_validate(
            {
                "id": 1,
                "paper_bid_id": 1,
                "tender_result_id": None,
                "result_status": "awarded",
                "winning_company": None,
                "winning_amount": 0.0,
                "winning_rate": 0.0,
                "amount_delta": 0.0,
                "absolute_error_rate": 0.0,
                "bid_rate_delta": 0.0,
                "absolute_bid_rate_error": 0.0,
                "price_close": False,
                "price_competitive": False,
                "would_have_won_price_only": "legacy_value",
                "would_have_won_final": None,
                "settlement_reason": None,
                "settled_at": None,
            }
        )
        assert record.would_have_won_price_only == "legacy_value"
        assert record.would_have_won_final is None

    def test_settlement_record_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingSettlementRecord.model_validate({"id": 1})
        assert "paper_bid_id" in str(excinfo.value)
