"""수집 싱글턴 리스가 busy 일 때의 bounded retry 동작.

construction/service 수집은 같은 beat tick 에 dispatch 되고 하나의
``koneps_collection`` advisory lease 를 공유한다. 리스 공유는 의도된 설계다 —
KONEPS 외부 호출은 직렬·throttle 이어야 하고(동시 burst 가 429 를 부른다),
카테고리별 독립 락은 그 직렬성을 깬다.

문제는 리스를 놓친 쪽의 처리였다. 즉시 ``duplicate_suppressed`` 로 끝내면 매
tick 마다 같은 카테고리가 지는 기아가 되므로(운영 7일 실측: service 23회 중
20회 suppressed), 재시도 예산이 남아 있는 동안은 celery countdown 재시도로
넘기고 예산 소진 시에만 기존 suppressed 기록을 남긴다.

이 스위트는 배포 ``.env`` 값에 좌우되면 안 된다(#348 재발 방지). 재시도 창을
읽는 테스트는 전부 ``pinned_retry_settings`` 로 세 설정을 고정하고, 기대값은
유도 함수를 다시 호출하는 대신 리터럴로 선언한다.
"""

import pytest

from app.core.config import Settings, settings
from app.models.models import CrawlJob
from app.schemas.task_payloads import CrawlTaskRequest
from app.tasks.collection_jobs import (
    lease_busy_retry_budget,
    run_singleton_koneps_collection_job,
)

#: analytics 의 ``failure_reason_breakdown`` 이 원문 문자열을 키로 쓰므로, suppressed
#: 사유는 재시도 횟수와 무관하게 이 상수 하나여야 한다(횟수는 metadata 로 나간다).
LEASE_BUSY_MESSAGE = "singleton lock busy; duplicate collection suppressed"

#: ``pinned_retry_settings`` 가 고정하는 입력과 그로부터 나오는 창.
#: ceil(min(hard_limit 1800, 주기 3600) / delay 600) = 3
PINNED_DELAY_SECONDS = 600
PINNED_HARD_LIMIT_SECONDS = 1800
PINNED_INTERVAL_MINUTES = 60
PINNED_BUDGET = (3, 600)


@pytest.fixture
def pinned_retry_settings(monkeypatch):
    """재시도 창을 정하는 세 설정을 고정한다.

    이 값들을 고정하지 않으면 ``KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS=0``
    이나 ``CELERY_TASK_TIME_LIMIT_SECONDS=0`` 을 쓰는 배포에서 스위트가 깨진다.
    """
    monkeypatch.setattr(
        settings,
        "KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS",
        PINNED_DELAY_SECONDS,
    )
    monkeypatch.setattr(
        settings, "CELERY_TASK_TIME_LIMIT_SECONDS", PINNED_HARD_LIMIT_SECONDS
    )
    monkeypatch.setattr(
        settings, "KONEPS_COLLECTION_INTERVAL_MINUTES", PINNED_INTERVAL_MINUTES
    )
    return PINNED_BUDGET


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


def _run(task, *, lease, session_factory=None, run_job=None, crawl_job_id=None):
    calls: list[dict] = []

    def _default_run_job(self_arg, **kwargs):
        calls.append(kwargs)
        return {"job_status": "completed", "collected_count": 0}

    result = run_singleton_koneps_collection_job(
        task,
        request=_request(),
        crawl_job_id=crawl_job_id,
        notify_inference_outbox_committed=lambda ids: None,
        enqueue_deferred_reserve_detail_backfill=lambda notices: 0,
        run_job=run_job or _default_run_job,
        lease_factory=lambda: lease,
        session_factory=session_factory,
    )
    return result, calls


def test_lease_busy_retries_with_configured_countdown_instead_of_suppressing(
    pinned_retry_settings,
):
    """예산이 남아 있으면 suppressed 기록 없이 countdown 재시도로 넘어간다."""
    expected_max_retries, expected_countdown = pinned_retry_settings
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=False)

    def _must_not_open_session():  # pragma: no cover - 호출되면 아래 assert 가 잡는다
        raise AssertionError("retry 경로는 CrawlJob 세션을 열지 않아야 한다")

    with pytest.raises(_RetryRaised) as excinfo:
        _run(task, lease=lease, session_factory=_must_not_open_session)

    assert excinfo.value.countdown == expected_countdown
    assert excinfo.value.max_retries == expected_max_retries
    assert len(task.retry_calls) == 1
    # 리스를 잡지 못했으니 release 는 acquire 실패 경로가 이미 처리한다(중복 해제 금지).
    assert lease.release_calls == 0


