# Production Smoke Test

운영 배포 후 README 전체를 훑지 않고 아래 순서만 실행한다. 기본 smoke는 crawl/monitor 쓰기 작업을 실행하지 않는다. `--write`를 붙인 경우에만 KONEPS crawl과 strategy monitor가 운영 DB에 기록을 남기며 Telegram 알림이 나갈 수 있다.

## 1. 사전 확인

운영 `.env`에 최소한 아래 값이 있어야 한다.

- `DATABASE_URL` 또는 `DATABASE_*`
- `JWT_SECRET_KEY`
- `KONEPS_OPENAPI_SERVICE_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- webhook을 쓰는 경우 `TELEGRAM_WEBHOOK_SECRET`

Telegram 수신 계정은 봇과 대화를 한 번 시작해야 한다.

## 2. 서버 기동

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
- strategy monitor: `POST /api/v1/operator/strategy/monitor`
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

## 6. 실패 위치 구분

- API 기동 문제: `/health` 실패
- DB/schema 문제: profile, strategy, dashboard 실패
- KONEPS credential 문제: crawl 단계에서 `KONEPS_OPENAPI_SERVICE_KEY` 또는 외부 API 오류
- 후보/전략 문제: candidate preview 또는 monitor에서 `returned=0`, `persisted=0`
- Telegram 문제: `telegram status` 또는 operations dashboard의 Telegram 카드가 `watch`/`critical`
- task/broker 문제: operations dashboard의 task 카드가 `watch`/`critical`

## 7. 자주 쓰는 옵션

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
