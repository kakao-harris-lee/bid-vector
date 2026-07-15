# CLAUDE.md

이 파일은 이 저장소의 단일 에이전트 작업 지침입니다. 현재 제품/실행 개요는 `README.md`, 단계별 목표는 `docs/roadmap.md`를 기준으로 합니다.

## 0. 환경 상태

이 호스트는 `ENVIRONMENT=production`일 수 있지만 외부 실사용자 트래픽이 없는 단일 검증 환경입니다. 운영자 1인이 실제 키와 운영 데이터를 사용해 서비스 가능성을 검증합니다.

- 다운타임이 필요한 compose 재시작은 가능하지만, 의도를 명확히 남깁니다.
- DB write, 백필, 데이터 정리는 사용자 승인 후 진행합니다.
- 시크릿 조회, 외부 호출, Telegram 송신, 원격 push/merge는 사용자 승인 없이 진행하지 않습니다.
- `ENVIRONMENT=production` 자체는 임의로 바꾸지 않습니다.

### Volume Mount 함정

`docker-compose.yml` 서비스는 `./:/app` 바인드 마운트를 사용합니다. 컨테이너가 실행하는 코드는 호스트 working tree의 현재 브랜치입니다. PR이 main에 머지되어도 호스트가 feature 브랜치에 있으면 컨테이너는 feature 브랜치 코드를 계속 실행합니다.

머지 후 운영 반영 순서:

```bash
git branch --show-current
git checkout main
git pull --rebase origin main
docker compose --profile tasks restart worker beat
docker compose run --rm frontend-build
```

requirements, Dockerfile, 이미지 타깃 변경이 있으면 `docker compose --profile tasks up -d --build`를 사용합니다.

## 1. 프로젝트 요약

한 운영자가 여러 가상 회사와 실제/가상 회사 프로필을 사용해 KONEPS 공고 추천, 투찰가 산정, 가상 투찰, 정산, 정확도 검증을 반복하는 입찰 의사결정 지원 서비스입니다.

핵심 모듈:

- `app/services/koneps/`: KONEPS 수집
- `app/services/classifier.py`: 공고 적합도/유사도 분류
- `app/ai/price_prediction.py`, `app/ai/predictors/`: 가격 예측
- `app/services/allocation.py`: 입찰 추진 결정
- `app/services/paper_bidding_backtest.py`: paper bidding 백테스트
- `app/services/synthetic_experiment.py`: synthetic experiment
- `app/services/notifications/`: Telegram, 웹 알림, callback
- `frontend/src/features/`: 사용자/운영 화면

단기 목표는 기능 추가가 아니라 검증입니다.

1. 운영자 1명이 가상의 여러 회사를 만들고, 입찰 종류별 추천/가상 투찰/정산을 반복한다.
2. 과거 데이터 학습, synthetic backtest, forward paper bidding, smoke test가 자동으로 증적을 남긴다.
3. 실증 후 가상 회사마다 독립 ID와 사업자 정보를 부여해 운영 단계 검증을 진행한다.
4. 이후 사용자 웹과 관리자 웹을 분리한다. 사용자 웹은 공고 알림/투찰 선택, 관리자 웹은 백테스트/스모크/통계/데이터 상태를 담당한다.
5. 최종 사업 모델은 조건에 맞는 입찰 공고 알림, 추천 투찰가, 최종 낙찰 지원, 수수료/구독 수익화다.

## 2. 정직 명세

| 표현 | 실제 의미 | 규칙 |
|---|---|---|
| `probability_score` | 가격 적합도 추정 | 실제 낙찰 확률로 표시하지 않음 |
| `would_have_won_price_only` | 가격 근접 기반 추정 낙찰 | 실제 낙찰로 표시하지 않음 |
| `would_have_won_final` | 낙찰하한/적격 게이트까지 적용한 추정 | 예정가 부재 시 `unknown` 유지 |
| 투찰서 | 운영자 직접 제출을 돕는 초안 | KONEPS 자동 제출 없음 |
| synthetic operator | `synthetic-` 접두 검증 계정 | canonical operator 오염 금지 |

