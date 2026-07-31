"""``PaperBidRun`` JSON payload 직렬화/복원 단일 경로.

종전에는 쓰기가 ``json.dumps(summary, ...)`` (persistence.py), 읽기가
``json.loads`` 후 실패 시 ``{}`` (api/backtests.py ``_parse_json_object``) 로 갈라져
있었다. 쓰는 쪽과 읽는 쪽이 서로의 계약을 모르니 키가 늘거나 줄어도 아무도 실패하지
않았다. 그래서 **직렬화·복원·복원 실패 정책**을 이 한 모듈로 모으고 dump/load 는
``model_dump_json()``/``model_validate_json()`` 만 쓴다.

복원 정책(비대칭인 이유):

* ``summary`` 는 대시보드가 렌더링한다 -> 해석 실패/키 누락이면 **빈(0) 요약**으로
  degrade 한다. ``None`` 을 주면 소비처의 ``summary.<field>`` 접근이 깨진다.
* ``request`` 는 감사 메타데이터다 -> 해석 실패면 **``None``(부재)** 를 유지하고,
  부분 복원은 미기록 필드를 ``null`` 로 남긴다(0 으로 채우면 그 run 이 실제로
  ``limit=0`` 으로 돌았다는 오독을 만든다).

degrade 는 조용해서는 안 된다: 손상된 payload 는 0 으로 렌더링되어 "정산 0건"처럼
보이므로, 어떤 run 이 degrade 됐는지 ``logger.warning`` 으로 남긴다. **payload 원문은
로그에 남기지 않는다**(크기 + 내용 오염 리스크).
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from app.core.constants import FORWARD_PAPER_RUN_MODE, HISTORICAL_BACKTEST_RUN_MODE
from app.schemas.paper_bidding_items import (
    PaperBiddingRunSummary,
    PersistedPaperBiddingRunSummary,
)
from app.schemas.paper_bidding_runs import (
    PaperBiddingRunRequestSnapshot,
    PersistedForwardPaperBiddingRunRequestSnapshot,
    PersistedHistoricalBacktestRunRequestSnapshot,
    PersistedPaperBiddingRunRequestSnapshot,
)

logger = logging.getLogger(__name__)

__all__ = [
    "dump_run_request_snapshot",
    "dump_run_summary",
    "load_run_request_snapshot",
    "load_run_summary",
]

# run mode -> 저장된 요청 스냅샷 복원 모델 (§4.5-2: 값 기반 분기는 룩업 테이블로)
_PERSISTED_REQUEST_BY_MODE: dict[
    str,
    type[PersistedHistoricalBacktestRunRequestSnapshot]
    | type[PersistedForwardPaperBiddingRunRequestSnapshot],
] = {
    HISTORICAL_BACKTEST_RUN_MODE: PersistedHistoricalBacktestRunRequestSnapshot,
    FORWARD_PAPER_RUN_MODE: PersistedForwardPaperBiddingRunRequestSnapshot,
}


def dump_run_request_snapshot(snapshot: PaperBiddingRunRequestSnapshot) -> str:
    """요청 스냅샷을 ``PaperBidRun.request_payload`` 문자열로 직렬화한다."""
    return snapshot.model_dump_json()


def dump_run_summary(summary: PaperBiddingRunSummary) -> str:
    """run 요약을 ``PaperBidRun.result_payload`` 문자열로 직렬화한다."""
    return summary.model_dump_json()


def load_run_summary(
    raw_value: str | None, *, run_id: int | None = None
) -> PaperBiddingRunSummary:
    """저장된 run 요약을 복원한다. 해석 불가면 빈(0) 요약으로 degrade.

    ``run_id`` 는 degrade 를 추적하기 위한 것이다(어느 run 의 요약이 0 으로 렌더링되고
    있는지 로그로 특정할 수 있어야 한다).
    """
    if not raw_value:
        return PersistedPaperBiddingRunSummary()
    try:
        return PersistedPaperBiddingRunSummary.model_validate_json(raw_value)
    except ValidationError as exc:
        logger.warning(
            "paper_bid_run result_payload 해석 실패 — 빈(0) 요약으로 degrade "
            "(run_id=%s, errors=%d)",
            run_id,
            exc.error_count(),
        )
        return PersistedPaperBiddingRunSummary()


def load_run_request_snapshot(
    *, mode: str | None, raw_value: str | None, run_id: int | None = None
) -> PersistedPaperBiddingRunRequestSnapshot | None:
    """저장된 요청 스냅샷을 run mode 로 분기해 복원한다.

    모드를 모르거나(과거 행) 해석에 실패하면 ``None`` — 없는 값을 0 으로 지어내지
    않는다. 복원된 스냅샷의 미기록 필드도 ``null`` 로 남는다.
    """
    model = _PERSISTED_REQUEST_BY_MODE.get(str(mode or ""))
    if model is None or not raw_value:
        return None
    try:
        return model.model_validate_json(raw_value)
    except ValidationError as exc:
        logger.warning(
            "paper_bid_run request_payload 해석 실패 — 요청 스냅샷을 부재로 처리 "
            "(run_id=%s, mode=%s, errors=%d)",
            run_id,
            mode,
            exc.error_count(),
        )
        return None
