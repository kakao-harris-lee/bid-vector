"""Reconcile orphaned non-terminal task-run rows into a terminal failed state.

This is the durable backstop for the operations dashboard
``task_stale_queue: critical`` KPI. The in-task finalize-on-failure path
(``StrategyMonitoringService._mark_run_failed`` /
``KonepsCollectorService.mark_crawl_job_failed``) closes a run out on ordinary
exceptions and soft time limits, but it cannot cover the cases where no Python
cleanup runs at all:

* hard time limit -> SIGKILL of the worker child,
* ``docker compose restart`` / OOM-kill of the worker,
* the database being unreachable at the moment the in-task finalize fires.

In those cases an ``operator_strategy_runs`` / ``crawl_jobs`` row is left in a
non-terminal (``running`` / ``queued``) state forever, and the analytics
reporting stale-task detector (>= 15 min non-terminal) flips the operations KPI
to ``critical`` permanently.

This reconciler periodically flips any non-terminal row that is older than the
Celery hard time limit (plus a safety grace) to ``failed`` with a
``[reconciled]`` marker so the row can never have been a genuinely live task.
It deliberately:

* never deletes the row or any of its partial results (status-only transition),
* never touches rows that are already terminal (completed / failed / cancelled),
* never touches rows that are non-terminal but still inside the live window.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import utc_now
from app.models.models import CrawlJob, OperatorPreviewSnapshot, OperatorStrategyRun

#: Non-terminal statuses that a periodic janitor may finalize once stale.
NON_TERMINAL_STATUSES = ("running", "queued", "pending", "started")

#: Marker prefix written into ``error_message`` for reconciled rows so an
#: operator can tell a janitor-finalized row apart from an in-task failure.
RECONCILED_MARKER = "[reconciled]"


def stale_threshold_seconds() -> int:
    """비종단 task 행을 고아로 판정하는 나이(초): hard limit + grace, 60s floor.

    reconciler 와 preview 스냅샷의 단일비행 회수(stale-running reclaim)가 같은
    임계를 공유한다(설계 §6.2 — "running 이 stale-task-reconciler 임계 초과면
    회수"). 단일 출처(§4.5).
    """
    hard_limit = max(0, int(settings.CELERY_TASK_TIME_LIMIT_SECONDS))
    grace = max(0, int(settings.STALE_TASK_RECONCILER_GRACE_SECONDS))
    # Always require at least a minute so a misconfigured 0 hard-limit cannot
    # reconcile rows that were created seconds ago.
    return max(60, hard_limit + grace)


class StaleTaskReconcilerService:
    """Finalize orphaned non-terminal strategy-run / crawl-job rows as failed."""

    def reconcile(self, db: Session) -> dict:
        """Flip every stale non-terminal task-run row to ``failed``.

        A row is *stale* when its effective start time is older than the Celery
        hard time limit plus the configured grace margin: no real task can still
        be running past that bound, so a non-terminal row there is an orphan left
        by a hard-kill / worker-restart / DB-down event.
        """
        threshold_seconds = self._stale_threshold_seconds()
        cutoff = utc_now() - timedelta(seconds=threshold_seconds)
        batch_limit = max(1, int(settings.STALE_TASK_RECONCILER_BATCH_LIMIT))
        reason = f"{RECONCILED_MARKER} non-terminal task run exceeded {threshold_seconds}s; finalized by reconciler"

        strategy_finalized = self._reconcile_strategy_runs(
            db, cutoff=cutoff, batch_limit=batch_limit, reason=reason
        )
        crawl_finalized = self._reconcile_crawl_jobs(
            db, cutoff=cutoff, batch_limit=batch_limit, reason=reason
        )
        snapshot_finalized = self._reconcile_preview_snapshots(
            db, cutoff=cutoff, batch_limit=batch_limit, reason=reason
        )

        if strategy_finalized or crawl_finalized or snapshot_finalized:
            db.commit()
        else:
            # Nothing changed; keep the session clean for the caller.
            db.rollback()

        return {
            "threshold_seconds": threshold_seconds,
            "strategy_runs_finalized": strategy_finalized,
            "crawl_jobs_finalized": crawl_finalized,
            "preview_snapshots_finalized": snapshot_finalized,
            "total_finalized": strategy_finalized + crawl_finalized + snapshot_finalized,
        }

    def _stale_threshold_seconds(self) -> int:
        """Resolve how old a non-terminal row must be before it is reconciled."""
        return stale_threshold_seconds()

    def _reconcile_strategy_runs(
        self,
        db: Session,
        *,
        cutoff,
        batch_limit: int,
        reason: str,
    ) -> int:
        """Finalize stale non-terminal operator strategy runs. Returns the count."""
        rows = (
            db.query(OperatorStrategyRun)
            .filter(OperatorStrategyRun.status.in_(NON_TERMINAL_STATUSES))
            .filter(
                or_(
                    # started_at is the truest "task began" timestamp; fall back to
                    # created_at for queued rows that never transitioned to running.
                    OperatorStrategyRun.started_at <= cutoff,
                    OperatorStrategyRun.started_at.is_(None),
                )
            )
            .filter(OperatorStrategyRun.created_at <= cutoff)
            .order_by(OperatorStrategyRun.id.asc())
            .limit(batch_limit)
            .all()
        )
        finalized = 0
        for row in rows:
            row.status = "failed"
            row.error_message = self._compose_reason(row.error_message, reason)
            row.completed_at = utc_now()
            finalized += 1
        return finalized

    def _reconcile_crawl_jobs(
        self,
        db: Session,
        *,
        cutoff,
        batch_limit: int,
        reason: str,
    ) -> int:
        """Finalize stale non-terminal crawl jobs. Returns the count.

        ``CrawlJob`` has no ``started_at`` column, so ``created_at`` is the only
        available age signal -- which is correct here because the job row is
        created with ``status="running"`` at the moment work begins.
        """
        rows = (
            db.query(CrawlJob)
            .filter(CrawlJob.status.in_(NON_TERMINAL_STATUSES))
            .filter(CrawlJob.created_at <= cutoff)
            .order_by(CrawlJob.id.asc())
            .limit(batch_limit)
            .all()
        )
        finalized = 0
        for row in rows:
            row.status = "failed"
            row.error_message = self._compose_reason(row.error_message, reason)
            row.completed_at = utc_now()
            finalized += 1
        return finalized

    def _reconcile_preview_snapshots(
        self,
        db: Session,
        *,
        cutoff,
        batch_limit: int,
        reason: str,
    ) -> int:
        """running 고아 preview 스냅샷 행을 failed 로 마감한다. Returns the count.

        키당 UNIQUE 행이라 삭제는 없다(payload·computed_at 보존). ``updated_at`` 이
        유일한 age 신호다: 클레임/mark_running 이 매번 갱신하므로 cutoff 보다
        오래된 running 은 실제 실행 중일 수 없다.

        이건 preview 의 backstop 일 뿐이다 — 대개 훨씬 짧은 회수창
        (``OPERATOR_PREVIEW_SNAPSHOT_RUNNING_RECLAIM_SECONDS``, 기본 300s)에서
        GET 자동 디스패치나 명시 갱신이 먼저 running 고아를 회수한다. 여기서
        failed 로 마감된 행은 실패 쿨다운(onupdate 로 갱신된 ``updated_at`` 기준)에
        걸려 바로 다음 GET 은 재디스패치하지 않고, 쿨다운 경과 후 GET 또는 명시
        갱신이 재계산한다. 이전 성공 계산이 있던 행은 그 payload 를 stale 로 계속
        서빙하고, 계산된 적 없는 고아는 빈 후보 + ``stale=false``(부트스트랩)로
        서빙된다.
        """
        rows = (
            db.query(OperatorPreviewSnapshot)
            .filter(OperatorPreviewSnapshot.status.in_(NON_TERMINAL_STATUSES))
            .filter(OperatorPreviewSnapshot.updated_at <= cutoff)
            .order_by(OperatorPreviewSnapshot.id.asc())
            .limit(batch_limit)
            .all()
        )
        finalized = 0
        for row in rows:
            row.status = "failed"
            row.last_error = self._compose_reason(row.last_error, reason)
            finalized += 1
        return finalized

    def _compose_reason(self, existing: str | None, reason: str) -> str:
        """Preserve any existing error context while prepending the reconcile marker."""
        existing = (existing or "").strip()
        if not existing:
            return reason
        if RECONCILED_MARKER in existing:
            return existing
        return f"{reason} (prior: {existing})"
