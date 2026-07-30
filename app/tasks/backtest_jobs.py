"""Backtest job bodies.

Extracted verbatim from ``app.tasks.jobs`` (§4.5 size decomposition). The
``@task`` entries ``run_synthetic_operator_backtest`` / ``run_historical_backtest``
stay in ``app.tasks.jobs`` (registration names unchanged) as thin shells that
delegate here. These bodies own their own ``db`` lifecycle via the shared
``task_session`` seam and accept an injectable ``session_factory`` (same axis as
the in-process schedulers), so no module-global monkeypatch is needed to drive
them against a test session.
"""

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.database import task_session


def run_synthetic_operator_backtest_job(
    payload: dict[str, Any] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> dict:
    """Run the per-synthetic-operator backtest in a background worker.

    Mirrors the synchronous `/api/v1/synthetic/backtests/run` endpoint but
    returns the comparison payload as the task result so the frontend can poll
    `/api/v1/synthetic/backtests/tasks/{task_id}` for completion.

    When the payload carries a ``run_id`` (Experiment Lab execution), the
    run/result lifecycle is persisted via ``run_experiment_backtest``; the legacy
    ad-hoc path (no ``run_id``) keeps its original behaviour unchanged.
    """
    from datetime import datetime
    from app.services.synthetic_backtest import SyntheticBacktestService

    data = dict(payload or {})

    if data.get("run_id") is not None:
        from app.services.synthetic_experiment import run_experiment_backtest

        return run_experiment_backtest(data)
    start_at_raw = data.get("start_at")
    end_at_raw = data.get("end_at")
    start_at = datetime.fromisoformat(start_at_raw) if isinstance(start_at_raw, str) else None
    end_at = datetime.fromisoformat(end_at_raw) if isinstance(end_at_raw, str) else None
    settle_actions_raw = data.get("settle_actions")
    if isinstance(settle_actions_raw, str):
        settle_actions = tuple(s.strip() for s in settle_actions_raw.split(",") if s.strip())
    elif isinstance(settle_actions_raw, (list, tuple)):
        settle_actions = tuple(str(s).strip() for s in settle_actions_raw if str(s).strip())
    else:
        settle_actions = None
    cutoff_hours_before_deadline = data.get("cutoff_hours_before_deadline")
    history_limit = data.get("history_limit")

    with task_session(session_factory) as db:
        return SyntheticBacktestService().run_for_all(
            db,
            start_at=start_at,
            end_at=end_at,
            category=data.get("category"),
            limit=int(data.get("limit") or 100),
            scenario=str(data.get("scenario") or "base"),
            slugs=data.get("slugs"),
            cutoff_hours_before_deadline=(
                int(cutoff_hours_before_deadline)
                if cutoff_hours_before_deadline is not None
                else None
            ),
            history_limit=int(history_limit) if history_limit is not None else None,
            settle_actions=settle_actions,
        )


def run_historical_backtest_job(
    request_payload: dict[str, Any] | None = None,
    *,
    session_factory: Callable[[], Session] | None = None,
) -> dict:
    """Replay awarded TenderResults as paper_bid + settlement comparison."""
    from datetime import datetime, timedelta, timezone
    from app.core.config import settings as runtime_settings
    from app.services.paper_bidding_backtest import PaperBiddingBacktestService

    payload = dict(request_payload or {})
    with task_session(session_factory) as db:
        lookback = max(1, int(payload.pop("lookback_days", runtime_settings.HISTORICAL_BACKTEST_LOOKBACK_DAYS)))
        end_at = datetime.now(timezone.utc)
        start_at = end_at - timedelta(days=lookback)
        settle_actions_raw = payload.pop("settle_actions", None)
        if isinstance(settle_actions_raw, str):
            settle_actions = tuple(s.strip() for s in settle_actions_raw.split(",") if s.strip())
        elif isinstance(settle_actions_raw, (list, tuple)):
            settle_actions = tuple(settle_actions_raw)
        else:
            settle_actions = ("bid_now", "review")
        return PaperBiddingBacktestService().run_historical_backtest(
            db,
            category=payload.get("category") or None,
            start_at=start_at,
            end_at=end_at,
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            strategy_version=str(payload.get("strategy_version") or "scheduled-historical-backtest"),
            model_version=str(payload.get("model_version") or "current"),
            cutoff_hours_before_deadline=int(payload.get("cutoff_hours_before_deadline") or 2),
            history_limit=int(payload.get("history_limit") or 80),
            settle_actions=settle_actions,
            persist=bool(payload.get("persist", True)),
        )
