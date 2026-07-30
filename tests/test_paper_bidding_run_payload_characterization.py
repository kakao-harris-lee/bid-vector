"""정산/백테스트 run payload 산출 불변 characterization (골든 diff 0).

방어적 DTO 규율 Phase 1. `settlement -> summary -> orchestration -> persistence`
체인이 무타입 dict 릴레이에서 Pydantic DTO 체인으로 바뀌어도 **경계 산출(JSON)은
byte 단위로 동일**해야 한다. 그래서 리팩토링 *전* 코드에서 캡처한 골든을 저장하고,
리팩토링 후 같은 시나리오가 같은 payload 를 만드는지 비교한다.

고정 대상:

* ``run_historical_backtest`` 전체 반환 payload (run_id/request/summary/items/settlements)
* ``run_forward_paper_bidding`` 전체 반환 payload
* ``run_forward_settlement`` 전체 반환 payload
* 영속화된 ``PaperBidRun.request_payload`` / ``result_payload`` 를 되읽은 값

의도된 동작 변경 후에만 재생성한다::

    PAPER_BIDDING_GOLDEN_REGEN=1 pytest -q tests/test_paper_bidding_run_payload_characterization.py

시간 의존: forward 경로는 ``utc_now()`` 로 data_cutoff 를 잡으므로 골든이 흔들린다.
``run_forward_paper_bidding`` 을 정의한 모듈의 ``utc_now`` 를 고정 시각으로 패치해
결정성을 확보한다(패치 대상은 ``__module__`` 로 찾으므로 모듈 분해에도 견딘다).
"""

from __future__ import annotations

import importlib
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.single_user import ensure_operator_account
from app.schemas.paper_bidding_items import PaperBiddingRunSummary
from app.models.models import (
    HistoricalData,
    PaperBid,
    PaperBidRun,
    Project,
    TenderResult,
)
from app.services.paper_bidding_backtest import PaperBiddingBacktestService

GOLDEN_DIR = Path(__file__).parent / "goldens" / "paper_bidding"
REGEN = os.environ.get("PAPER_BIDDING_GOLDEN_REGEN") == "1"

FROZEN_NOW = datetime(2025, 4, 1, 9, 0, tzinfo=UTC)

BASE_AMOUNT = 100_000_000.0
# construction 낙찰하한율 0.87 기준: 예정가 100M -> 하한 87M, 예정가 103M -> 하한 89.61M.
# 페이퍼 투찰가는 아래 fake predictor 가 항상 88M 을 내므로 예정가만 바꿔 게이트
# 4개 판정(favorable / outbid / unknown / disqualified)을 모두 태운다.
FAVORABLE_WINNING_AMOUNT = 88_100_000.0
OUTBID_WINNING_AMOUNT = 87_500_000.0
UNKNOWN_WINNING_AMOUNT = 90_000_000.0
DISQUALIFIED_RESERVE_AMOUNT = 103_000_000.0


class FrozenPredictionPort:
    """결정적 예측 포트 — 골든이 predictor 튜닝에 흔들리지 않게 고정값을 낸다."""

    def predict_price(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "predicted_price": 88_000_000.0,
            "predicted_bid_rate": 0.88,
            "price_range_min": 87_000_000.0,
            "price_range_max": 89_000_000.0,
            "confidence_score": 0.75,
            "predictor_name": "characterization_fake",
            "predictor_family": "fake",
            "model_version": "fake-1",
            "bid_rate_candidates": [
                {
                    "label": "conservative",
                    "bid_rate": 0.90,
                    "predicted_price": 90_000_000.0,
                },
                {"label": "base", "bid_rate": 0.88, "predicted_price": 88_000_000.0},
                {
                    "label": "aggressive",
                    "bid_rate": 0.86,
                    "predicted_price": 86_000_000.0,
                },
            ],
        }


def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def _project(*, title: str, deadline: datetime, status: str = "awarded") -> Project:
    return Project(
        title=title,
        description="Road maintenance project",
        requirements="standard qualification",
        budget_estimate=BASE_AMOUNT,
        category="construction",
        status=status,
        issuing_agency="Seoul",
        created_at=_dt(2025, 2, 1),
        deadline=deadline,
    )


def _unlinked_history(*, opened_at: datetime, bid_rate: float) -> HistoricalData:
    """predictor 입력용 과거 표본 (target project 와 연결되지 않은 행)."""
    return HistoricalData(
        notice_number=f"H-{opened_at:%Y%m%d}",
        agency_name="Seoul",
        category="construction",
        base_amount=BASE_AMOUNT,
        predicted_price=BASE_AMOUNT * bid_rate,
        bid_rate=bid_rate,
        opened_at=opened_at,
    )


