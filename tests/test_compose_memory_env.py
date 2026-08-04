"""docker-compose.yml 스레드/아레나 선언 가드 (설계 2026-07-30 §5 PR-A-4 / S1).

glibc malloc 은 스레드마다 아레나를 늘리고 OS 로 잘 반환하지 않는다 — anyio
40-스레드 풀 x torch 추론이 api RSS 단조 증가(1→8.4GiB)의 유력 주범(S1)이었다.
MALLOC_ARENA_MAX=2 / OMP_NUM_THREADS=1 은 코드가 아니라 compose env 로
선언한다(§4.5 선언적 구성). 이 테스트는 선언 누락/오타 회귀를 막는다.
"""
from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"
# 스펙 §5 PR-A-4 가 명시한 ML 실행 서비스 (beat 는 ML 미실행이라 제외).
TUNED_SERVICES = ("api", "worker", "inference-worker", "ml-worker", "training-worker")


def test_malloc_arena_and_omp_threads_declared_for_all_ml_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    for service_name in TUNED_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert str(environment.get("MALLOC_ARENA_MAX")) == "2", service_name
        assert str(environment.get("OMP_NUM_THREADS")) == "1", service_name


# 스펙 §6.4: #317(api 8g)과 동일한 컨테이너 스코프 격리의 완성. worker 는 상주
# ~4.8GiB 관측 대비 여유 10g, ML 워커 2종은 8g(자식 3GiB x 동시성 2 = 6GiB +
# 부모/오버헤드 여유). 값은 배포 후 관측으로 조정하되 아래 산술 불변식은 지킨다.
DECLARED_MEM_LIMITS = {
    "api": "8g",
    "worker": "10g",
    "inference-worker": "8g",
    "ml-worker": "8g",
    "training-worker": "8g",
}
# celery 워커를 돌리는 서비스만 자식 리사이클러 산술의 적용 대상이다(api 제외).
CELERY_WORKER_SERVICES = ("worker", "inference-worker", "ml-worker", "training-worker")
_MEM_LIMIT_UNIT_BYTES = {"k": 1024, "m": 1024**2, "g": 1024**3}


def _mem_limit_bytes(value: str) -> int:
    """compose mem_limit 문자열("8g")을 바이트로 해석한다."""
    text = str(value).strip().lower().rstrip("b")
    return int(text[:-1]) * _MEM_LIMIT_UNIT_BYTES[text[-1]]


def test_mem_limits_declared_for_all_ml_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    for service_name, expected in DECLARED_MEM_LIMITS.items():
        assert str(compose["services"][service_name].get("mem_limit")) == expected, service_name


def test_inference_worker_listens_only_to_inference_queue():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    command = str(compose["services"]["inference-worker"]["command"])

    assert "${CELERY_ML_INFERENCE_QUEUE:-bid_vector_ml_inference}" in command
    assert "${CELERY_OPS_QUEUE" not in command
    assert "${CELERY_ML_BACKFILL_QUEUE" not in command
    assert "${CELERY_ML_REEVALUATION_QUEUE" not in command
    assert "${CELERY_ML_TRAINING_QUEUE" not in command
    assert "--concurrency=${CELERY_INFERENCE_WORKER_CONCURRENCY:-1}" in command


def test_mem_limits_leave_headroom_above_child_recycler_budget():
    """컨테이너 상한 > 자식 RSS 상한 x 동시성 (§6.4 이중 가드의 순서 보장).

    두 가드는 순서가 있어야 의미가 있다: 자식이 상한을 넘으면 celery 가 task
    경계에서 **정상 재생성**하고, 컨테이너 OOM 킬러는 그 뒤의 최후 방어선이다.
    mem_limit 이 (자식 상한 x 동시성) 이하이면 순서가 뒤집혀 OOM 킬러가 먼저
    이기고 #317 의 SIGKILL 회귀가 워커에서 재현된다.
    """
    from app.core.config import settings

    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    child_budget_bytes = (
        int(settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB)
        * 1024
        * int(settings.CELERY_WORKER_CONCURRENCY)
    )
    for service_name in CELERY_WORKER_SERVICES:
        limit_bytes = _mem_limit_bytes(compose["services"][service_name]["mem_limit"])
        assert limit_bytes > child_budget_bytes, service_name
