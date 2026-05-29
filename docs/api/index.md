# bid-vector API 레퍼런스

나라장터(KONEPS) 입찰 자동화 백엔드(FastAPI)의 HTTP API 레퍼런스입니다.
이 문서는 `api-doc-pipeline` 하네스(분석 → 설명 → 예제 → 완성도 리뷰)로 OpenAPI 스펙과
라우터 소스로부터 자동 생성되었습니다.

> 자동 생성 문서입니다. 코드가 바뀌면 `api-doc-pipeline` 스킬을 다시 돌려 갱신하세요.
> 수기로 고치면 다음 생성 시 덮어쓰입니다.

## 공통 규약

- **베이스 경로**: 모든 엔드포인트는 `/api/v1` 하위입니다 (예: `/api/v1/projects`).
- **인증**: 대부분의 보호 엔드포인트는 `Authorization: Bearer <ACCESS_TOKEN>` 헤더를 요구합니다.
  단, 일부 라우터(`projects`, `bids`, `predictions`, `ml`, `analytics`, `operations`,
  `operator`, `synthetic`, `admin`)는 **단일 운영자 모델** 특성상 토큰 의존성이 없고
  서버가 canonical operator를 자동으로 사용합니다. 각 태그 문서 상단의 인증 안내를 확인하세요.
  토큰은 `POST /api/v1/auth/session`으로 발급합니다.
- **요청/응답 형식**: JSON (`Content-Type: application/json`).
- **에러 형식**: FastAPI 표준 `{"detail": "..."}`. 검증 실패는 `422`,
  미인증 `401`, 미존재 `404`, 충돌 `409`.
- **비동기 잡**: 임베딩 재계산·모델 학습·백테스트 등 장시간 작업은 `202`로 `task_id`를
  반환하고, `GET .../tasks/{task_id}`로 상태를 폴링합니다.

## 태그별 문서

| 태그 | 문서 | 엔드포인트 | 설명 |
|---|---|---:|---|
| Authentication | [authentication.md](./authentication.md) | 6 | 운영자 부트스트랩·로그인·토큰·비밀번호 재설정·내 프로필 |
| Operator | [operator.md](./operator.md) | 14 | 운영자 프로필·전략·전략 후보·모니터링·알림 |
| Projects | [projects.md](./projects.md) | 9 | KONEPS 공고 CRUD·임베딩 재계산·유사 공고(pgvector) |
| Bids | [bids.md](./bids.md) | 4 | 입찰(투찰) 제출·조회·수정 |
| AI Predictions | [ai-predictions.md](./ai-predictions.md) | 3 | 가격 예측·투찰 추천·문서 분석 |
| ML Jobs | [ml-jobs.md](./ml-jobs.md) | 6 | 임베딩 백필·가격 predictor 학습·실험 재평가 (비동기) |
| Backtests | [backtests.md](./backtests.md) | 6 | paper bidding 백테스트 실행·요약·데이터 감사 |
| Synthetic | [synthetic.md](./synthetic.md) | 5 | synthetic 운영자(`synthetic-*`) 시드·백테스트 비교 |
| Dashboard | [dashboard.md](./dashboard.md) | 4 | 운영자 대시보드 후보·투찰·결과·요약 |
| Analytics | [analytics.md](./analytics.md) | 17 | 결정 분석·예측 리포팅·실험(decision experiments)·이벤트 |
| Operations | [operations.md](./operations.md) | 17 | 수집(크롤)·분류·기회 분석·입찰 결정·Telegram 알림 |
| Legacy Admin | [legacy-admin.md](./legacy-admin.md) | 3 | legacy 호환 관리 엔드포인트 (deprecated 성격) |

> **합계 94개 HTTP 엔드포인트** (+ `GET /health` 등 untagged 1개).
> WebSocket(`Realtime`) 채널은 OpenAPI 스펙에 포함되지 않아 본 레퍼런스에서 제외됩니다.

## 주의

- **win rate 프록시**: 백테스트/synthetic 문서의 낙찰률은
  `would_have_won_price_only_count / settled_count` 기반 **가격 기준 추정 낙찰**이며
  실제 낙찰이 아닙니다. 각 문서의 caveat를 참고하세요.
- **predictor guardrail**: 가격 예측은 카테고리 낙찰하한 미만 값을 반환하지 않도록
  guardrail이 적용됩니다 (응답의 `guardrail_*` 필드).
- 예제의 토큰·식별자·금액은 모두 가짜 값/플레이스홀더입니다.