불변 원칙: predictor guardrail 우회 금지, pgvector 384 유지, 시간 누수 차단, per-operator silent canonical fallback 금지.

## 3. 빠른 명령어

```bash
source .venv/bin/activate
python -m pip install -r requirements/runtime.txt -r requirements/ml-training.txt -r requirements/dev.txt

pytest -q
pytest -q -k synthetic
npm --prefix frontend run test
npm --prefix frontend run build

python scripts/seed_synthetic_operators.py --dry-run
python scripts/seed_synthetic_operators.py
python scripts/backtest_synthetic_operators.py \
  --start-date 2025-01-01 --end-date 2025-12-31 --limit 200

python scripts/production_smoke_test.py \
  --base-url http://localhost:3000 \
  --evidence-out smoke-read.json

docker compose config --quiet
docker compose --profile tasks config --quiet
```

## 4. 코드 경계

- 라우터는 `app/api/`에 두고 얇게 유지합니다.
- 외부 입출력은 `app/schemas/`에 둡니다.
- 도메인 로직은 `app/services/` 또는 `app/ai/`에 둡니다.
- 모델 변경은 `app/models/models.py` + Alembic migration + 테스트를 함께 다룹니다.
- 프론트 신규 화면은 `frontend/src/features/<area>/`에 둡니다.
- UI 문구는 한국어로 작성하고 ko 단일 번들을 유지합니다.
- 새 API는 OpenAPI/type drift를 확인합니다.

작업 원칙:

1. 현재 실행 가능한 코드가 오래된 기획 문서보다 우선입니다.
2. 새 기능은 가능한 한 기존 패턴과 경계 안에서 작게 추가합니다.
3. 신규 API는 route/schema/service/test를 함께 다룹니다.
4. 백엔드 API 변경 시 프론트 타입과 호출부를 함께 확인합니다.
5. 비즈니스 로직을 라우터에 직접 키우지 않습니다.
6. 무거운 작업은 요청-응답 경로에서 직접 실행하지 않고 task 경로를 사용합니다.
7. 시크릿, 토큰, 사업자 개인정보는 코드/문서/로그에 남기지 않습니다.
8. 문서 변경 시 완료된 plan 문서를 계속 늘리지 않습니다. 현재 상태는 `README.md`, 단계 계획은 `docs/roadmap.md`, 운영 절차는 `docs/production-smoke-test.md` 또는 `docs/operations/`에 둡니다.

## 4.5 설계 규칙 (선언적 구성 · 상태머신 · 크기 · 위임 · 패턴 · 파이프라인)

고질적인 회귀는 대부분 **함수 안에 흩어진 매직값**과 **불어나는 조건 분기**에서 옵니다. 이를 막기 위해 값·규칙·특수케이스를 코드 흐름이 아니라 **선언적 데이터**(config · 상수 · 룩업표 · YAML/DSL)로 모으고, 코드는 그 데이터를 **해석만** 합니다. 아래 한도는 **소프트 가이드**(의미 있는 단일 책임이면 약간 초과 허용)이며, 리뷰어 에이전트가 PR에서 점검합니다. 파일 글롭별 상세 예시는 `.claude/rules/code-architecture.md`에 있습니다.

### 1. 선언적 구성 (매직값을 함수 밖으로)

- 동작·타이밍·리트라이·청크 크기·라우팅을 좌우하는 값은 함수/블록 안에 리터럴로 쓰지 않습니다.
  - **런타임·환경 설정**(타임아웃, 큐 이름, 청크 크기, 기능 토글)은 `app/core/config.py`의 pydantic `Settings`에 선언하고 주입합니다. env 우선순위 함정 주의: 기존 키 **값 변경**은 `docker compose up -d` 재생성이 필요하고 `restart`로는 반영되지 않습니다.
  - **교차 모듈 도메인 상수·값 집합**은 `app/core/constants.py`에 단일 출처로 선언합니다(예: `ACTIVE_DECISION_STATUSES`).
  - **프론트 상수·설정**은 컴포넌트 안 매직값 대신 `frontend/src/shared/`(또는 feature 로컬 config)로 추출합니다.
