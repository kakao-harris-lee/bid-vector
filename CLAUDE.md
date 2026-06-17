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

## 10. 워크플로

비-trivial 작업은 다음 순서로 진행합니다.

```text
main 최신화 -> 별도 worktree + feature branch 생성 -> 그 worktree에서 작업/커밋 -> push -> PR 생성 -> code review -> 리뷰 대응 -> 사용자 승인 후 머지
```

원칙:

- `main`/`master` worktree에서 비-trivial 파일 수정 금지.
- 작업 시작 전에 `git branch --show-current`와 `git status --short`로 현재 브랜치와 dirty 상태를 확인한다.
- 현재 브랜치가 `main`/`master`이고 작업이 비-trivial이면 먼저 별도 worktree를 만든다.
- 새 worktree는 최신 `origin/main` 기준으로 만들고, 작업 브랜치는 목적이 드러나는 이름을 쓴다.
- 기존 worktree가 dirty이면 임의로 reset/restore하지 말고, 사용자에게 현재 변경을 새 worktree로 옮길지 확인한다.
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
