"""수집 싱글턴 리스가 busy 일 때의 bounded retry 동작.

construction/service 수집은 같은 beat tick 에 dispatch 되고 하나의
``koneps_collection`` advisory lease 를 공유한다. 리스 공유는 의도된 설계다 —
KONEPS 외부 호출은 직렬·throttle 이어야 하고(동시 burst 가 429 를 부른다),
카테고리별 독립 락은 그 직렬성을 깬다.

문제는 리스를 놓친 쪽의 처리였다. 즉시 ``duplicate_suppressed`` 로 끝내면 매
tick 마다 같은 카테고리가 지는 기아가 되므로(운영 7일 실측: service 23회 중
20회 suppressed), 재시도 예산이 남아 있는 동안은 celery countdown 재시도로
넘기고 예산 소진 시에만 기존 suppressed 기록을 남긴다.
"""

import pytest

from app.core.config import settings
from app.models.models import CrawlJob
from app.schemas.task_payloads import CrawlTaskRequest
from app.tasks.collection_jobs import (
    lease_busy_retry_budget,
    run_singleton_koneps_collection_job,
)


class _FakeLease:
    """acquire 결과를 주입하는 리스 대역(§4.7 seam)."""

    def __init__(self, acquired: bool) -> None:
        self._acquired = acquired
        self.acquire_calls = 0
        self.release_calls = 0

    def acquire(self) -> bool:
        self.acquire_calls += 1
        return self._acquired

    def release(self) -> None:
        self.release_calls += 1


class _RetryRaised(Exception):
    """celery ``Task.retry`` 가 던지는 ``Retry`` 자리의 센티널."""

    def __init__(self, countdown, max_retries) -> None:
        super().__init__("retry")
        self.countdown = countdown
        self.max_retries = max_retries


class _FakeRequestContext:
    def __init__(self, *, retries: int, is_eager: bool = False, called_directly: bool = False) -> None:
        self.id = "task-id-1"
        self.retries = retries
        self.is_eager = is_eager
        self.called_directly = called_directly


class _FakeTask:
    """``bind=True`` task 의 ``self`` 대역."""

    def __init__(self, *, retries: int = 0, is_eager: bool = False, called_directly: bool = False) -> None:
        self.request = _FakeRequestContext(
            retries=retries, is_eager=is_eager, called_directly=called_directly
        )
        self.retry_calls: list[tuple] = []

    def retry(self, *, countdown=None, max_retries=None):
        self.retry_calls.append((countdown, max_retries))
        raise _RetryRaised(countdown, max_retries)


def _request() -> CrawlTaskRequest:
    return CrawlTaskRequest.model_validate(
        {
            "source": "koneps-openapi",
            "category": "service",
            "execution_mode": "auto",
            "max_items": 500,
        }
    )


def _run(task, *, lease, session_factory=None, run_job=None):
    calls: list[dict] = []

    def _default_run_job(self_arg, **kwargs):
        calls.append(kwargs)
        return {"job_status": "completed", "collected_count": 0}

    result = run_singleton_koneps_collection_job(
        task,
        request=_request(),
        crawl_job_id=None,
        notify_inference_outbox_committed=lambda ids: None,
        enqueue_deferred_reserve_detail_backfill=lambda notices: 0,
        run_job=run_job or _default_run_job,
        lease_factory=lambda: lease,
        session_factory=session_factory,
    )
    return result, calls


def test_lease_busy_retries_with_configured_countdown_instead_of_suppressing():
    """예산이 남아 있으면 suppressed 기록 없이 countdown 재시도로 넘어간다."""
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=False)
    max_retries, countdown = lease_busy_retry_budget()

    def _must_not_open_session():  # pragma: no cover - 호출되면 아래 assert 가 잡는다
        raise AssertionError("retry 경로는 CrawlJob 세션을 열지 않아야 한다")

    with pytest.raises(_RetryRaised) as excinfo:
        _run(task, lease=lease, session_factory=_must_not_open_session)

    assert excinfo.value.countdown == countdown
    assert excinfo.value.max_retries == max_retries
    assert len(task.retry_calls) == 1
    # 리스를 잡지 못했으니 release 는 acquire 실패 경로가 이미 처리한다(중복 해제 금지).
    assert lease.release_calls == 0