def test_lease_busy_records_suppressed_when_retry_budget_is_exhausted(
    pinned_retry_settings, test_db
):
    """재시도 예산을 다 쓰면 기존 duplicate_suppressed 기록 의미가 그대로 보존된다."""
    expected_max_retries, _ = pinned_retry_settings
    task = _FakeTask(retries=expected_max_retries)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"
    assert result["metadata"]["reason"] == "singleton_lock_busy"
    assert result["metadata"]["lease_busy_retries"] == expected_max_retries

    row = test_db.query(CrawlJob).filter(CrawlJob.id == result["metadata"]["crawl_job_id"]).one()
    assert row.status == "duplicate_suppressed"
    assert row.result_count == 0


@pytest.mark.parametrize("retries", [0, 1, 3])
def test_suppressed_error_message_is_constant_across_retry_counts(
    pinned_retry_settings, test_db, retries
):
    """analytics 의 failure_reason_breakdown 이 원문을 키로 쓰므로 사유는 상수여야 한다.

    재시도 횟수를 접미사로 붙이면 같은 원인이 횟수별 버킷으로 분절된다.
    """
    task = _FakeTask(retries=retries, is_eager=True)  # eager = 재시도 없이 즉시 기록
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    row = test_db.query(CrawlJob).filter(CrawlJob.id == result["metadata"]["crawl_job_id"]).one()
    assert row.error_message == LEASE_BUSY_MESSAGE
    # 횟수는 사라지지 않고 metadata 로 관찰된다.
    assert result["metadata"]["lease_busy_retries"] == retries


def test_lease_busy_suppresses_without_retry_in_eager_mode(pinned_retry_settings, test_db):
    """eager(=memory:// broker) 에서는 재시도가 인라인 재실행이 되므로 즉시 기록한다."""
    task = _FakeTask(retries=0, is_eager=True)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"
    assert result["metadata"]["lease_busy_retries"] == 0


def test_lease_busy_suppresses_without_retry_when_called_directly(
    pinned_retry_settings, test_db
):
    """워커 밖 직접 호출(스크립트·테스트)은 재배달 경로가 없으므로 기존 동작 유지."""
    task = _FakeTask(retries=0, called_directly=True)
    lease = _FakeLease(acquired=False)

    result, _ = _run(task, lease=lease, session_factory=lambda: test_db)

    assert task.retry_calls == []
    assert result["job_status"] == "duplicate_suppressed"


def test_lease_busy_does_not_retry_when_a_crawl_job_row_already_exists(
    pinned_retry_settings, test_db
):
    """API 비동기 경로(``crawl_job_id`` 지정)는 기다리지 않고 즉시 답한다.

    그 경로는 ``queued`` 행을 먼저 만들어 두므로, 재시도 창(최대 1800s)만큼 기다리면
    행의 나이가 stale reconciler 임계(hard limit + grace = 2100s)를 넘어 살아있는
    작업이 ``failed [reconciled]`` 로 오판 마감된다. beat 는 ``crawl_job_id`` 를
    넘기지 않으므로 기아 해소 목적은 그대로다.
    """
    task = _FakeTask(retries=0)
    lease = _FakeLease(acquired=False)

    result, _ = _run(
        task, lease=lease, session_factory=lambda: test_db, crawl_job_id=4242
    )

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


def test_shipped_defaults_produce_the_declared_retry_window(monkeypatch):
    """배포 기본값이 실제로 (3회 x 600s) 창을 만드는지 — 기본값 변경 회귀 가드.

    ``settings`` 인스턴스가 아니라 ``Settings`` 선언 기본값을 넣어 확인하므로 이
    호스트의 ``.env`` 와 무관하다.
    """
    fields = Settings.model_fields
    for name in (
        "KONEPS_COLLECTION_LEASE_BUSY_RETRY_DELAY_SECONDS",
        "CELERY_TASK_TIME_LIMIT_SECONDS",
        "KONEPS_COLLECTION_INTERVAL_MINUTES",
    ):
        monkeypatch.setattr(settings, name, fields[name].default)

    assert lease_busy_retry_budget() == PINNED_BUDGET
