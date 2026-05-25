# CLAUDE.md

이 문서는 **Claude Code**가 이 저장소에서 작업할 때 자동으로 읽는 진입 컨텍스트입니다.
일반 사용자 시스템 가이드(`AGENT.md`)는 별도로 유지되며, 본 파일은 Claude Code 전용 규칙·도구·서브에이전트·슬래시 커맨드·MCP 매핑에 집중합니다.

> 단일 출처: 기획·구현 매핑은 `docs/first_plan_implementation_review.md`,
> 운영·작업 원칙은 `AGENT.md`,
> 웹 프론트엔드 확장 계획은 `docs/web-development-plan.md`를 참고하세요.

## 1. 프로젝트 한 줄 요약

한 업체(단일 운영자)가 나라장터(KONEPS) 공고에서 낙찰 가능성이 가장 높은 입찰 후보를 자동으로 찾고, 적정 투찰가를 결정해 추진하도록 돕는 FastAPI 백엔드 + Vite/React/TS 프론트엔드.

핵심 도메인 모듈은 `app/services/koneps/`(수집), `app/services/classifier.py`(분류), `app/ai/predictors/`(가격 예측), `app/services/allocation.py`(결정 엔진), `app/services/notifications/`(텔레그램/실시간), `app/services/paper_bidding_backtest.py`(백테스트)입니다.

## 2. 빠른 명령어

```bash
# Python 가상환경 (Mac 개발 기준)
source .venv/bin/activate

# 백엔드 테스트
pytest -q                                      # 전체
pytest -q tests/test_paper_bidding_backtest.py # 특정 파일
pytest -q -k synthetic                         # 키워드 매칭

# 백엔드 정적 검증
python -m py_compile app/services/*.py
black app/
flake8 app/

# 프론트엔드
npm --prefix frontend install
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run dev   # http://localhost:5173

# 가상 운영자 시드 + 백테스트 (Phase 5 검증용)
python scripts/seed_synthetic_operators.py
python scripts/backtest_synthetic_operators.py \
    --start-date 2025-01-01 --end-date 2025-12-31 --limit 200

# Docker 로컬 통합 검증
docker compose config --quiet
docker compose --profile tasks config --quiet
```

`/check` 슬래시 커맨드는 위 명령들을 한 번에 실행합니다.

## 3. 디렉토리 규칙 (어디에 무엇이 들어가나)

- `app/api/` — FastAPI 라우터. 얇게 유지, 로직은 services/ai로 위임
- `app/schemas/` — Pydantic 입출력 스키마. 외부 입력은 반드시 여기를 거침
- `app/services/` — 도메인 로직. 한 파일 한 책임. 25개 모듈 존재
- `app/ai/` — predictor / backtest / 추천 / 문서 분석
- `app/core/` — config / database / security / time / vector / single_user
- `app/models/models.py` — SQLAlchemy 모델 (19개). 변경 시 마이그레이션·테스트 동반
- `app/tasks/` — Celery app/jobs. broker `memory://`도 동작해야 함
- `frontend/src/` — Vite + React + TS. 신규 화면은 `features/<area>/` 하위
- `scripts/` — 실행형 스크립트 (백테스트/시드/ML release)
- `docs/` — 운영·계획 문서. 새 계획은 `docs/<topic>-plan.md`
- `tests/` — pytest. 신규 API/서비스는 정상 + 실패 케이스 최소 1쌍

**금지**: 새 기능을 `app/main.py`나 `frontend/src/App.tsx`에 직접 부풀리지 말 것. App.tsx는 Phase 0에서 `features/`로 분할 진행 중입니다.

**한국형 서비스 — 다중 로케일 미지원**: 이 프로젝트는 나라장터(KONEPS) 한정 서비스라 i18n 다중 로케일을 지원하지 않습니다. `frontend/src/shared/i18n/`는 ko 단일 번들만 유지하고 영어/기타 로케일 번들을 추가하지 마세요. UI 문구는 한국어로 작성하고 `ko.json`에 모아 일관성만 관리합니다.

## 4. 작업 원칙 (Claude Code가 반드시 지킬 것)

1. **테스트 깨면 진행하지 않는다.** 변경 후 관련 pytest/vitest 실행이 그린이어야 함. 실패 시 우선 진단 — 강제로 통과시키지 말 것.
2. **현재 구현을 존중한다.** `first_plan.md`보다 코드가 우선. 충돌이 있으면 호환 가능한 중간 단계를 먼저 설계.
3. **단일 운영자 모델을 깨지 말 것.** legacy `allocations` 테이블은 유지하되 새 로직은 `BidDecisionRecord` 기준으로 작성.
4. **synthetic 운영자는 username 접두 `synthetic-`로 한정.** canonical `operator` 계정과 절대 충돌시키지 말 것.
5. **타입/스키마를 동기화한다.** 백엔드 API 변경 시 `/sync-types` 또는 직접 `frontend/src/shared/types/openapi.d.ts` 갱신.
6. **시크릿은 코드에 절대 쓰지 않는다.** `.env`/`.env.example`만 사용.
7. **predictor guardrail은 우회하지 않는다.** 카테고리 낙찰하한 미만 추천은 항상 차단.
8. **메모리 broker에서도 동작해야 한다.** 새 Celery 태스크는 `memory://`에서 eager 실행이 가능해야 함.
9. **요약 강박을 피한다.** 사용자는 코드 diff를 직접 본다. 작업 후 짧게 핵심만 보고.
10. **불확실하면 묻는다.** `AskUserQuestion`을 사용해 결정을 먼저 받음.