def _settled_history_for(
    project: Project, *, reserve_amount: float = BASE_AMOUNT
) -> HistoricalData:
    """정산 시 예정가/낙찰하한가를 유도하는 개찰 완료 행."""
    return HistoricalData(
        project_id=project.id,
        notice_number=f"GATE-{project.id}",
        agency_name="Seoul",
        category="construction",
        base_amount=BASE_AMOUNT,
        predicted_price=0.0,
        bid_rate=0.88,
        reserve_prices=json.dumps([reserve_amount] * 4),
        selected_numbers=json.dumps([1, 2, 3, 4]),
        opened_at=_dt(2025, 3, 12),
    )


def _award(project: Project, *, winning_amount: float) -> TenderResult:
    return TenderResult(
        project_id=project.id,
        winning_company="Winner",
        winning_amount=winning_amount,
        winning_rate=winning_amount / BASE_AMOUNT,
        result_status="awarded",
        announced_at=_dt(2025, 3, 11),
    )


def _service() -> PaperBiddingBacktestService:
    return PaperBiddingBacktestService(price_prediction_port=FrozenPredictionPort())


def _assert_golden(name: str, payload: Any) -> None:
    """골든과 비교한다. 기록은 ``REGEN`` 일 때만.

    골든이 없는데 조용히 만들어 두면 "현재 산출을 기록하고 그 기록과 비교"하는 자기봉인
    이 되어 어떤 회귀도 잡지 못한다(골든 파일을 지우면 항상 통과). 그래서 부재는
    **실패**로 다룬다.
    """
    path = GOLDEN_DIR / f"{name}.json"
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if REGEN:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{serialized}\n", encoding="utf-8")
        pytest.skip(f"골든 재생성: {path.name}")
    if not path.exists():
        pytest.fail(
            f"골든 없음: {name} — 의도된 변경이면 PAPER_BIDDING_GOLDEN_REGEN=1"
        )
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert json.loads(serialized) == expected


def _seed_historical_awards(test_db) -> dict[str, Project]:
    """4개 정산 게이트 판정을 모두 만드는 개찰 완료 공고 집합."""
    ensure_operator_account(test_db)
    for index in range(8):
        test_db.add(
            _unlinked_history(
                opened_at=_dt(2025, 1, 1) + timedelta(days=index), bid_rate=0.88
            )
        )

    projects: dict[str, Project] = {
        "favorable": _project(title="favorable tender", deadline=_dt(2025, 3, 10)),
        "outbid": _project(title="outbid tender", deadline=_dt(2025, 3, 9)),
        "unknown": _project(title="unknown tender", deadline=_dt(2025, 3, 8)),
        "disqualified": _project(title="disqualified tender", deadline=_dt(2025, 3, 7)),
    }
    for project in projects.values():
        test_db.add(project)
    test_db.flush()

    test_db.add(_settled_history_for(projects["favorable"]))
    test_db.add(_settled_history_for(projects["outbid"]))
    test_db.add(
        _settled_history_for(
            projects["disqualified"], reserve_amount=DISQUALIFIED_RESERVE_AMOUNT
        )
    )
    # "unknown" 은 개찰 완료 행이 없어 예정가를 유도할 수 없다 -> 판정 보류.

    test_db.add(_award(projects["favorable"], winning_amount=FAVORABLE_WINNING_AMOUNT))
    test_db.add(_award(projects["outbid"], winning_amount=OUTBID_WINNING_AMOUNT))
    test_db.add(_award(projects["unknown"], winning_amount=UNKNOWN_WINNING_AMOUNT))
    test_db.add(
        _award(projects["disqualified"], winning_amount=FAVORABLE_WINNING_AMOUNT)
    )
    test_db.commit()
    return projects


def _run_historical(test_db) -> dict[str, Any]:
    return _service().run_historical_backtest(
        test_db,
        category="construction",
        start_at=_dt(2025, 3, 1),
        end_at=_dt(2025, 3, 31),
        limit=10,
        persist=True,
        settle_actions=("bid_now", "review"),
    )


def test_historical_run_payload_matches_golden(test_db):
    _seed_historical_awards(test_db)

    result = _run_historical(test_db)

    _assert_golden("historical_run", result)


def test_historical_run_settlement_verdicts_cover_every_gate_branch(test_db):
    """골든이 무엇을 덮고 있는지 명시 — 4개 낙찰하한 판정이 모두 표본에 있다."""
    _seed_historical_awards(test_db)

    result = _run_historical(test_db)

    verdicts = {
        item["would_have_won_final"] for item in result["settlements"]
    }
    assert verdicts == {
        "eligible_favorable",
        "eligible_but_outbid",
        "unknown",
        "disqualified",
    }
    # 정직 명세: 예정가가 없으면 최종 판정은 unknown 으로 남고 가격 근접 추정치는 별도다.
    unknown_items = [
        item
        for item in result["settlements"]
        if item["would_have_won_final"] == "unknown"
    ]
    assert unknown_items
    for item in unknown_items:
        assert item["estimated_price"] is None
        assert item["minimum_bid_price"] is None
        assert item["would_have_won_price_only"] in {
            "plausible",
            "competitive",
            "unlikely",
        }


