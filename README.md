# 나라장터 AI 입찰 서비스 (bid-vector)

`bid-vector`는 나라장터(KONEPS) 공고를 수집하고, 업체 조건에 맞는 입찰 후보를 선별한 뒤, 과거 개찰 데이터와 현재 전략을 바탕으로 투찰가와 실행 판단을 돕는 FastAPI + React 서비스입니다.

현재 단계는 외부 실사용자 대상 SaaS가 아니라 **서비스 가능성 검증 환경**입니다. 운영자 1인이 가상의 여러 회사를 만들어 업종별 공고 추천, 가상 투찰, 정산, 백테스트, 스모크 테스트 자동화를 검증하는 단계입니다.

중요한 한계도 명확합니다. 이 시스템은 나라장터에 자동으로 투찰서를 제출하지 않습니다. 추천 투찰가, 의사결정 요약, 투찰서 초안을 제공하고 운영자가 직접 제출합니다. `probability_score`는 실제 낙찰 확률이 아니라 과거 정산 결과로 보정한 **가격 적합도(추정)** 입니다.

## 현재 상태

- KONEPS 공고/개찰 결과 수집: OpenAPI 우선, 라이브 크롤 fallback
- 공고 분류/매칭: 업체 프로필, 면허, 지역, 예산, 시공능력, 임베딩 유사도 기반
- 가격 예측: historical / ensemble / distribution(예정가 추첨 분포 — 자동 승격 제외, 명시 선호로만) predictor + guardrail (sequence-model predictor 는 2026-08-09 은퇴)
- 의사결정: `bid_now`, `review`, `skip` 판단과 근거 영속화
- 알림: Telegram 버튼, `/strategy` 편집, 웹 알림, WebSocket realtime
- 검증: paper bidding, synthetic operator backtest, synthetic experiment lab, 운영 smoke run 저장
- 리포트: 추천 vs 실제 정확도, 의사결정 증적 export, 운영 KPI, operations dashboard
- 웹: `frontend/` Vite + React + TypeScript를 `/dashboard` 사용자 앱과 `/admin` 관리자 앱의 별도 Vite 번들로 서빙

현재 로드맵 위치는 **Phase 2 / G-2 독립 가상 사업자 운영 검증 진행 중**입니다. `50c9336` 기준으로 G-2 evidence API, 사업자별 알림 채널 메타데이터, sample-gap 기반 synthetic evidence 실행 계획, 관리자/사용자 surface 분리, G-2 runbook, operator-scoped synthetic evidence, read-only G-2 evidence 수집/스냅샷 경로가 `main`에 반영되었습니다. 현재 병목은 더 많은 기능 구현이 아니라 **3개 이상 가상 사업자에 대해 `reports/g2-evidence/` 기준 N일 운영 증적을 쌓고 exit review를 수행하는 것**입니다. 단계별 목표와 다음 로드맵은 [docs/roadmap.md](docs/roadmap.md)를 기준으로 봅니다.

## 서비스 목표

1. 운영자 1명이 여러 가상 회사를 만들어 입찰 종류별 후보 추천, 가상 투찰, 최종 낙찰 정산을 반복 검증합니다.
2. 과거 데이터 학습, synthetic backtest, forward paper bidding, smoke test가 자동으로 돌며 증적을 남깁니다.
3. 실증이 충분하면 가상 회사마다 독립 ID와 사업자 정보를 가진 운영 검증 단계로 넘어갑니다.
4. 사용자 대상 서비스 웹과 관리자 웹은 이미 `/dashboard`와 `/admin`의 별도 번들로 분리되어 있습니다. 사용자 웹은 공고 알림과 투찰 선택에 집중하고, 관리자 웹은 백테스트, 스모크 테스트, 통계, 데이터 상태를 다룹니다.
5. 실제 사업자는 설정한 조건에 맞는 입찰 가능 공고를 Telegram 또는 앱 알림으로 받고, 추천 투찰가와 근거를 확인한 뒤 투찰 여부를 선택합니다.

## 아키텍처

