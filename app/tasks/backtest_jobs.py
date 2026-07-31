"""Backtest job bodies.

Extracted from ``app.tasks.jobs`` (§4.5 size decomposition). The ``@task`` entries
``run_synthetic_operator_backtest`` / ``run_historical_backtest`` stay in
``app.tasks.jobs`` (registration names unchanged) as thin shells that **validate
the broker payload** into the DTOs in ``app.schemas.task_payloads`` and delegate
here. These bodies therefore receive a validated model, not a raw ``dict``: the
hand-rolled coercers (``int(payload.get("limit") or 100)`` …) that used to live
here are now the DTO's declared field constraints.

These bodies own their ``db`` lifecycle via the shared ``task_session`` seam and
accept an injectable ``session_factory`` (same axis as the in-process schedulers),
so no module-global monkeypatch is needed to drive them against a test session.
"""

from typing import Callable

from sqlalchemy.orm import Session

from app.core.database import task_session
from app.schemas.task_payloads import (
    HistoricalBacktestTaskRequest,
    SyntheticOperatorBacktestTaskRequest,
)


def run_synthetic_operator_backtest_job(
    request: SyntheticOperatorBacktestTaskRequest,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> dict:
    """Run the per-synthetic-operator backtest in a background worker.

    Mirrors the synchronous `/api/v1/synthetic/backtests/run` endpoint but
    returns the comparison payload as the task result so the frontend can poll
    `/api/v1/synthetic/backtests/tasks/{task_id}` for completion.

    When the payload carries a ``run_id`` (Experiment Lab execution), the
    run/result lifecycle is persisted via ``run_experiment_backtest``; the legacy
    ad-hoc path (no ``run_id``) keeps its original behaviour unchanged. That
    runner still consumes a ``dict``, so it receives the validated model dumped
    back to exactly the keys the sender set.
    """
    from app.services.synthetic_backtest import SyntheticBacktestService

    if request.is_experiment_run:
        from app.services.synthetic_experiment import run_experiment_backtest

        # mode="json" 은 UTC 를 ``…Z`` 로 덤프하므로 러너의 ``_parse_dt``
        # (``datetime.fromisoformat``)는 Python 3.11+ 가 필요하다(런타임 3.12).
        return run_experiment_backtest(
            request.model_dump(mode="json", exclude_unset=True)
        )

    with task_session(session_factory) as db:
        return SyntheticBacktestService().run_for_all(
            db,
            start_at=request.start_at,
            end_at=request.end_at,
            category=request.category,
            limit=request.limit,
            scenario=request.scenario,
            slugs=request.slugs,
            cutoff_hours_before_deadline=request.resolved_cutoff_hours(),
            history_limit=request.history_limit,
            settle_actions=request.resolved_settle_actions(),
        )


def run_historical_backtest_job(
    request: HistoricalBacktestTaskRequest,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> dict:
    """Replay awarded TenderResults as paper_bid + settlement comparison.

    The replay window is derived from ``lookback_days`` (falling back to
    ``HISTORICAL_BACKTEST_LOOKBACK_DAYS``), so the request carries no explicit
    ``start_at``/``end_at``.
    """
    from datetime import datetime, timedelta, timezone
    from app.core.config import settings as runtime_settings
    from app.services.paper_bidding_backtest import PaperBiddingBacktestService

    with task_session(session_factory) as db:
        lookback = max(
            1,
            request.lookback_days
            or runtime_settings.HISTORICAL_BACKTEST_LOOKBACK_DAYS,
        )
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=lookback)
        return PaperBiddingBacktestService().run_historical_backtest(
            db,
            category=request.category or None,
            start_at=start_at,
            end_at=end_at,
            limit=request.limit,
            scenario=request.scenario,
            strategy_version=request.strategy_version,
            model_version=request.model_version,
            cutoff_hours_before_deadline=request.cutoff_hours_before_deadline,
            history_limit=request.history_limit,
            settle_actions=tuple(request.settle_actions),
            persist=request.persist,
        )
