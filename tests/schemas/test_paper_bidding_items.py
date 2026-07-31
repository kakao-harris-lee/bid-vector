"""``app.schemas.paper_bidding_items`` 계약 테스트 (정상 + 거부 경로).

종전 이 경계는 무타입 dict 였고 필수/옵셔널이 코드로 판별되지 않았다. 계약을 DTO 로
올린 뒤에도 **무엇이 필수이고 무엇이 어휘 밖인지**가 회귀로 느슨해지지 않게 happy 와
sad 를 쌍으로 고정한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.paper_bidding_items import (
    PaperBiddingCandidateItem,
    PaperBiddingRunSummary,
    PaperBiddingSettlementInput,
    PaperBiddingSettlementItem,
    PersistedPaperBiddingRunSummary,
)


def _candidate_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": 7,
        "project_title": "도로 보수 공사",
        "notice_number": None,
        "category": "construction",
        "issuing_agency": "서울시",
        "data_cutoff_at": "2025-03-09T00:00:00+00:00",
        "deadline": "2025-03-10T00:00:00+00:00",
        "budget_estimate": 100_000_000.0,
        "scenario": "base",
        "action": "bid_now",
        "decision_status": "planned",
        "paper_bid_amount": 88_000_000.0,
        "paper_bid_rate": 0.88,
        "priority_score": 0.71,
        "probability_score": 0.62,
        "matched_score": 0.9,
        "predicted_price": 88_000_000.0,
        "predicted_bid_rate": 0.88,
        "price_range_min": 87_000_000.0,
        "price_range_max": 89_000_000.0,
        "confidence_score": 0.75,
        "predictor_name": "historical_statistical",
        "predictor_family": "statistical",
        "model_version": "current",
        "strategy_version": "local-backtest",
        "historical_sample_size": 8,
        "history_ids": [1, 2, 3],
        "input_snapshot_hash": "a" * 64,
        "matched_score_source": "heuristic",
        "match_reasons": [],
        "reasoning": "우선순위 충족",
    }
    payload.update(overrides)
    return payload


def _settlement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": 7,
        "category": "construction",
        "budget_estimate": 100_000_000.0,
        "result_time": "2025-03-11T00:00:00+00:00",
        "tender_result_id": 3,
        "result_status": "awarded",
        "winning_company": "Winner",
        "winning_amount": 88_100_000.0,
        "winning_rate": 0.881,
        "amount_delta": -100_000.0,
        "absolute_error_rate": 0.001135,
        "bid_rate_delta": -0.001,
        "absolute_bid_rate_error": 0.001,
        "price_close": True,
        "price_competitive": True,
        "would_have_won_price_only": "plausible",
        "would_have_won_final": "eligible_favorable",
        "estimated_price": 100_000_000.0,
        "minimum_bid_price": 87_000_000.0,
        "settlement_reason": "적격·유리",
    }
    payload.update(overrides)
    return payload


class TestPaperBiddingCandidateItem:
    def test_valid_payload_validates(self) -> None:
        item = PaperBiddingCandidateItem.model_validate(_candidate_payload())
        assert item.project_id == 7
        assert item.action == "bid_now"
        assert item.notice_number is None

    def test_missing_required_field_is_rejected(self) -> None:
        payload = _candidate_payload()
        del payload["paper_bid_amount"]
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingCandidateItem.model_validate(payload)
        assert "paper_bid_amount" in str(excinfo.value)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingCandidateItem.model_validate(
                _candidate_payload(paper_bid_ammount=1)
            )
        assert "paper_bid_ammount" in str(excinfo.value)

    def test_type_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingCandidateItem.model_validate(
                _candidate_payload(history_ids="1,2,3")
            )

    def test_action_outside_gate_vocabulary_is_rejected(self) -> None:
        """결정 게이트가 낼 수 없는 action 이 조용히 흐르지 않는다."""
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingCandidateItem.model_validate(_candidate_payload(action="submit"))
        assert "action" in str(excinfo.value)

    def test_nullable_notice_fields_accept_none(self) -> None:
        item = PaperBiddingCandidateItem.model_validate(
            _candidate_payload(
                project_title=None, category=None, issuing_agency=None, deadline=None
            )
        )
        assert item.project_title is None
        assert item.deadline is None


class TestPaperBiddingSettlementInput:
    def test_valid_payload_validates(self) -> None:
        item = PaperBiddingSettlementInput.model_validate(
            {
                "project_id": 7,
                "category": "construction",
                "budget_estimate": 100_000_000.0,
                "paper_bid_amount": 88_000_000.0,
                "paper_bid_rate": 0.88,
            }
        )
        assert item.paper_bid_amount == 88_000_000.0

    def test_budget_estimate_defaults_to_zero(self) -> None:
        """예산을 모르는 forward 정산 경로에서도 게이트 가드가 그대로 적용된다."""
        item = PaperBiddingSettlementInput.model_validate(
            {
                "project_id": 7,
                "category": None,
                "paper_bid_amount": 1.0,
                "paper_bid_rate": 0.5,
            }
        )
        assert item.budget_estimate == 0.0

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingSettlementInput.model_validate(
                {"project_id": 7, "category": None, "paper_bid_rate": 0.5}
            )
        assert "paper_bid_amount" in str(excinfo.value)

    def test_none_bid_amount_is_rejected(self) -> None:
        """종전 ``float(item[...] or 0.0)`` 가 흡수하던 None 은 이제 계약에서 막힌다."""
        with pytest.raises(ValidationError):
            PaperBiddingSettlementInput.model_validate(
                {
                    "project_id": 7,
                    "category": None,
                    "paper_bid_amount": None,
                    "paper_bid_rate": 0.5,
                }
            )

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingSettlementInput.model_validate(
                {
                    "project_id": 7,
                    "category": None,
                    "paper_bid_amount": 1.0,
                    "paper_bid_rate": 0.5,
                    "action": "bid_now",
                }
            )

    def test_from_candidate_narrows_to_the_five_read_fields(self) -> None:
        candidate = PaperBiddingCandidateItem.model_validate(_candidate_payload())
        narrowed = PaperBiddingSettlementInput.from_candidate(candidate)
        assert narrowed.model_dump() == {
            "project_id": candidate.project_id,
            "category": candidate.category,
            "budget_estimate": candidate.budget_estimate,
            "paper_bid_amount": candidate.paper_bid_amount,
            "paper_bid_rate": candidate.paper_bid_rate,
        }


class TestPaperBiddingSettlementItem:
    def test_valid_payload_validates(self) -> None:
        item = PaperBiddingSettlementItem.model_validate(_settlement_payload())
        assert item.would_have_won_final == "eligible_favorable"

    def test_absent_reserve_data_keeps_unknown_verdict_with_null_prices(self) -> None:
        """정직 명세: 예정가가 없으면 최종 판정은 unknown, 가격은 None 을 유지한다."""
        item = PaperBiddingSettlementItem.model_validate(
            _settlement_payload(
                would_have_won_final="unknown",
                estimated_price=None,
                minimum_bid_price=None,
            )
        )
        assert item.would_have_won_final == "unknown"
        assert item.estimated_price is None
        assert item.minimum_bid_price is None

    def test_verdict_outside_vocabulary_is_rejected(self) -> None:
        """"won" 처럼 실제 낙찰로 읽히는 값이 판정 어휘에 끼어들 수 없다."""
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingSettlementItem.model_validate(
                _settlement_payload(would_have_won_final="won")
            )
        assert "would_have_won_final" in str(excinfo.value)

    def test_price_only_verdict_outside_vocabulary_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingSettlementItem.model_validate(
                _settlement_payload(would_have_won_price_only="likely")
            )

    def test_missing_required_field_is_rejected(self) -> None:
        payload = _settlement_payload()
        del payload["settlement_reason"]
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingSettlementItem.model_validate(payload)
        assert "settlement_reason" in str(excinfo.value)

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingSettlementItem.model_validate(
                _settlement_payload(would_have_won=True)
            )


class TestPaperBiddingRunSummary:
    def test_empty_summary_is_all_zero_with_absent_averages(self) -> None:
        summary = PaperBiddingRunSummary()
        assert summary.settled_count == 0
        assert summary.action_counts == {}
        # 표본이 없으면 평균 오차는 0.0 이 아니라 부재(None)다.
        assert summary.average_absolute_bid_rate_error is None
        assert summary.average_absolute_amount_error_rate is None

    def test_unknown_field_is_rejected_on_the_production_model(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            PaperBiddingRunSummary.model_validate({"settled_countt": 3})
        assert "settled_countt" in str(excinfo.value)

    def test_type_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaperBiddingRunSummary.model_validate({"action_counts": {"bid_now": "x"}})

    def test_persisted_variant_drops_unknown_legacy_keys(self) -> None:
        """지표가 개명·삭제된 시절의 payload 를 읽어도 목록 API 가 죽지 않는다."""
        summary = PersistedPaperBiddingRunSummary.model_validate_json(
            '{"settled_count": 2, "legacy_win_count": 9}'
        )
        assert summary.settled_count == 2
        assert not hasattr(summary, "legacy_win_count")

    def test_persisted_variant_keeps_extra_forbid_off_only_for_reads(self) -> None:
        assert PaperBiddingRunSummary.model_config["extra"] == "forbid"
        assert PersistedPaperBiddingRunSummary.model_config["extra"] == "ignore"
