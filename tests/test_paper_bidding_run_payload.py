"""``PaperBidRun`` payload 직렬화/복원 단일 경로 계약.

종전에는 쓰기가 ``json.dumps`` (persistence), 읽기가 ``json.loads`` 후 실패 시 ``{}``
(router) 로 갈라져 있어서 계약 위반이 아무 데서도 드러나지 않았다. 이 모듈은
**왕복 불변**과 **복원 실패 정책**(summary 는 빈 요약으로 degrade, request 는 부재
유지)을 고정한다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from app.core.constants import FORWARD_PAPER_RUN_MODE, HISTORICAL_BACKTEST_RUN_MODE
from app.schemas.paper_bidding_items import PaperBiddingRunSummary
from app.schemas.paper_bidding_runs import (
    ForwardPaperBiddingRunRequestSnapshot,
    HistoricalBacktestRunRequestSnapshot,
)
from app.services.paper_bidding_run_payload import (
    dump_run_request_snapshot,
    dump_run_summary,
    load_run_request_snapshot,
    load_run_summary,
)


_PAYLOAD_LOGGER_NAME = "app.services.paper_bidding_run_payload"


@contextmanager
def _captured_warnings() -> Iterator[list[str]]:
    """degrade 로그를 해당 로거에 직접 붙어 수집한다.

    ``caplog`` 를 쓰지 않는 이유: alembic 을 ``Config("alembic.ini")`` 로 돌리는 다른
    테스트가 ``fileConfig(disable_existing_loggers=True)`` 로 전역 로깅을 재설정해
    기존 로거를 ``disabled`` 로 만들어 버린다(같은 함정을
    ``tests/test_business_verification.py`` 도 기록해 두었다). 그래서 이 계약은 전역
    로깅 상태와 무관하게 검증한다.
    """
    messages: list[str] = []

    class _Recorder(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger(_PAYLOAD_LOGGER_NAME)
    handler = _Recorder(level=logging.WARNING)
    previous = (logger.level, logger.disabled)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous[0])
        logger.disabled = previous[1]


def _historical_snapshot() -> HistoricalBacktestRunRequestSnapshot:
    return HistoricalBacktestRunRequestSnapshot(
        category="construction",
        award_categories=["construction"],
        start_at="2025-03-01T00:00:00+00:00",
        end_at="2025-03-31T00:00:00+00:00",
        limit=10,
        scenario="base",
        strategy_version="local-backtest",
        model_version="current",
        cutoff_hours_before_deadline=2,
        history_limit=80,
        settle_actions=["bid_now", "review"],
        persist=True,
    )


def _forward_snapshot() -> ForwardPaperBiddingRunRequestSnapshot:
    return ForwardPaperBiddingRunRequestSnapshot(
        category=None,
        limit=10,
        scenario="base",
        strategy_version="forward-paper",
        model_version="current",
        history_limit=80,
        persist=True,
        data_cutoff_at="2025-04-01T09:00:00+00:00",
    )


class TestRunSummaryPayload:
    def test_round_trip_preserves_every_field(self) -> None:
        summary = PaperBiddingRunSummary(
            candidate_count=4,
            paper_bid_count=4,
            settled_count=4,
            action_counts={"review": 4},
            average_absolute_bid_rate_error=0.00675,
            average_absolute_amount_error_rate=0.007552,
            would_have_won_final_unknown_count=1,
        )
        restored = load_run_summary(dump_run_summary(summary))
        assert restored.model_dump() == summary.model_dump()

    def test_absent_payload_degrades_to_empty_summary(self) -> None:
        """실행 요약이 없어도 ``summary.<field>`` 접근이 깨지지 않아야 한다."""
        assert load_run_summary(None).model_dump() == PaperBiddingRunSummary().model_dump()
        assert load_run_summary("").settled_count == 0

    def test_corrupt_payload_degrades_to_empty_summary(self) -> None:
        assert load_run_summary("{not json").settled_count == 0
        assert load_run_summary('{"settled_count": "many"}').settled_count == 0

    def test_legacy_payload_with_unknown_keys_is_readable(self) -> None:
        restored = load_run_summary('{"settled_count": 3, "legacy_win_count": 9}')
        assert restored.settled_count == 3
        # 없는 지표는 부재(None)/0 으로 남고 예외가 되지 않는다.
        assert restored.average_absolute_bid_rate_error is None

    def test_dump_is_pure_pydantic_json(self) -> None:
        """``json.dumps`` 대신 ``model_dump_json`` 이므로 왕복이 모델 계약을 통과한다."""
        raw = dump_run_summary(PaperBiddingRunSummary(settled_count=1))
        assert '"settled_count":1' in raw

    def test_degrade_is_logged_with_run_id_and_without_payload(self) -> None:
        """degrade 는 조용해서는 안 된다 — 0 요약은 "정산 0건"처럼 보이기 때문이다.

        동시에 payload 원문은 로그에 남기지 않는다(크기 + 내용 오염 리스크).
        """
        secret_marker = "SHOULD-NOT-BE-LOGGED"
        with _captured_warnings() as messages:
            load_run_summary(f'{{"settled_count": "{secret_marker}"}}', run_id=42)

        assert messages
        assert "run_id=42" in messages[-1]
        assert secret_marker not in messages[-1]

    def test_absent_payload_does_not_log(self) -> None:
        """아직 완료되지 않은 run(요약 없음)은 정상 상태라 경고를 만들지 않는다."""
        with _captured_warnings() as messages:
            load_run_summary(None, run_id=7)
        assert messages == []


class TestRunRequestSnapshotPayload:
    @pytest.mark.parametrize(
        ("mode", "snapshot_factory"),
        [
            (HISTORICAL_BACKTEST_RUN_MODE, _historical_snapshot),
            (FORWARD_PAPER_RUN_MODE, _forward_snapshot),
        ],
    )
    def test_round_trip_per_mode(self, mode, snapshot_factory) -> None:
        snapshot = snapshot_factory()
        restored = load_run_request_snapshot(
            mode=mode, raw_value=dump_run_request_snapshot(snapshot)
        )
        assert restored is not None
        assert restored.model_dump() == snapshot.model_dump()

    def test_unknown_mode_yields_absence(self) -> None:
        raw = dump_run_request_snapshot(_historical_snapshot())
        assert load_run_request_snapshot(mode=None, raw_value=raw) is None
        assert load_run_request_snapshot(mode="legacy_mode", raw_value=raw) is None

    def test_absent_or_corrupt_payload_yields_absence(self) -> None:
        """감사 메타데이터는 0 으로 지어내지 않고 부재를 유지한다."""
        assert (
            load_run_request_snapshot(
                mode=HISTORICAL_BACKTEST_RUN_MODE, raw_value=None
            )
            is None
        )
        assert (
            load_run_request_snapshot(
                mode=HISTORICAL_BACKTEST_RUN_MODE, raw_value="{not json"
            )
            is None
        )

    def test_mode_is_the_authoritative_discriminator(self) -> None:
        """복원은 ``run.mode`` 로만 분기한다 — payload 모양 추측을 하지 않는다.

        ``mode`` 와 ``request_payload`` 는 ``_create_run`` 이 한 행에 함께 쓰므로 둘이
        어긋날 수 없다. 그래서 복원은 mode 를 믿고, 되읽기 모델은 미지 키를 버린다
        (지표가 개명된 과거 payload 하나가 목록 API 를 500 으로 만들면 안 된다).
        이 관용의 대가를 명시해 둔다: mode 를 잘못 주면 그 모드의 키만 남는다.
        생산 경로의 두 스냅샷 모델은 서로 배타적임이
        ``tests/schemas/test_paper_bidding_runs.py`` 에서 고정된다.
        """
        raw = dump_run_request_snapshot(_forward_snapshot())
        mismatched = load_run_request_snapshot(
            mode=HISTORICAL_BACKTEST_RUN_MODE, raw_value=raw
        )
        assert isinstance(mismatched, HistoricalBacktestRunRequestSnapshot)
        assert not hasattr(mismatched, "data_cutoff_at")
        # 겹치는 키는 살아남고, 기록에 없던 historical 전용 키는 null(미기록)로 남는다.
        assert mismatched.limit == 10
        assert mismatched.award_categories is None
        assert mismatched.cutoff_hours_before_deadline is None
        assert mismatched.settle_actions is None

    def test_partial_legacy_payload_keeps_unrecorded_fields_null(self) -> None:
        """미기록 필드를 기본값으로 채워 "기록된 값"처럼 날조하지 않는다.

        생산 모델의 기본값(``persist=False``, ``scenario=""``, ``limit=0``)을 복원에
        재사용하면 ``{"limit": 5}`` 만 남은 과거 run 이 "persist=false 로 돌았다"고
        읽힌다. 복원 전용 모델은 전부 ``X | None`` 이라 미기록이 보존된다.
        """
        restored = load_run_request_snapshot(
            mode=HISTORICAL_BACKTEST_RUN_MODE,
            raw_value='{"limit": 5, "legacy_flag": true}',
        )
        assert restored is not None
        assert restored.limit == 5
        assert restored.persist is None
        assert restored.scenario is None
        assert restored.strategy_version is None
        assert restored.award_categories is None
