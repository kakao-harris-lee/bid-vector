"""전략 preview 스냅샷: DB 영속 마지막 스캔 + 온디맨드 재계산 (설계 2026-07-30 §6).

GET /operator/strategy/candidates 는 더 이상 요청 경로에서 인라인 ML 을 실행하지
않는다. 마지막 계산 결과(top-100 직렬화 후보 + 스캔 메타)를
``operator_preview_snapshots`` 행에 두고 서빙은 순수 읽기(요청 limit 슬라이스)만
한다. 재계산은 ops 큐 task 로만 실행되며 행 ``status`` 가 DB 단일비행 가드다:
running 이면 스킵, running 이 reconciler 임계(stale_threshold_seconds)를 넘긴
고아면 회수 후 재클레임.

구 preview_cache(#315)의 행동 의도 승계: 스탬피드 방지 = DB 단일비행(프로세스
경계를 넘어 동작 — 구현보다 강한 보장), 단기 TTL = stale 판정 + 자동 디스패치.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.single_user import ensure_operator_strategy_for
from app.core.time import ensure_utc, utc_now
from app.models.models import OperatorPreviewSnapshot, User
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.stale_task_reconciler import stale_threshold_seconds

logger = logging.getLogger(__name__)

SNAPSHOT_STATUS_IDLE = "idle"
SNAPSHOT_STATUS_RUNNING = "running"
SNAPSHOT_STATUS_FAILED = "failed"
#: 스냅샷이 저장하는 직렬화 후보 상한. GET limit 쿼리 상한(le=100)과 같은 값이라
#: 어떤 요청 limit 도 슬라이스만으로 충족된다(설계 §6.1 — limit 은 키 차원이
#: 아님). 100 을 preview_candidates 에 넘기면 스캔 예산은
#: _preview_scan_limit(100)=min(1200, PREVIEW_SCAN_CEILING)=250 으로 고정된다.
SNAPSHOT_CANDIDATE_LIMIT = 100


class PreviewSnapshotService:
    """스냅샷 행의 조회·클레임·라이프사이클·서빙 슬라이스."""

    def __init__(
        self,
        *,
        monitoring_service: StrategyMonitoringService | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self._monitoring = monitoring_service or StrategyMonitoringService()
        self._now = now

    # --- 행 조회/생성 -------------------------------------------------------

    def get_row(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        return (
            db.query(OperatorPreviewSnapshot)
            .filter(
                OperatorPreviewSnapshot.operator_id == int(operator_id),
                OperatorPreviewSnapshot.high_priority_only == bool(high_priority_only),
            )
            .first()
        )

    def _get_or_create_row(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        row = self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)
        if row is not None:
            return row
        row = OperatorPreviewSnapshot(
            operator_id=int(operator_id),
            high_priority_only=bool(high_priority_only),
            status=SNAPSHOT_STATUS_IDLE,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            # 동시 생성 경합은 UNIQUE(operator_id, high_priority_only)가 판정한다.
            db.rollback()
            row = self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)
        return row

    # --- DB 단일비행 --------------------------------------------------------

    def _claim(self, db: Session, row: OperatorPreviewSnapshot) -> bool:
        """행을 running 으로 원자적으로 클레임한다 (설계 §6.2 단일비행 가드).

        UPDATE ... WHERE (status != running OR updated_at <= 회수컷오프) 의
        rowcount 판정이라 여러 프로세스가 동시에 디스패치해도 한쪽만 이긴다.
        회수컷오프는 stale-task-reconciler 와 같은 임계 — 그보다 오래된 running
        은 SIGKILL/재시작 고아라 실제 실행 중일 수 없다.
        """
        reclaim_cutoff = self._now() - timedelta(seconds=stale_threshold_seconds())
        claimed = (
            db.query(OperatorPreviewSnapshot)
            .filter(
                OperatorPreviewSnapshot.id == int(row.id),
                or_(
                    OperatorPreviewSnapshot.status != SNAPSHOT_STATUS_RUNNING,
                    OperatorPreviewSnapshot.updated_at <= reclaim_cutoff,
                ),
            )
            .update(
                {"status": SNAPSHOT_STATUS_RUNNING, "updated_at": self._now()},
                synchronize_session=False,
            )
        )
        db.commit()
        return bool(claimed)

    # --- 디스패치 (API·전략 쓰기 트리거) --------------------------------------

    def dispatch_recompute(
        self, db: Session, *, operator_id: int, high_priority_only: bool
    ) -> OperatorPreviewSnapshot | None:
        """단일비행 가드 하에 재계산 task 를 디스패치한다.

        반환: 클레임에 성공해 디스패치한 행(재조회본). 이미 running 이라
        스킵했으면 None. enqueue 실패는 요청을 죽이지 않고 행을 failed 로
        마감한다(다음 GET 이 재디스패치).
        """
        row = self._get_or_create_row(
            db, operator_id=operator_id, high_priority_only=high_priority_only
        )
        if row is None:  # pragma: no cover - 생성 경합 직후 소실 불가
            return None
        if not self._claim(db, row):
            return None
        try:
            # 순환 import 회피 + 테스트 monkeypatch 표면(§4.7): 호출 시점에
            # app.tasks.jobs 모듈 속성으로 해석한다.
            from app.tasks import jobs

            async_result = jobs.enqueue_preview_snapshot_recompute(
                operator_id=int(operator_id), high_priority_only=bool(high_priority_only)
            )
        except Exception as exc:  # noqa: BLE001 - enqueue 실패가 GET 을 죽여선 안 된다
            logger.exception(
                "preview snapshot enqueue failed operator_id=%s high_priority_only=%s",
                operator_id, high_priority_only,
            )
            self.mark_failed(db, row_id=int(row.id), error=f"enqueue failed: {exc}")
            return None
        # eager(테스트) 모드에선 위 enqueue 가 task 를 인라인 완주시키므로 상태를
        # 덮지 않도록 task_id 만 표적 UPDATE 한다.
        db.query(OperatorPreviewSnapshot).filter(
            OperatorPreviewSnapshot.id == int(row.id)
        ).update({"task_id": str(async_result.id)}, synchronize_session=False)
        db.commit()
        return self.get_row(db, operator_id=operator_id, high_priority_only=high_priority_only)

    def dispatch_for_strategy_write(self, db: Session, *, operator_id: int) -> int:
        """전략 쓰기 후 재계산 트리거 (설계 §6.3, 구 preview_cache.invalidate 대체).

        기존 스냅샷 행이 있는 키만 디스패치한다 — 사용된 적 없는 키 변형의
        불필요한 스캔 방지. 행이 하나도 없으면 기본 키(high_priority_only=False)
        만. 반환: 디스패치 수(running 스킵 제외).
        """
        rows = (
            db.query(OperatorPreviewSnapshot)
            .filter(OperatorPreviewSnapshot.operator_id == int(operator_id))
            .all()
        )
        keys = sorted({bool(row.high_priority_only) for row in rows}) or [False]
        dispatched = 0
        for key in keys:
            if (
                self.dispatch_recompute(
                    db, operator_id=int(operator_id), high_priority_only=key
                )
                is not None
            ):
                dispatched += 1
        return dispatched

    # --- task 라이프사이클 (synthetic experiment run 패턴, DB-first) ---------

    def run_recompute(
        self,
        db: Session,
        *,
        operator_id: int,
        high_priority_only: bool,
        task_id: str | None,
    ) -> dict:
        """task body: mark_running → 스캔(preview_candidates) → mark_completed/failed.

        celery_task_id 멱등(crawl_jobs 패턴): 키당 행이 UNIQUE 라 고아 행 자체가
        생길 수 없고, acks_late 재전달(동일 task id)은 task_id 가 일치하는 자기
        행을 그대로 재사용해 재계산한다.
        """
        row = self._get_or_create_row(
            db, operator_id=operator_id, high_priority_only=high_priority_only
        )
        if row is None:  # pragma: no cover - 생성 경합 직후 소실 불가
            raise ValueError(f"snapshot row unavailable for operator {operator_id}")
        row_id = int(row.id)
        self.mark_running(db, row_id=row_id, task_id=task_id)
        try:
            operator = db.query(User).filter(User.id == int(operator_id)).first()
            if operator is None:
                raise ValueError(f"Operator {int(operator_id)} not found")
            payload = self._monitoring.preview_candidates(
                db,
                limit=SNAPSHOT_CANDIDATE_LIMIT,
                high_priority_only=bool(high_priority_only),
                operator=operator,
            )
            self.mark_completed(db, row_id=row_id, payload=self._json_safe_payload(payload))
        except Exception as exc:
            db.rollback()
            self.mark_failed(db, row_id=row_id, error=str(exc))
            raise
        return {
            "operator_id": int(operator_id),
            "high_priority_only": bool(high_priority_only),
            "snapshot_id": row_id,
            "candidate_count": int(payload.get("returned_candidate_count") or 0),
            "evaluated_project_count": int(payload.get("evaluated_project_count") or 0),
        }

    def mark_running(self, db: Session, *, row_id: int, task_id: str | None) -> None:
        row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
        if row is None:
            return
        row.status = SNAPSHOT_STATUS_RUNNING
        if task_id:
            row.task_id = str(task_id)
        row.updated_at = self._now()
        db.commit()

    def mark_completed(self, db: Session, *, row_id: int, payload: dict) -> None:
        row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
        if row is None:
            return
        row.status = SNAPSHOT_STATUS_IDLE
        row.payload_json = payload
        row.computed_at = self._now()
        row.last_error = None
        db.commit()

    def mark_failed(self, db: Session, *, row_id: int, error: str) -> None:
        """실패 마킹 — run_recompute 의 except 암에서 호출되므로 절대 raise 금지."""
        try:
            row = db.query(OperatorPreviewSnapshot).filter(OperatorPreviewSnapshot.id == int(row_id)).first()
            if row is None:
                return
            row.status = SNAPSHOT_STATUS_FAILED
            row.last_error = str(error)
            db.commit()
        except Exception:  # noqa: BLE001 - 원인 예외를 가리지 않는다
            db.rollback()

    # --- 직렬화/키 해석 ------------------------------------------------------

    def resolve_high_priority_key(
        self, db: Session, *, operator: User, high_priority_only: bool | None
    ) -> bool:
        """요청 파라미터(None=전략 기본값)를 스냅샷 키 불리언으로 해석한다."""
        strategy = ensure_operator_strategy_for(db, operator)
        _, resolved = self._monitoring._resolve_runtime_options(
            strategy, limit=None, high_priority_only=high_priority_only
        )
        return bool(resolved)

    def _json_safe_payload(self, payload: dict) -> dict:
        """payload_json(JSON 컬럼)에 넣을 수 있게 후보 deadline 을 ISO 문자열화.

        후보 dict 에서 datetime 은 deadline 뿐이다(_serialize_candidate). 서빙 시
        OperatorStrategyCandidateItem(pydantic)이 datetime 으로 복원하므로 응답
        형태는 불변이다(구현 계획 이탈 노트 1).
        """
        candidates = [
            {
                **candidate,
                "deadline": candidate["deadline"].isoformat()
                if isinstance(candidate.get("deadline"), datetime)
                else candidate.get("deadline"),
            }
            for candidate in payload.get("candidates") or []
        ]
        return {**payload, "candidates": candidates}

    def _computed_age_exceeds(self, row: OperatorPreviewSnapshot) -> bool:
        """마지막 성공 계산이 stale 기준을 넘겼는가 (응답 stale 플래그의 원천)."""
        if row.computed_at is None:
            return False
        age = self._now() - ensure_utc(row.computed_at)
        return age > timedelta(seconds=int(settings.OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS))

    def _needs_recompute(self, row: OperatorPreviewSnapshot) -> bool:
        """성공 계산이 없거나(최초/실패) stale 이면 재계산 대상이다."""
        return row.computed_at is None or self._computed_age_exceeds(row)