```text
KONEPS OpenAPI / Crawl
        |
        v
Canonical Project / HistoricalData / TenderResult
        |
        +--> transactional semantic_input.changed outbox
        |          |
        |          v
        |    inference worker --> embedding.ready --> similarity read model
        |
        +--> stored-only opportunity scan --> Price predictors + guardrails
        |
        v
BidDecisionRecord / PaperBid / SyntheticExperiment
        |
        +--> Telegram / Web notification / WebSocket
        |
        +--> read-only user API / Dashboard / Analytics / Accuracy report

Admin API --> training / backfill / reevaluation queues --> versioned artifacts
```

## 주요 디렉토리

```text
app/
  api/          FastAPI 라우터
  ai/           가격 예측, predictor, 추천 로직
  core/         설정, DB, 보안, 시간, pgvector 타입
  models/       SQLAlchemy 모델
  schemas/      Pydantic 스키마
  services/     수집, 분류, 의사결정, 백테스트, 알림, 리포트
  tasks/        Celery app/jobs
frontend/       Vite + React + TypeScript 대시보드
scripts/        시드, 백테스트, ML release, smoke test
docs/api/       HTTP API 레퍼런스
docs/           로드맵과 운영 문서
tests/          pytest 회귀 테스트
```

## 빠른 시작

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements/runtime.txt -r requirements/ml-training.txt -r requirements/dev.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 3000
```

`requirements.txt`는 runtime, embedding, training, dev 전체 번들을 한 번에 설치합니다. 로컬 API/pytest 개발은 위 세 그룹으로 충분합니다. sentence-transformers/torch가 필요한 임베딩 실험은 Docker `api-embedding`/`api-ml-full` 타깃을 쓰거나 별도로 설치합니다.

프론트엔드 개발 서버:

```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

기본 `dev`는 사용자 앱(`/dashboard`) 타깃입니다. 관리자 앱을 Vite dev server에서 확인할 때는 별도 admin 타깃으로 실행합니다.

```bash
npm --prefix frontend run dev:admin
```

Docker 로컬 실행:

```bash
cp .env.example .env
docker compose up -d --build
```

서버형 compose 실행:

```bash
make docker-up-server
```

기본 URL:

| Surface | URL |
|---|---|
| API / 앱 | http://localhost:3000 |
| API Docs | http://localhost:3000/docs |
| 사용자 화면 | http://localhost:3000/dashboard |
| 관리자 화면 | http://localhost:3000/admin |
| Vite dev server (사용자) | http://localhost:3001/dashboard |
| Vite dev server (관리자) | http://localhost:3001/admin |

## 자주 쓰는 검증 명령

```bash
pytest -q
npm --prefix frontend run test
npm --prefix frontend run build
docker compose config --quiet
docker compose --profile tasks config --quiet
```

Postgres 티어(선택): 기본 스위트는 SQLite에서 돌기 때문에 pgvector `VECTOR(384)`,
json 컬럼 DISTINCT, 행 잠금 클레임 같은 **PostgreSQL 전용 계약**은 검증되지 않습니다.
`postgres` 마커가 붙은 소수 테스트가 그 공백을 담당하며, `TEST_POSTGRES_URL`이
없으면 skip됩니다. 반드시 **일회용 인스턴스**를 쓰고 compose의 `db`(운영 데이터)를
가리키지 않습니다.

```bash
docker run --rm -d --name pgtier-test \
  -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=test \
  -p 55432:5432 pgvector/pgvector:pg16

TEST_POSTGRES_URL=postgresql://test:test@localhost:55432/test pytest -m postgres -q

docker stop pgtier-test
```

가상 운영자 검증:

```bash
python scripts/seed_synthetic_operators.py --dry-run
python scripts/seed_synthetic_operators.py
python scripts/backtest_synthetic_operators.py \
  --start-date 2025-01-01 --end-date 2025-12-31 --limit 200
```

운영 smoke test:

```bash
python scripts/production_smoke_test.py \
  --base-url http://localhost:3000 \
  --evidence-out smoke-read.json

python scripts/production_smoke_test.py \
  --base-url http://localhost:3000 \
  --write \
  --max-items 3 \
  --monitor-limit 3 \
  --evidence-out smoke-write.json
```

## API