- Bad: 태스크 함수 안 `time_limit = 1800`.  Good: `settings.CELERY_TASK_TIME_LIMIT_SECONDS` 주입.

### 2. 상태·흐름은 룩업/전이표로 (중첩 if-else 금지)

- 2단계를 넘는 조건 체인이나 값 기반 라우팅은 중첩 `if-else` 대신 **lookup map(dict)·dispatch table**로, 프로세스 흐름 제어는 **FSM/상태 전이표**로 표현합니다.
- 이유: 분기 트리는 케이스가 늘 때마다 회귀의 온상이 됩니다. 데이터로 표현하면 새 케이스 추가가 한 줄로 안전해집니다.

```python
# Bad — 값 기반 분기가 트리로 자람
if action == "create":
    do_create(payload)
elif action == "update":
    do_update(payload)
elif action == "cancel":
    do_cancel(payload)

# Good — 룩업 디스패치 (미지원 키는 명시적으로 검증/거부)
ACTION_HANDLERS = {"create": do_create, "update": do_update, "cancel": do_cancel}
ACTION_HANDLERS[action](payload)
```

### 3. 예외·특수케이스는 데이터로 선언 (config/YAML/DSL)

- 자주 바뀌거나 운영자가 튜닝하는 특수 규칙(게이트 키워드, 발주처 밴드, 카테고리 라우팅, 면허 별칭 등)은 코드 분기로 흩뿌리지 않고 **선언적 데이터**로 모읍니다: 상수 테이블 · `OperatorStrategy` 필드, 규칙이 커지면 **YAML/DSL descriptor + 얇은 로더/해석기**.
- 실제 예: 해양 세그먼트 게이트의 `required_keywords`(OR 매칭)·`focus_categories`는 코드 `if`가 아니라 전략 **데이터**로 선언되고(`scripts/seed_marine_gate.py`, `docs/marine-engineering-gate.md`) 매처가 이를 해석합니다. 새 세그먼트는 **코드가 아니라 데이터**를 추가해 확장합니다.
- 규칙 자체는 데이터로, 코드는 해석기(interpreter)만 유지합니다. 테스트는 규칙 데이터 케이스 단위로 붙입니다.

### 4. 크기 한도 (초과 시 분해 권장)

