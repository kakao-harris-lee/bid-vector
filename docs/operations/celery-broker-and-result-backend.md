# Celery 브로커·결과 백엔드 역할 경계

이전에 명시적으로 결정·기록되지 않았던 아키텍처 사실을 확정하는 노트다: **Celery 브로커는 RabbitMQ, 결과 백엔드와 모든 영속 상태는 PostgreSQL이다.** RabbitMQ는 `d04e2f7`(2026-05-12, "Separate ML jobs from API request path")에서 ML 작업을 API 요청 경로 밖으로 빼며 실 worker를 도입할 때 함께 들어왔고, 별도로 결정·문서화된 적이 없어 "RabbitMQ를 backend stream으로 쓰는 것 아니냐"는 오해를 낳았다. 운영자 결정: **RabbitMQ를 유지하고, 아래 역할 경계를 문서로 고정한다.**

## 1. 역할 경계

| 구성요소 | 역할 | 소유 상태 |
| --- | --- | --- |
| RabbitMQ (`amqp://…@rabbitmq:5672/bidvector`) | Celery **broker 전용** — task 메시지 발행/전달 | task 메시지(전달되면 소멸). 영속 상태 없음 |
| PostgreSQL (`db+postgresql+psycopg://…@db:5432/bid_vector_db`) | Celery **result backend** + 모든 도메인 영속 상태 | `celery_taskmeta`·`celery_tasksetmeta`(task 결과·상태), `crawl_jobs`, `operator_strategy_runs`, `operator_preview_snapshots`, `paper_bid_runs` |
| 프로세스 로컬 캐시 | 없음 | #321에서 인메모리 `preview_cache` 제거 → DB 단일비행 스냅샷(`operator_preview_snapshots`)으로 대체 |

결과 백엔드는 `app/core/config.py`가 자동 도출한다. 브로커가 `memory://`가 아니면(`uses_in_memory_celery == False`) `CELERY_RESULT_BACKEND`가 비었거나 기본값(`cache+memory://`)일 때 `DATABASE_URL`을 `db+…` 형식으로 변환해 주입한다(`_to_celery_database_result_backend` + `_compose_database_url`). 따라서 task 결과·상태는 RabbitMQ가 아니라 Postgres에 남는다.

## 2. 왜 RabbitMQ인가

- RabbitMQ는 Celery의 정석 브로커이며, worker-backed 모드에서 `task_acks_late=True` + `worker_cancel_long_running_tasks_on_connection_loss=True`로 **at-least-once 재배달**을 보장한다(`app/tasks/celery_app.py`). Redis 브로커의 visibility-timeout 추정 없이 ack 기반으로 재배달한다.
- 이 저장소는 재배달·고아 task 사고 이력이 있다: scsbid reserve-detail 인라인 fetch가 hard time limit을 넘겨 SIGKILL→고아→0행 재배달 루프가 났고(PR#123에서 defer로 수정), stale-task reconciler(`RECONCILE_STALE_TASK_RUNS`, `build_stale_task_reconciler_beat_schedule`)가 상시 돈다. 강한 배달 보장이 실제로 중요한 환경이다.
- **RabbitMQ를 backend stream으로 쓰지 않는다.** 브로커는 task 전달만 하고, 결과·상태·durable 워크플로 행은 전부 Postgres가 소유한다.

## 3. 개발/테스트 기본값

- 코드 기본값은 `memory://`(broker) + `cache+memory://`(backend)이다(`app/core/config.py`). 이때 Celery는 인메모리 전송에 eager 실행되어 **브로커 컨테이너 없이 인라인**으로 돈다(`uses_in_memory_celery`).
- `docker-compose.yml`의 api 서비스도 이 기본값을 쓴다. worker/beat 등은 broker만 `amqp://…`로 승격하되 backend 기본은 여전히 `cache+memory://`이며, 이 경우 위 자동 도출이 DB backend로 채운다.
- 프로덕션은 이 호스트의 `.env`와 `docker-compose.server.yml`이 broker=`amqp://…`, backend=`db+postgresql+psycopg://…@db:5432/bid_vector_db`로 명시 승격한다. ML 작업 큐 분리 배경은 [../ml-task-separation.md](../ml-task-separation.md) 참고.

## 4. Redis 스탠스

- 현재 스택에 Redis는 **없다**(compose·requirements·런타임 어디에도 없음).
- `.github/copilot-instructions.md`의 "Set up Redis cache"는 초기 스캐폴드의 미착수 to-do이며 아키텍처 의도가 아니다(이 노트로 대체됨).
- inline-ML 설계 스펙의 "스냅샷 Redis 승격"은 명시적으로 **로드맵 별도 단계**로 defer된 미래 옵션이며 현재 아키텍처가 아니다.
- `app/services/smoke_failure_taxonomy.py`의 `"redis"`는 장애 분류 키워드일 뿐 의존성이 아니다.

## 5. 바꾸려면

- 브로커를 Redis 등으로 교체하려면 위 재배달 보장(at-least-once, `acks_late`)이 약해지는 리스크를 먼저 검토한다(visibility-timeout 튜닝 필요, 고아 task 재발 가능).
- `.env`의 `CELERY_BROKER_URL`·`CELERY_RESULT_BACKEND` 기존 키 **값 변경**은 `docker compose restart`가 아니라 `docker compose up -d <service>` 재생성이 필요하다(CLAUDE.md §0 env 우선순위 함정).