def test_lease_busy_records_suppressed_when_retry_budget_is_exhausted(test_db):
    """재시도 예산을 다 쓰면 기존 duplicate_suppressed 기록 의미가 그대로 보존된다."""
    max_retries, _ = lease_busy_retry_budget()
    task = _FakeTask(retries=max_retries)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"
    assert result["metadata"]["reason"] == "singleton_lock_busy"
    assert result["metadata"]["lease_busy_retries"] == max_retries

    row = test_db.query(CrawlJob).filter(CrawlJob.id == result["metadata"]["crawl_job_id"]).one()
    assert row.status == "duplicate_suppressed"
    assert row.result_count == 0
    assert "singleton lock busy" in str(row.error_message)


def test_lease_busy_suppresses_without_retry_in_eager_mode(test_db):
    """eager(=memory:// broker) 에서는 재시도가 인라인 재실행이 되므로 즉시 기록한다."""
    task = _FakeTask(retries=0, is_eager=True)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"
    assert result["metadata"]["lease_busy_retries"] == 0


def test_lease_busy_suppresses_without_retry_when_called_directly(test_db):
    """워커 밖 직접 호출(스크립트·테스트)은 재배달 경로가 없으므로 기존 동작 유지."""
    task = _FakeTask(retries=0, called_directly=True)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"


def test_zero_delay_restores_immediate_suppression(monkeypatch, test_db):
    """지연 0 = 재시도 끄기 스위치 — 예전 즉시 suppressed 동작으로 되돌아간다."""
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS", 0)
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"


def test_lease_acquired_runs_job_and_releases_lease():
    """정상 경로: 리스를 잡으면 수집 본문을 실행하고 반드시 해제한다."""
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=True)

    result, calls = _run(task, lease=lease)

    assert result["job_status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["crawl_job_id"] is None
    assert lease.release_calls == 1


def test_lease_is_released_when_collection_body_raises():
    """본문 예외에도 리스는 해제된다(다음 tick 이 영구히 굶지 않도록)."""
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=True)

    def _boom(self_arg, **kwargs):
        raise RuntimeError("collection failed")

    with pytest.raises(RuntimeError, match="collection failed"):
        _run(task, lease=lease, run_job=_boom)

    assert lease.release_calls == 1


@pytest.mark.parametrize(
    "delay,hard_limit,interval_minutes,expected",
    [
        # 운영 기본값: 리스는 길어야 30분(hard limit) 잡히고 주기는 60분 → 3회 x 10분.
        (600, 1800, 60, (3, 600)),
        # 지연이 창보다 길면 1회로 충분하다(과다 재시도 금지).
        (1800, 1800, 60, (1, 1800)),
        # 수집 주기가 hard limit 보다 짧으면 주기가 창을 정한다(다음 tick 과 겹침 방지).
        (600, 1800, 15, (2, 600)),
        # 지연 0 이하 = 재시도 없음.
        (0, 1800, 60, (0, 0)),
        (-1, 1800, 60, (0, 0)),
        # hard limit 을 끈 배포(0)는 리스 점유 상한이 없으므로 대기 근거도 없다.
        (600, 0, 60, (0, 600)),
    ],
)
def test_retry_window_is_derived_from_lease_hold_and_cycle(
    monkeypatch, delay, hard_limit, interval_minutes, expected
):
    """재시도 창은 튜닝값이 아니라 (리스 최대 점유 시간, 수집 주기) 유도값이다."""
    monkeypatch.setattr(
        settings, "KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS", delay
    )
    monkeypatch.setattr(settings, "CELERY_TASK_TIME_LIMIT_SECONDS", hard_limit)
    monkeypatch.setattr(settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", interval_minutes)

    assert lease_busy_retry_budget() == expected


def test_default_retry_window_covers_the_maximum_lease_hold():
    """기본 설정에서 재시도 창이 리스 최대 점유 시간을 덮는지 확인한다."""
    max_retries, countdown = lease_busy_retry_budget()
    window = min(
        settings.CELERY_TASK_TIME_LIMIT_SECONDS,
        settings.KONEPS_COLLECTION_INTERVAL_MINUTES * 60,
    )

    assert max_retries >= 1
    assert max_retries * countdown >= window