- Python 파일 **~500줄**, 함수/메서드 **~50줄**, React 컴포넌트 **~250줄**.
- 초과하면 **책임 단위로 분해**합니다. 예: `collector.py`를 `parsing`/`openapi`/`html_parsing`/`matching`/`http_client` 모듈로 점진 분해(#127~#140). 긴 함수는 헬퍼로, 큰 화면은 하위 컴포넌트로.
- 한도를 넘겨야 할 합당한 이유가 있으면 PR 본문에 사유를 남깁니다.

### 5. 위임 (얇은 경계, 깊은 도메인)

- 라우터·컴포넌트는 얇게(§4). 도메인 로직은 `app/services/`·`app/ai/`(백엔드), `features/`·`shared/` 훅(프론트)으로 위임합니다.
- 한 함수가 여러 일을 하면 단일 책임 단위로 분해해 위임합니다.
- 무겁거나 시간제한이 있는 작업은 요청-응답 경로에서 직접 실행하지 않고 **celery task로 위임**합니다(예: defer→backfill).
- 멀티파일·복합 작업은 적절한 빌더 서브에이전트(`backend-builder`/`frontend-builder`/`ml-builder`)로 위임합니다.

### 6. 패턴 활용 (재사용 우선, 복붙 금지)

- 새 코드는 **기존 패턴을 먼저 찾아 따릅니다**: db 주입 service 클래스, repository-style 조회, `defer + chunk + idempotency` backfill(#82/#123/#138), self-chain 직렬화, 시간 헬퍼(`utc_now`/`ensure_utc`/`kst_now`/`to_kst`), react-query 훅, `zod` 폼, shadcn 래퍼.
- 같은 문제를 두 번째로 풀면 **공용 헬퍼/모듈로 추출**합니다. 복붙·중복 로직 금지.

### 7. 이벤트 드리븐 + 스트림 데이터 파이프라인 유지

- 수집·예측·정산·증적은 **celery task + beat 스케줄의 비동기 파이프라인**으로 흐릅니다. 이 흐름을 동기 블로킹으로 되돌리지 않습니다.
- 대량 작업은 **스트림/청크 단위**로 처리하고 **부분 진행을 영속화**(중간 commit), **멱등성**(`celery_task_id`/persisted-state)으로 재배달·재시작에 안전하게 만듭니다.
- 외부 호출(KONEPS 등)은 **rate/quota를 존중**해 직렬·throttle·backoff합니다. 동시 burst 금지(reserve-detail 동시 청크가 KONEPS rate limit을 초과한 사례에서 얻은 교훈).
- 작업은 soft/hard time limit 안에 들도록 분할하고, 못 끝내면 self-chain/재배달로 이어가되 **orphan을 남기지 않습니다**(reconciler·idempotency).

## 4.6 구현 규율 (계획 · TDD · 테스트 · 불필요한 변경 금지)

설계 규칙(§4.5)이 "코드를 어떤 모양으로 두는가"라면, 아래는 "어떻게 바꾸는가"의 회귀 방지 규율입니다.

- **계획 우선:** 파일을 수정하기 전에 구현 계획을 마크다운으로 제시하고 사용자 확인을 받습니다(§10 워크플로와 함께 적용).
- **TDD 회귀 가드:** 버그 수정은 먼저 실패하는 재현 테스트를 찾거나 만들고, 고친 뒤 그 테스트가 통과하며 기존 테스트가 깨지지 않음을 확인합니다.
- **테스트 실행:** 완료를 선언하기 전에 관련 테스트(`pytest`, `npm --prefix frontend run test`, build)를 실제로 돌립니다(§3·§9).
- **불필요한 변경 금지:** 요청되지 않은, 잘 동작하는 코드는 리팩터·재작성·재포맷하지 않습니다.

## 5. 서브에이전트

`.claude/agents/` 기준으로 역할을 나눕니다.

| 에이전트 | 책임 |
|---|---|
| `frontend-builder` | React 화면, 훅, UI 테스트 |
| `backend-builder` | FastAPI route/schema/service/test |
| `ml-builder` | predictor, ML training/release, 데이터셋 |
| `api-reviewer` | API 일관성, OpenAPI drift, 테스트 누락 |
| `ml-reviewer` | guardrail, pgvector, manifest, leakage |
| `test-runner` | pytest/vitest/playwright 실행과 실패 triage |
| `data-seed-runner` | synthetic seed/backtest 스크립트 실행 |

ML/예측 파이프라인은 `ml-builder`/`ml-reviewer` 소유입니다. backend-builder는 ML을 노출하는 얇은 API 경계까지만 담당합니다.

## 6. 스킬

`.claude/skills/`의 로컬 스킬을 사용합니다.

- `screen`: 프론트 화면 스캐폴드
- `api-route`: route/schema/service/test 스캐폴드
- `sync-types`: OpenAPI 기반 프론트 타입 갱신
- `run-backtest`: synthetic operator backtest 실행
- `seed-synthetic`: synthetic operator 시드
- `check`: pytest + vitest + build
- `release-preflight`: ML release preflight
- `api-doc-pipeline`: API 문서 생성

## 7. 자주 부딪히는 함정

- `CompanyProfile.user_id`와 `OperatorStrategy.user_id`는 현재 unique입니다. SaaS 멀티테넌트 전환은 별도 로드맵 단계입니다.
- `ensure_operator_strategy(db)`는 canonical operator 전략을 가져옵니다. per-operator 작업에서는 operator 객체 기반 helper를 사용합니다.
- `CELERY_ALLOW_INLINE_ML_TASKS=true`는 API 프로세스에서 ML 잡을 실행합니다. 운영에서는 켜지 않습니다.
- Telegram 송신은 `ENVIRONMENT=test`에서 스킵되어야 합니다.
- Frontend 빌드는 compose의 `frontend-build` 서비스가 담당합니다. 운영 반영 시 수동 산출물 편집을 피합니다.
- Web/API 문서의 phase plan이 코드보다 오래되면 코드와 `docs/roadmap.md`를 우선합니다.

## 8. 보안

- `.env`, `.env.example`, `JWT_SECRET_KEY`, `KONEPS_OPENAPI_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `ML_RELEASE_MANIFEST_SIGNING_KEY` 값을 문서/로그/코드에 쓰지 않습니다.
- 운영자 입찰 판단은 `BidDecisionRecord.reasoning` 등으로 감사 가능하게 남깁니다.
- KONEPS 외부 호출은 OpenAPI 우선, 저빈도, 적절한 제한을 유지합니다.

피해야 할 것:

- 기존 FastAPI/React 구조를 무시하고 새 프레임워크를 도입
- `app/main.py`나 대형 화면 파일에 새 도메인 로직을 누적
- 테스트 없이 predictor, guardrail, score 계산 로직 변경
- synthetic 데이터를 canonical operator에 섞기
- KONEPS/Telegram 실제 호출을 테스트 환경에서 발생시키기
- `.env` 없이 시크릿을 코드에 직접 넣기
- 완료된 계획 문서를 새 계획처럼 남겨두기

## 9. PR 체크리스트

- [ ] 관련 pytest/vitest 통과
- [ ] 새 의존성은 적절한 requirements 또는 `frontend/package.json`에 반영
- [ ] 새 API는 schema/route/service/test 포함
- [ ] 새 화면은 `features/<area>/`에 배치
- [ ] OpenAPI 변경 시 타입 갱신
- [ ] README 또는 `docs/roadmap.md`/운영 문서 갱신
- [ ] 시크릿/개인정보 로깅 없음
- [ ] PR 본문에 어느 로드맵 단계/게이트와 연결되는지 명시
- [ ] 설계 규칙(§4.5): 매직값 없음(config/`constants.py`로 선언), 3단계+ 조건 분기 없음(룩업/FSM), 특수케이스는 데이터로 선언(config/YAML/DSL), 파일/함수 크기 한도 준수(초과 시 분해 또는 사유), 기존 패턴·헬퍼 재사용(복붙 없음), 무거운 작업은 task 경로·외부 호출은 throttle/멱등(파이프라인 유지)

## 10. 워크플로

비-trivial 작업은 다음 순서로 진행합니다.

```text
main 최신화 -> 별도 worktree + feature branch 생성 -> 그 worktree에서 작업/커밋 -> push -> PR 생성 -> code review -> 리뷰 결과 보고 -> 리뷰 대응 -> 사용자 머지 승인 -> 머지
```

원칙:

- `main`/`master` worktree에서 비-trivial 파일 수정 금지.
- 작업 시작 전에 `git branch --show-current`와 `git status --short`로 현재 브랜치와 dirty 상태를 확인한다.
- 현재 브랜치가 `main`/`master`이고 작업이 비-trivial이면 먼저 별도 worktree를 만든다.
- 새 worktree는 최신 `origin/main` 기준으로 만들고, 작업 브랜치는 목적이 드러나는 이름을 쓴다.
- 기존 worktree가 dirty이면 임의로 reset/restore하지 말고, 사용자에게 현재 변경을 새 worktree로 옮길지 확인한다.
- 테스트 통과는 code review가 아니다. 머지 전에는 diff를 실제로 읽고, 발견 사항/잔여 리스크/검증 결과를 사용자에게 보고한다.
- 사용자가 "리뷰 후 머지" 또는 "문제 없으면 머지"를 요청한 경우, 리뷰 결과를 먼저 보고하고 사용자가 머지를 승인한 뒤에만 머지한다.
- 리뷰에서 수정이 발생하면 같은 브랜치에서 수정 커밋과 재검증을 끝낸 뒤, 다시 리뷰 결과를 보고하고 머지 승인을 받는다.
- 예외는 문서 오타 1줄, 자명한 lint/format fix, 사용자의 명시적 직접 작업/직접 푸시 지시뿐입니다.

권장 명령:

```bash
git fetch origin
git worktree add ../bid-vector-<slug> -b <type>/<slug> origin/main
cd ../bid-vector-<slug>
```

브랜치 예:

- `feature/<slug>`
- `fix/<slug>`
- `chore/<slug>`
- `docs/<slug>`
- `refactor/<slug>`
