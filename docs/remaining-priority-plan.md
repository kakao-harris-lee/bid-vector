# 잔여 과제 우선순위 및 실행 계획 (2026-05-18)

## 현재 검증 기준

- `python3 -m py_compile app/services/notifications/telegram_strategy.py app/services/notifications/update_processor.py app/api/operator.py app/api/operations.py app/api/analytics.py app/schemas/schemas.py`: 통과
- `pytest -q tests/test_operator.py tests/test_operations.py`: `76 passed`
- `pytest -q`: `164 passed, 1 skipped`
- `docker compose config --quiet`: 통과
- `docker compose --profile tasks config --quiet`: 통과

## 최근 완료 반영

- 웹 클라이언트용 통합 대시보드 API 추가: `GET /api/v1/operator/dashboard`
- 대시보드 응답에 카드, 최근 입찰 판단, 전략 모니터링 실행 이력, 피드백 요약, 관련 API 링크 포함
- 텔레그램 전략 명령 추가: `/strategy`, `/strategy_set`, `/strategy_clear`
- 텔레그램 웹훅/폴링 처리에서 전략 조회, 수정, 초기화 명령과 버튼 기반 단계형 편집 지원
- 로드맵 문서 갱신: `docs/optimal-bid-analysis-roadmap.md`

## 현재 범위 처리 기준

현재 선택은 이 저장소에서 프론트엔드를 직접 구현하는 것이 아니라, 외부 클라이언트가 소비할 수 있는 API 계약을 검증하는 것이다. 실제 웹 앱이 별도 저장소에 있다면 아래 계약을 화면 컴포넌트에 연결한다.

### 1순위 — 대시보드 API 계약 검증

`GET /api/v1/operator/dashboard`는 웹 클라이언트용 고정 계약이다. 빈 데이터 상태에서도 동일한 최상위 필드와 타입을 반환해야 하며, 샘플 데이터가 있으면 상세 API 링크가 포함되어야 한다.

#### 고정 응답 필드

- `cards`: 운영자 프로필, 진행 중 판단, 미확인 알림, 모니터링 실패, 추천 오차율 카드
- `recent_decisions`: 최근 입찰 판단과 `/api/v1/operations/bid-decisions/{id}` 상세 링크
- `recent_monitor_runs`: 최근 전략 모니터링 실행과 `/api/v1/operator/strategy/monitor/runs/{run_id}` 상세 링크
- `feedback_summary`: 예측/추천 피드백 집계와 `/api/v1/analytics/prediction-feedback` 링크
- `action_hrefs`: 분석 실행, 판단 목록, 후보 미리보기, 모니터링 실행, 운영 대시보드 진입점

#### 검증 기준

- FastAPI response model: `OperatorDashboardResponse`
- OpenAPI path: `/api/v1/operator/dashboard`
- 빈 상태: `recent_decisions=[]`, `recent_monitor_runs=[]`, `feedback_summary.result_count=0`
- 샘플 상태: decision detail href, monitor run detail href, feedback href, action href 모두 포함

### 2순위 — 실제 KONEPS/Telegram 운영 smoke test

모의/테스트 환경의 회귀 테스트는 통과했다. 남은 작업은 실제 외부 연동 환경에서 공고 수집, 전략 모니터링, 텔레그램 알림, 텔레그램 전략 수정이 한 주기에서 끊기지 않는지 검증하는 것이다.

#### 사전 설정

- `.env`에 `KONEPS_OPENAPI_SERVICE_KEY` 설정
- `.env`에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 설정
- webhook을 쓸 경우 `TELEGRAM_WEBHOOK_SECRET` 설정
- 대상 Telegram 계정이 봇과 대화를 한 번 시작했는지 확인

#### 실행 순서

1. KONEPS crawl 실행 후 crawl result payload 확인
2. `GET /api/v1/operator/strategy/candidates`로 strategy candidate preview 확인
3. `POST /api/v1/operator/strategy/monitor`로 strategy monitor 실행
4. Telegram 알림 수신 확인
5. 인라인 버튼 `투찰`, `검토`, `보류` 중 하나를 눌러 상태 변경 확인
6. `/strategy`, `/strategy_set`, `/strategy_clear` 실제 봇 대화 확인
7. `/strategy` 버튼에서 `업종`, `지역`, `키워드`, `예산`, `임계치`, `알림 범위`, `후보 수` 단계형 편집 확인

#### 증적

- crawl 결과 payload
- strategy monitor run detail: `/api/v1/operator/strategy/monitor/runs/{run_id}`
- Telegram 수신 메시지와 callback 처리 결과
- `GET /api/v1/analytics/operations-dashboard` 상태 카드
- Telegram delivery telemetry의 `status`, `detail`, failure reason

#### 실패 분류

- credential: KONEPS key, Telegram token/chat id/webhook secret 누락 또는 불일치
- KONEPS response: 외부 API 오류, payload schema 변화, 수집 결과 0건
- Telegram delivery: pending configuration, Bot API 오류, chat not found, webhook/polling 미도달
- strategy threshold: 후보는 있으나 threshold/filter 때문에 알림 대상 0건

### 3순위 — 운영 배포 preflight 실환경 실행

ML release manifest 생성, signature 검증, object storage publish/apply, rollout preflight 경로는 구현되어 있다. 남은 작업은 실제 운영 credential/IAM 환경에서 preflight를 실행하고 배포 체크리스트에 결과를 반영하는 것이다.

#### 실행 명령

```bash
make ml-release-preflight MANIFEST_REF=<manifest-ref> REQUIRE_SIGNATURE=true
```

또는:

```bash
python scripts/promote_ml_release.py preflight-rollout \
  --manifest <manifest-ref> \
  --require-signature
```

#### 확인 항목

- manifest 존재와 schema 로딩
- artifact checksum 검증
- signature required 모드
- `ML_RELEASE_OBJECT_STORAGE_URL`의 bucket/prefix 접근
- object storage write/delete probe
- IAM 실패 payload의 `status`, `detail`, `failure_reasons`, `preflight.checks`

#### 우선 검토 파일

- `app/services/ml_release.py`
- `scripts/promote_ml_release.py`
- `Makefile`
- `docs/ml-task-separation.md`
- `README.md`

#### 완료 기준

- 운영자가 실제 credential/IAM으로 rollout 전에 manifest, signature, bucket/prefix, write permission을 확인할 수 있어야 한다.
- 실패 시 `status`, `detail`, `failure_reasons`, `preflight.checks`만 보고 원인을 구분할 수 있어야 한다.

### 4순위 — 텔레그램 UX 보강

명령형 전략 수정은 계속 지원하고, `/strategy` 응답에 자주 쓰는 수정 버튼을 추가한다. 버튼 편집은 “필드 선택 → 새 값 입력 → 검증 → 적용/취소” 흐름으로 동작한다.

#### 지원 버튼

- `업종`: `focus_categories`
- `지역`: `focus_regions`
- `키워드`: `required_keywords`
- `예산`: `min_budget_estimate`, `max_budget_estimate`
- `임계치`: `minimum_match_score`, `minimum_probability_score`, `bid_now_threshold`, `review_threshold`
- `알림 범위`: `notify_only_high_priority`
- `후보 수`: `max_recommended_candidates`

#### 동작 기준

- 입력값은 확인 단계에서만 staged 상태이며, `적용` 전에는 DB 전략을 바꾸지 않는다.
- 잘못된 입력은 현재 값을 유지하고 올바른 예시를 다시 안내한다.
- `취소`를 누르면 staged 변경을 폐기한다.
- 기존 `/strategy_set`과 `/strategy_clear` 명령은 계속 사용할 수 있다.
