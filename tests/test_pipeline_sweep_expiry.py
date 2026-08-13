"""주기 sweep 메시지에 수명이 붙어 있는가 — 백로그의 구조적 상한.

소비자가 막혀도 beat 는 멈추지 않는다. 2026-08-13 에 inference 워커가 4시간 점유된
동안 beat 는 계속 밀어 넣었고 ``bid_vector_ml_inference`` 에 21,321건이 쌓였다.
관측(P4 큐 깊이)은 그 상태를 *보여줄* 뿐이고, ``expires`` 가 그것을 *막는다*.

이 태스크들은 전부 idempotent sweep 이라 놓친 tick 을 다음 tick 이 대신한다 —
오래된 메시지는 가치가 없으므로 버리는 것이 맞다.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.tasks.pipeline_schedules import (
    INFERENCE_OUTBOX_PROCESS_TASK_NAME,
    NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME,
    SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
    build_pipeline_beat_schedule,
)

SWEEP_ENTRIES = [
    ("inference_outbox_periodic", INFERENCE_OUTBOX_PROCESS_TASK_NAME),
    (
        "similarity_projection_backfill_periodic",
        SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
    ),
    (
        "notification_delivery_outbox_periodic",
        NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME,
    ),
]


@pytest.fixture
def all_sweeps_enabled(monkeypatch):
    for flag in (
        "INFERENCE_OUTBOX_SCHEDULE_ENABLED",
        "SIMILARITY_PROJECTION_BACKFILL_SCHEDULE_ENABLED",
        "NOTIFICATION_DELIVERY_OUTBOX_SCHEDULE_ENABLED",
    ):
        monkeypatch.setattr(settings, flag, True)
    return build_pipeline_beat_schedule()


@pytest.mark.parametrize("entry_name,task_name", SWEEP_ENTRIES)
def test_every_periodic_sweep_publishes_an_expiring_message(
    all_sweeps_enabled, entry_name: str, task_name: str
):
    entry = all_sweeps_enabled[entry_name]

    assert entry["task"] == task_name
    assert entry["options"]["expires"] > 0


@pytest.mark.parametrize("entry_name,_task_name", SWEEP_ENTRIES)
def test_message_lifetime_is_a_multiple_of_its_own_interval(
    all_sweeps_enabled, entry_name: str, _task_name: str
):
    """상한이 주기에 비례해야 한다 — 고정 상수면 주기를 바꿀 때 조용히 어긋난다."""
    entry = all_sweeps_enabled[entry_name]

    assert entry["options"]["expires"] == pytest.approx(
        entry["schedule"] * settings.PERIODIC_SWEEP_EXPIRY_INTERVAL_MULTIPLE
    )


def test_lifetime_outlives_a_single_tick(all_sweeps_enabled):
    """1주기로 두면 워커가 잠깐 바쁘기만 해도 정상 tick 이 버려진다."""
    for entry_name, _ in SWEEP_ENTRIES:
        entry = all_sweeps_enabled[entry_name]
        assert entry["options"]["expires"] > entry["schedule"]


def test_backlog_bound_is_a_few_messages_per_schedule(all_sweeps_enabled):
    """소비자가 멈춰도 스케줄당 미만료 메시지가 상수 개로 묶인다."""
    for entry_name, _ in SWEEP_ENTRIES:
        entry = all_sweeps_enabled[entry_name]
        unexpired = entry["options"]["expires"] / entry["schedule"]
        assert unexpired <= 5, f"{entry_name} 백로그 상한이 느슨하다"
