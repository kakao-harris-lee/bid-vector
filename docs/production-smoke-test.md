# Production Smoke Test

운영 배포 후 README 전체를 훑지 않고 아래 순서만 실행한다. 기본 smoke는 crawl/monitor 쓰기 작업을 실행하지 않는다. `--write`를 붙인 경우에만 KONEPS crawl과 strategy monitor가 운영 DB에 기록을 남기며 Telegram 알림이 나갈 수 있다.

G-0 운영 검증의 완료 기준은 scheduled smoke 7회 연속 green이다. 운영 대시보드의 `smoke_test.current_streak`가 `7` 이상이고, 최신 scheduled run의 phase evidence에서 KONEPS 수집, 후보 생성, Telegram 알림 또는 명시적 skip reason을 확인할 수 있어야 한다.

G-2의 operator별 증적 축적은 이 문서의 G-0 smoke 절차를 선행 신호로만 사용하고, 실제 반복 실행 순서는 [G-2 Evidence Runbook](operations/g2-evidence-runbook.md)을 따른다. G-2 판정에서는 `operator_id`, `current_operator_id`, `operator_scope`, `source_run_type`이 섞이지 않았는지 별도로 확인한다.

## 1. 사전 확인

운영 `.env`에 최소한 아래 값이 있어야 한다.

- `DATABASE_URL` 또는 `DATABASE_*`
- `JWT_SECRET_KEY`
- `KONEPS_OPENAPI_SERVICE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- webhook을 쓰는 경우 `TELEGRAM_WEBHOOK_SECRET`
- webhook을 쓰지 않고 버튼/명령을 polling으로 처리하는 경우 `TELEGRAM_POLLING_SCHEDULE_ENABLED=true`

Telegram 수신 계정은 봇과 대화를 한 번 시작해야 한다.

## 2. 서버 기동

기본 로컬 포트는 다음처럼 구분한다.

| Surface | URL | 용도 |
|---|---|---|
| Bid-vector app/API | `http://localhost:3000` | `/health`, `/docs`, `/api/v1/*`, smoke test 대상 |
| Built user dashboard route | `http://localhost:3000/dashboard` | 빌드된 사용자 SPA를 FastAPI가 정적으로 서빙할 때 사용 |
| Built admin route | `http://localhost:3000/admin` | 빌드된 관리자 SPA를 FastAPI가 정적으로 서빙할 때 사용 |
| Frontend dev server (user) | `http://localhost:3001/dashboard` | `npm --prefix frontend run dev`; `/api`는 app/API로 프록시 |
| Frontend dev server (admin) | `http://localhost:3001/admin` | `npm --prefix frontend run dev:admin`; `/api`는 app/API로 프록시 |

```bash
make docker-up-server
```

또는:

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml up -d --build
```

## 3. 읽기 전용 smoke

```bash
python scripts/production_smoke_test.py \
  --base-url https://<your-api-host> \
  --evidence-out smoke-read.json
```

확인 항목:

- `/health`
- operator profile/strategy
- `GET /api/v1/operator/dashboard` 계약
- `GET /api/v1/analytics/operations-dashboard`
- Telegram status
- strategy candidate preview

이 단계는 KONEPS crawl과 strategy monitor를 실행하지 않는다. 단, 완전히 비어 있는 DB에서는 기존 API 동작에 따라 기본 operator/profile/strategy가 생성될 수 있으므로 production bootstrap은 배포 전에 끝내둔다.

## 4. 실제 연동 smoke

```bash
python scripts/production_smoke_test.py \
  --base-url https://<your-api-host> \
  --write \
  --max-items 3 \
  --monitor-limit 3 \
  --evidence-out smoke-write.json
```

`--write` 실행 항목:

- KONEPS OpenAPI crawl: `POST /api/v1/operations/crawl`
- strategy monitor kickoff: `POST /api/v1/operator/strategy/monitor` — 동기 실행이 아니라 202 async envelope(`task_id`/`monitor_run_id`/`poll_url`)을 반환한다
- strategy monitor 결과 폴링: kickoff의 `poll_url`(`GET /api/v1/operator/strategy/monitor/tasks/{task_id}`)을 terminal 상태까지 폴링해 `result`를 읽는다. `completed`가 아닌 terminal 상태는 실패로 처리한다. 폴링 횟수·간격은 `--monitor-poll-attempts`/`--monitor-poll-interval-seconds`로 조정한다
- monitor run detail 확인

기본값은 `source=koneps-openapi`, `category=general-service`, `execution_mode=auto`, `high_priority_only=true`이다.

후보가 없어서 Telegram 알림이 나가지 않을 수 있다. 후보 탐색 범위를 넓히려면:

```bash
python scripts/production_smoke_test.py \
  --base-url https://<your-api-host> \
  --write \
  --monitor-all-candidates \
  --max-items 5 \
  --monitor-limit 5 \
  --evidence-out smoke-write-wide.json