## 5. 서브에이전트 매핑

`.claude/agents/`에 정의되거나 (없으면) `Agent` 호출 시 다음 의도로 사용:

| 에이전트 | 책임 | 권한 | 호출 예 |
|---|---|---|---|
| `frontend-builder` | `frontend/src/features/`/`shared/` 하위 화면·훅 구현 | Read/Write/Edit + npm/vitest 실행 | "Phase 1 StrategyEditor 구현해줘" |
| `backend-builder` | `app/api/`, `app/services/`, `app/schemas/`, `tests/` 구현 | Read/Write/Edit + pytest/py_compile | "Phase 5 synthetic backtest 라우터 + 서비스 만들어줘" |
| `api-reviewer` | 변경된 라우터·스키마·서비스의 일관성·OpenAPI drift·테스트 누락 점검 | Read 전용 | "이번 PR의 API 변경 리뷰해줘" |
| `test-runner` | pytest/vitest/playwright 실행, 실패 triage | Read + 명령 실행 (수정 금지) | "전체 테스트 돌리고 실패한 것만 표 만들어줘" |
| `data-seed-runner` | 시드/리셋 스크립트만 실행 (`seed_synthetic_operators.py` 등) | 명령 실행 | "synthetic 운영자 리시드" |

원칙: 한 에이전트가 다른 에이전트의 책임 영역을 건드리지 않게 프롬프트에 영역을 명시합니다.

## 6. 슬래시 커맨드

`.claude/commands/`에 마크다운으로 정의된 (또는 정의할) 단축 명령:

- `/screen <feature> <ScreenName>` — `features/<feature>/<ScreenName>.tsx`, `<ScreenName>.test.tsx`, `index.ts` + 라우트 등록 + react-query 훅 placeholder
- `/api-route <name>` — `app/api/<name>.py`, `app/schemas/<name>.py`, `tests/test_<name>.py` 스캐폴드 + `routes.py` 등록
- `/sync-types` — 백엔드 OpenAPI(`/openapi.json`) → `frontend/src/shared/types/openapi.d.ts` 재생성
- `/run-backtest [slugs]` — 활성 venv로 `scripts/backtest_synthetic_operators.py` 실행
- `/seed-synthetic [--purge]` — `scripts/seed_synthetic_operators.py` 실행
- `/check` — `pytest -q && npm --prefix frontend run test && npm --prefix frontend run build`
- `/release-preflight <manifest-ref>` — `python scripts/promote_ml_release.py preflight-rollout --manifest <manifest-ref> --require-signature`

새 슬래시 커맨드를 만들 땐 같은 폴더에 `<name>.md`를 추가하고 사용 예와 인자를 명시합니다.

## 7. MCP 서버 (개발 환경 한정)

`.claude/mcp.json` 또는 사용자 설정에 등록:

- **PostgreSQL (dev, read-only)** — 로컬 dev DB. 마이그레이션/시드는 MCP가 아닌 스크립트로만.
- **Filesystem** — repo 루트 한정 (기본 동작).
- **(옵션) KONEPS mock** — 로컬 mock 응답 디렉토리. 사양 점검 시에만 활성화.
- **(옵션) Telegram Bot API** — **dev 토큰**일 때만. 운영 토큰은 절대 노출 금지.

운영 자격증명은 어떤 경우에도 MCP에 직접 노출하지 않습니다. 운영 작업이 필요하면 명령형 스크립트(`scripts/production_smoke_test.py` 등)로 분리.

## 8. 자주 부딪히는 함정

- **`ensure_operator_strategy(db)`는 항상 canonical operator의 전략을 가져옵니다.** 운영자 ID로 작업할 땐 `PaperBiddingBacktestService._resolve_operator_strategy(operator)`처럼 operator 객체로 직접 조회해야 함.
- **`CompanyProfile.user_id`/`OperatorStrategy.user_id`는 `unique=True`** — 한 운영자에 하나만. 다중 운영자(synthetic 백테스트 포함)에서는 사용자별로 따로 upsert.
- **pgvector 차원은 384 고정** (`Project.embedding`). 모델을 바꿀 땐 manifest promotion gate를 거쳐 차원 호환성을 먼저 검증.
- **임베딩 모델은 `paraphrase-multilingual-MiniLM-L12-v2`** (`models/` 하위에 캐시됨). 오프라인이면 `CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY=true`로 다운로드 회피.
- **Telegram 송신은 `ENVIRONMENT=test`에서 자동 스킵**. 테스트가 실제 메시지를 보내면 안 됨.
- **Celery `CELERY_ALLOW_INLINE_ML_TASKS=true`는 ML 잡을 API 프로세스에서 eager 실행**. 운영에서는 절대 켜지 말 것.
- **`comparison.csv`/`.json`의 win rate 프록시는 `would_have_won_price_only_count / settled_count`** — "실제 낙찰"이 아니라 "가격 기준 추정 낙찰". 분석 시 항상 caveat 표기.
- **WebSocket 토큰 만료**는 Phase 7에서 통합 처리 예정. 그 전까지 임시 재로그인 모달로 대응.