def test_historical_run_persisted_payloads_match_returned_payload(test_db):
    """영속화 왕복 불변: 저장된 request/result payload == 반환 payload."""
    _seed_historical_awards(test_db)

    result = _run_historical(test_db)

    run = test_db.query(PaperBidRun).one()
    assert json.loads(run.request_payload) == result["request"]
    assert json.loads(run.result_payload) == result["summary"]


def test_forward_run_payload_matches_golden(test_db, monkeypatch):
    ensure_operator_account(test_db)
    for index in range(8):
        test_db.add(
            _unlinked_history(
                opened_at=_dt(2025, 1, 1) + timedelta(days=index), bid_rate=0.88
            )
        )
    test_db.add(
        _project(title="open forward tender", deadline=_dt(2025, 5, 1), status="open")
    )
    test_db.add(
        _project(
            title="second forward tender", deadline=_dt(2025, 5, 2), status="re_notice"
        )
    )
    # 예산 0 공고는 candidate 생성이 ValueError -> skipped_invalid_count 경로를 태운다.
    invalid = _project(
        title="zero budget forward tender", deadline=_dt(2025, 5, 3), status="open"
    )
    invalid.budget_estimate = 0.0
    test_db.add(invalid)
    test_db.commit()

    forward_module = importlib.import_module(
        PaperBiddingBacktestService.run_forward_paper_bidding.__module__
    )
    monkeypatch.setattr(forward_module, "utc_now", lambda: FROZEN_NOW)

    result = _service().run_forward_paper_bidding(
        test_db, limit=10, persist=True, history_limit=80
    )

    _assert_golden("forward_run", result)


def test_build_summary_sets_every_field(test_db):
    """생산 경로 완전성 가드 — 요약 DTO 의 기본값이 "빠진 지표"를 가리지 못하게 한다.

    ``PaperBiddingRunSummary`` 는 과거 payload 복원 때문에 모든 필드에 기본값이 있다.
    그러면 ``_build_summary`` 가 지표 하나를 빼먹어도 0 으로 조용히 통과하므로,
    생산자가 **모든 필드를 명시적으로 채웠는지**(``model_fields_set``)를 고정한다.
    """
    _seed_historical_awards(test_db)
    result = _run_historical(test_db)
    assert result["summary"]

    summary = _service()._build_summary(
        candidate_items=[],
        settlement_items=[],
        skipped_by_strategy=0,
        action_counts=Counter(),
    )
    assert summary.model_fields_set == set(PaperBiddingRunSummary.model_fields)


def test_forward_settlement_payload_matches_golden(test_db, monkeypatch):
    """마감이 지난 forward 페이퍼 투찰을 뒤늦게 정산하는 경로."""
    projects = _seed_historical_awards(test_db)
    target = projects["favorable"]

    run = PaperBidRun(
        operator_id=ensure_operator_account(test_db).id,
        strategy_version="forward-paper",
        model_version="current",
        status="completed",
        mode="forward_paper",
        scenario="base",
        data_cutoff_policy="execution_time",
        started_at=_dt(2025, 3, 1),
    )
    test_db.add(run)
    test_db.flush()
    test_db.add(
        PaperBid(
            run_id=run.id,
            project_id=target.id,
            operator_id=run.operator_id,
            notice_number=None,
            action="bid_now",
            decision_status="planned",
            data_cutoff_at=_dt(2025, 3, 1),
            paper_bid_amount=88_000_000.0,
            paper_bid_rate=0.88,
            scenario="base",
            priority_score=0.5,
            probability_score=0.5,
            matched_score=0.85,
            predicted_price=88_000_000.0,
            predicted_bid_rate=0.88,
            price_range_min=87_000_000.0,
            price_range_max=89_000_000.0,
            confidence_score=0.75,
            predictor_name="characterization_fake",
            predictor_family="fake",
            model_version="fake-1",
            strategy_version="forward-paper",
            input_snapshot_hash="frozen-hash",
            reasoning="frozen",
        )
    )
    test_db.commit()

    settlement_module = importlib.import_module(
        PaperBiddingBacktestService.run_forward_settlement.__module__
    )
    monkeypatch.setattr(settlement_module, "utc_now", lambda: FROZEN_NOW)

    result = _service().run_forward_settlement(test_db, limit=10, persist=True)

    _assert_golden("forward_settlement_run", result)
