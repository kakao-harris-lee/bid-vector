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
# 스펙 §5 PR-A-4 가 명시한 4개 서비스 (beat 는 ML 미실행이라 제외).
TUNED_SERVICES = ("api", "worker", "ml-worker", "training-worker")


def test_malloc_arena_and_omp_threads_declared_for_all_ml_services():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    for service_name in TUNED_SERVICES:
        environment = compose["services"][service_name]["environment"]
        assert str(environment.get("MALLOC_ARENA_MAX")) == "2", service_name
        assert str(environment.get("OMP_NUM_THREADS")) == "1", service_name