```

수동 smoke의 `--evidence-out` JSON은 실패한 step마다 `failure_category`, `action_required`, `retry_method`를 남긴다. strategy monitor가 성공했지만 알림이 0건이면 `skip_reason`에 `no strategy candidates selected`, `selected candidates already persisted or skipped`, `no new notification created` 중 하나가 기록된다.

## 5. Telegram 수동 확인

실제 봇 대화에서 아래를 확인한다.

```text
/strategy
/strategy_set categories=software regions=서울 keywords=AI limit=3
/strategy_clear categories keywords
```

`/strategy` 응답의 버튼도 확인한다.

- `업종`
- `지역`
- `키워드`
- `예산`
- `임계치`
- `알림 범위`
- `후보 수`

버튼 플로우는 `필드 선택 → 새 값 입력 → 적용/취소` 순서다. 잘못된 입력은 기존 전략을 변경하지 않아야 한다.

## 6. Scheduled smoke evidence

Celery beat의 `smoke_test_daily`는 `SmokeTestRun.phases`에 다음 phase를 저장한다.

| Phase | Green 조건 | Dashboard evidence |
|---|---|---|
| `koneps_collect` | KONEPS OpenAPI live 수집이 1건 이상 | `collected_count` |
| `sbert_embedding` | 최근 수집 project가 fallback embedding이 아님 | `project_id`, `project_title` |
| `predict_price` | 예측 입찰률이 guardrail 범위 `0.7 <= rate <= 1.0` | `project_id`, `predicted_bid_rate`, `predictor_name` |
| `candidate_generation` | strategy monitor가 완료됨 | `monitor_run_id`, `evaluated_project_count`, `selected_candidate_count`, `persisted_candidate_count`, `notification_count`, `skip_reason` |
| `telegram_ping` | Telegram smoke 메시지 전송 성공 | `telegram_status`, `telegram_message_id` |

`candidate_generation`은 후보나 알림이 0건이어도 phase 자체는 green일 수 있다. 이 경우 최신 phase의 `skip_reason`이 운영 판단 근거다. skip reason이 없거나 monitor가 exception을 내면 `failure_category=candidate_generation`으로 본다.

Operations dashboard 확인 경로:

```bash
curl https://<your-api-host>/api/v1/analytics/operations-dashboard
```

확인할 필드:

- `smoke_test.current_streak`: G-0 7회 연속 green 진행도
- `smoke_test.latest.phases[].evidence`: 최신 scheduled run의 compact evidence
- `smoke_test.latest.phases[].failure_category`: 실패 phase의 원인 분류
- `smoke_test.latest.phases[].action_required`: 사람이 할 조치
- `smoke_test.latest.phases[].retry_method`: 재실행 방법
- `smoke_test.recent_failures[].phase_details`: 최근 실패 run별 phase detail

## 7. 실패 위치 구분과 retry

| `failure_category` | 의미 | 먼저 볼 곳 | Retry method |
|---|---|---|---|
| `credential` | KONEPS/Telegram/API 인증값 누락 또는 거부 | 운영 `.env`, secret store, HTTP 401/403 | credential 수정 후 scheduled smoke task 또는 `python scripts/production_smoke_test.py --write` 재실행 |
| `koneps_response` | KONEPS OpenAPI 장애, timeout, 응답 형식 문제 | crawl log, KONEPS status, `KONEPS_OPENAPI_SERVICE_KEY` | KONEPS 정상화 후 `python scripts/production_smoke_test.py --write --max-items 3` |
| `candidate_generation` | strategy monitor 실행 실패 | `monitor_run_id`, `/api/v1/operator/strategy/monitor/runs/{id}`, app log | `python scripts/production_smoke_test.py --write --monitor-all-candidates` 또는 monitor endpoint 재실행 |
| `no_candidate` | 실행은 됐지만 후보/최근 project가 없음 | latest phase `skip_reason`, strategy filters, active project count | 필터를 넓힌 뒤 재실행. 의도된 skip이면 코드 retry 불필요 |
| `telegram` | Telegram 설정 또는 전송 실패 | Telegram status endpoint, bot chat, notification log | 설정 수정 후 `python scripts/production_smoke_test.py --write --telegram-sync` |
| `task_broker` | Celery broker/backend/worker 문제 | operations dashboard task 카드, worker log | broker/worker 복구 후 scheduled smoke task 재실행 |
| `db_schema` | migration/schema 불일치 | API/worker log, Alembic revision | migration 적용 및 재시작 후 scheduled smoke task 재실행 |
| `prediction` | embedding 또는 price prediction 문제 | `project_id`, model/runtime log | model/data 복구 후 같은 smoke 재실행 |
| `unknown` | 위 분류에 걸리지 않는 예외 | phase detail, app log | root cause 수정 후 같은 command 재실행 |

- API 기동 문제: `/health` 실패
- DB/schema 문제: profile, strategy, dashboard 실패
- KONEPS credential 문제: crawl 단계에서 `KONEPS_OPENAPI_SERVICE_KEY` 또는 외부 API 오류
- 후보/전략 문제: candidate preview 또는 monitor에서 `returned=0`, `persisted=0`
- Telegram 문제: `telegram status` 또는 operations dashboard의 Telegram 카드가 `watch`/`critical`
- task/broker 문제: operations dashboard의 task 카드가 `watch`/`critical`

## 8. 자주 쓰는 옵션

```bash
# 로컬 서버
python scripts/production_smoke_test.py --base-url http://localhost:3000

# 운영 API가 프록시에서 Bearer token을 요구하는 경우
python scripts/production_smoke_test.py \
  --base-url https://<your-api-host> \
  --bearer-token "$TOKEN"

# Telegram polling까지 한 번 당겨서 확인
python scripts/production_smoke_test.py \
  --base-url https://<your-api-host> \
  --write \
  --telegram-sync

# 환경 변수로 실행
SMOKE_BASE_URL=https://<your-api-host> \
SMOKE_WRITE=true \
SMOKE_EVIDENCE_OUT=smoke-write.json \
python scripts/production_smoke_test.py
```
