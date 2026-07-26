"""Experiment-scoped backtest entrypoint invoked by the Celery task."""

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.synthetic_backtest import SyntheticBacktestService

from .sample_gap import (
    _first_present,
    _normalize_settle_actions,
    _safe_optional_int,
)
from .serialization import _parse_dt
from .service import SyntheticExperimentService


def run_experiment_backtest(
    payload: dict[str, Any],
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    """Execute an experiment-scoped backtest inside a fresh DB session.

    Invoked by the Celery task. Opens its own session via ``session_factory``
    (defaulting to ``SessionLocal``) so it is safe in both eager (memory://) and
    worker execution. Persists the run/result lifecycle when ``run_id`` is
    present in the payload.
    """
    db = session_factory()
    run_id = payload.get("run_id")
    service = SyntheticExperimentService(db)
    try:
        if run_id is not None:
            service.mark_running(run_id)

        backtest = SyntheticBacktestService()
        result = backtest.run_for_all(
            db,
            start_at=_parse_dt(payload.get("start_at")),
            end_at=_parse_dt(payload.get("end_at")),
            category=payload.get("category"),
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            slugs=payload.get("slugs"),
            cutoff_hours_before_deadline=_safe_optional_int(
                _first_present(
                    payload.get("cutoff_hours_before_deadline"),
                    payload.get("cutoff_hours"),
                )
            ),
            history_limit=_safe_optional_int(payload.get("history_limit")),
            settle_actions=_normalize_settle_actions(payload.get("settle_actions")),
        )
        if run_id is not None:
            source_context = payload.get("source_sample_gap_candidate")
            service.mark_completed(
                run_id,
                result,
                source_sample_gap_candidate=(
                    source_context if isinstance(source_context, dict) else None
                ),
            )
        return result
    except Exception as exc:
        if run_id is not None:
            service.mark_failed(run_id, str(exc))
        raise
    finally:
        db.close()