상세 API는 [docs/api/index.md](docs/api/index.md)를 기준으로 봅니다. 주요 라우터는 `/api/v1` 하위에 있습니다.
프론트엔드 OpenAPI 타입은 `frontend/src/shared/types/openapi.d.ts`에 생성되며, API 스키마 변경 후 아래 명령으로 갱신/검증합니다.

```bash
npm --prefix frontend run sync-types
npm --prefix frontend run check:sync-types
```

- `/auth`: 단일 운영자 부트스트랩, 세션, 비밀번호 재설정
- `/operator`: 회사 프로필, 전략, 후보, 모니터링, 알림
- `/projects`: 공고 CRUD, 임베딩 재계산, 유사 공고
- `/operations`: 수집, 분류, 기회 분석, 입찰 판단, Telegram 연동
- `/predictions`: 가격 예측, 투찰 추천, 문서 분석
- `/analytics`: 정확도, KPI, funnel, decision experiments, operations dashboard
- `/backtests`: paper bidding 백테스트
- `/synthetic`: 가상 운영자와 synthetic experiment
- `/ml`: 임베딩 백필, predictor 학습, 실험 재평가 비동기 작업

G-2 운영 검증에서 자주 쓰는 엔드포인트:

- `/analytics/g2-evidence`: operator별 smoke/monitor/experiment/notification 증적 ledger
- `/operator/notification-channels`: operator별 masked notification route metadata
- `/synthetic/experiments/sample-gaps/candidates`: sample-gap 기반 synthetic evidence 실행 계획
- `scripts/collect_g2_evidence.py`: operator별 read-only evidence, `daily-worklog.json`, `manifest-draft.json` 수집
- `scripts/g2_blocking_gap_register.py`: manifest들의 unresolved G-2 gap을 JSON/Markdown 운영표로 병합
- `scripts/run_g2_synthetic_evidence.py --evidence-out`: sample-gap dry-run/write 승인 payload를 파일 증적으로 저장
- `scripts/verify_g2_notification_targets.py`: notification channel masking, dry-run, non-canonical 송신 경계 검증
- `scripts/build_g2_exit_review.py`: 일일 manifest draft를 `manifest.json`/`exit-review.md` review bundle로 병합
- `scripts/check_g2_exit_readiness.py`: review manifest가 G-2 human review에 올릴 준비가 됐는지 gate별 점검

## 문서

- [CLAUDE.md](CLAUDE.md): 에이전트 작업 규칙과 함정
- [docs/roadmap.md](docs/roadmap.md): 현재 단계, 목표, exit gate
- [docs/operations/g2-exit-agent-plan.md](docs/operations/g2-exit-agent-plan.md): G-2 exit 기반 병렬 작업 완료 기록과 잔여 TODO
- [docs/operations/g2-evidence-runbook.md](docs/operations/g2-evidence-runbook.md): G-2 operator별 운영 증적 반복 실행 절차와 exit review 양식
- [docs/operations/roadmap-next-agent-plan.md](docs/operations/roadmap-next-agent-plan.md): 최근 완료된 병렬 작업 기록과 후속 gap
- [docs/production-smoke-test.md](docs/production-smoke-test.md): 운영 smoke test 절차
- [docs/ml-image-separation.md](docs/ml-image-separation.md): ML 이미지/의존성 분리
- [docs/ml-task-separation.md](docs/ml-task-separation.md): ML 작업 큐 분리
- [docs/operations/celery-broker-and-result-backend.md](docs/operations/celery-broker-and-result-backend.md): Celery 브로커(RabbitMQ)·결과 백엔드(PostgreSQL) 역할 경계
- [docs/operations/ml-release-business-group.md](docs/operations/ml-release-business-group.md): 업종 그룹 인식 ML release 절차

## 운영 원칙

- 시크릿은 `.env`와 운영 환경 변수로만 주입합니다.
- predictor guardrail은 우회하지 않습니다.
- synthetic 운영자는 `synthetic-` 접두 계정만 사용해 canonical operator 데이터를 오염시키지 않습니다.
- 백테스트는 `data_cutoff_at` 규율로 시간 누수를 막습니다.
- 실제 Telegram/KONEPS 외부 호출, DB 정리, 운영 배포 작업은 의도와 증적을 남깁니다.

## License

Internal use only.