## 9. 보안 빨간 줄

- `.env`, `.env.example`, `JWT_SECRET_KEY`, `KONEPS_OPENAPI_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `ML_RELEASE_MANIFEST_SIGNING_KEY`는 절대 git에 커밋 금지.
- 운영자 입찰 기록은 항상 감사 가능하게 영속화(`BidDecisionRecord.reasoning`에 변경 사유 포함).
- predictor가 카테고리 낙찰하한 미만 값을 반환하지 못하도록 `app/ai/price_prediction.py::_apply_prediction_guardrails`를 우회하지 말 것.
- 외부 사이트(나라장터) 크롤 시 과도한 요청 금지. `fake-useragent` + 적절한 sleep, OpenAPI 우선 경로 유지.

## 10. PR/커밋 체크리스트

- [ ] 관련 pytest/vitest 그린
- [ ] 새 의존성은 `requirements/<group>.txt` 또는 `frontend/package.json`에 명시
- [ ] 새 API는 schema/route/service/test 4종 세트
- [ ] 새 화면은 `features/<area>/`에 두고 라우트 등록
- [ ] `frontend/src/styles.css`에 새 규칙 추가하지 않음 (Tailwind/shadcn로 작성)
- [ ] README 또는 `docs/<topic>.md` 갱신 (사용자가 인지해야 할 변화)
- [ ] OpenAPI 변경 시 `/sync-types` 실행 → `openapi.d.ts` 커밋
- [ ] 시크릿/개인정보 로깅하지 않음
- [ ] 본 PR이 어느 Phase의 어느 수용 기준을 충족하는지 설명 본문에 명시

## 11. 작업 시작 시 권장 워크플로

> **모든 비-trivial 작업은 반드시 다음 순서를 지킵니다 (글로벌 `~/.claude/CLAUDE.md`의 MANDATORY WORKFLOW와 일치):**
>
> `main 확인 → feature branch 생성 → 작업/커밋 → push → PR 생성 → /code-review → 리뷰 대응 → 머지`

1. **상태 파악**: `git status` / `git log -n 10`로 현재 상태 확인. `main`은 `origin/main`과 동기여야 함.
2. **컨텍스트 정독**: 관련 docs 1~2개 빠르게 정독 (`docs/web-development-plan.md` 또는 도메인 문서).
3. **브랜치 생성**: `git switch -c feature/<slug>` (또는 `fix/<slug>`, `chore/<slug>`). 절대 `main`에 직접 커밋 금지.
4. **계획**: 변경 범위가 큰 작업은 `Plan` 서브에이전트로 단계 분해 → 사용자 확인.
5. **구현**: 적합한 서브에이전트(`frontend-builder` / `backend-builder`)에 위임. 의미 있는 단위로 atomic commit.
6. **회귀 검증**: 변경 후 `/check`로 pytest + vitest + build 확인.
7. **PR 생성**: `git push -u origin <branch>` → `gh pr create`. PR 본문에 무엇/왜/테스트/수용 기준 체크리스트 포함.
8. **코드 리뷰**: PR을 연 직후 `/code-review`(또는 `/code-review:code-review`) 실행. 자동 리뷰 결과를 PR에 코멘트로 게시.
9. **리뷰 대응**: 받은 코멘트는 같은 브랜치에 추가 커밋 + push로 처리. 회귀 방지 테스트도 함께.
10. **머지**: 사용자가 명시적으로 "merge"/"land"라고 지시할 때만 진행.
11. **보고**: 사용자에게는 핵심 변경 요약 + PR 링크 + 다음 액션만 짧게 보고.

### 브랜치 네이밍

- `feature/<slug>` — 신규 기능, 화면, 라우트
- `fix/<slug>` — 버그 수정
- `chore/<slug>` — 의존성 업그레이드, 빌드/CI 변경
- `docs/<slug>` — 문서만 변경
- `refactor/<slug>` — 동작 변경 없는 리팩토링

### 예외 (브랜치/PR 없이 main에 직접 푸시 가능)

다음만 예외다. 의심되면 항상 PR 경로로:

- 문서 오타 1줄 수정
- 명백히 자명한 lint/format fix
- 사용자가 명시적으로 "PR 없이 직접 푸시"를 지시했을 때

### 이미 main에 커밋된 경우

작업을 시작했는데 main에 커밋해 버렸다면:

```bash
git switch -c feature/<slug>          # 현재 HEAD에서 새 브랜치 생성
git switch main
git reset --keep origin/main          # main을 origin과 동기화
git switch feature/<slug>             # 작업 계속
git push -u origin feature/<slug>     # PR 경로로 진입
gh pr create ...
```
