# Operator API

> 베이스 경로: `/api/v1/operator` · 베이스 URL 예시: `http://localhost:3000`
> 인증: Bearer 토큰은 선택이다. 토큰이 없으면 legacy 단일 운영자 경로로 canonical `operator`를 사용한다. 토큰이 있으면 토큰 소유자가 기본 target이고, canonical `operator` 또는 admin만 `?operator_id=`로 다른 운영자(`synthetic-*` 포함)를 조회/실행할 수 있다.
> operator context: `operator_id`는 응답 데이터가 귀속된 target 운영자다. `current_operator_id`/`current_operator_username`도 프론트가 현재 선택한 회사로 표시해야 하는 target 운영자를 뜻한다. 프론트는 선택값이 `null`이면 `operator_id` query를 생략하고, privileged 사용자가 다른 회사를 선택했을 때만 숫자 `operator_id`를 전달한다.
> 도메인 요약: **프로필**(`CompanyProfile`)은 면허·지역·매출 등 적합도 정보를, **전략**(`OperatorStrategy`)은 어떤 공고를 감시·우선순위화할지 규칙을 담는다. **전략 모니터링**은 매칭 후보를 평가해 입찰 판단(`BidDecisionRecord`)을 영속화하고 알림을 만든다.

## 목차
- [GET /profile](#get-apiv1operatorprofile) — 운영자 계정+회사 프로필 조회
- [PUT /profile](#put-apiv1operatorprofile) — 운영자 계정+적합도 프로필 갱신
- [GET /strategy](#get-apiv1operatorstrategy) — 감시 전략 조회
- [PUT /strategy](#put-apiv1operatorstrategy) — 감시 전략 갱신
- [GET /strategy/candidates](#get-apiv1operatorstrategycandidates) — 전략 매칭 후보 미리보기(스냅샷 읽기)
- [POST /strategy/candidates/refresh](#post-apiv1operatorstrategycandidatesrefresh) — 미리보기 스냅샷 재계산 큐잉(202)
- [POST /strategy/monitor](#post-apiv1operatorstrategymonitor) — 전략 실행 큐잉(202, 판단·알림 영속화)
- [GET /strategy/monitor/runs](#get-apiv1operatorstrategymonitorruns) — 모니터링 실행 이력
- [GET /strategy/monitor/runs/{run_id}](#get-apiv1operatorstrategymonitorrunsrun_id) — 실행 상세
- [POST /strategy/monitor/async](#post-apiv1operatorstrategymonitorasync) — 전략 비동기 실행(큐잉)
- [GET /strategy/monitor/tasks/{task_id}](#get-apiv1operatorstrategymonitortaskstask_id) — 비동기 작업 상태 조회
- [GET /dashboard](#get-apiv1operatordashboard) — 웹 대시보드 종합 페이로드
- [GET /overview](#get-apiv1operatoroverview) — 컴팩트 요약 지표
- [GET /accounts](#get-apiv1operatoraccounts) — 현재 토큰에서 볼 수 있는 운영자 계정 목록
- [GET /notification-channels](#get-apiv1operatornotification-channels) — operator별 masked 알림 채널 메타데이터
- [GET /notifications](#get-apiv1operatornotifications) — 알림 목록
- [PUT /notifications/{notification_id}/read](#put-apiv1operatornotificationsnotification_idread) — 알림 읽음 처리

---

## GET /api/v1/operator/profile

싱글톤 운영자의 계정 정보와 회사 적합도 프로필을 한 번에 반환한다. 운영자 설정 화면 진입 시 현재 저장된 면허/지역 코드·연매출·수행능력 점수·누적 수주 건수를 불러올 때 사용한다.

- 인증: 불필요(단일 운영자).
- 도메인: `license_codes`/`region_codes`는 내부 멀티값 텍스트를 배열로 분해한 값. `profile_configured`는 면허·지역·연매출·수행능력·수주 중 하나라도 채워졌으면 true.

**파라미터**

(없음)

**요청 예시**
```bash
curl http://localhost:3000/api/v1/operator/profile
```

**응답 200**
```json
{
  "operator_id": 1,
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "운영자",
  "company": "가나다건설",
  "is_active": true,
  "created_at": "2026-01-05T09:12:00Z",
  "business_type": "건설업",
  "license_codes": ["C1100", "C1200"],
  "region_codes": ["11", "41"],
  "annual_revenue": 4200000000.0,
  "capacity_score": 0.72,
  "total_awards": 18,
  "profile_configured": true
}
```

---

## PUT /api/v1/operator/profile

싱글톤 운영자의 계정·적합도 프로필을 부분 갱신한다. 모든 필드는 선택이며 `null`/생략 시 기존 값을 유지한다. 설정 화면에서 면허·지역·매출 등을 저장할 때 호출한다.

- 인증: 불필요(단일 운영자).
- 도메인: `capacity_score`는 0~1 정규화 점수, `annual_revenue`·`total_awards`는 0 이상. 변경하려는 username/email은 다른 사용자와 중복 불가.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | username | string\|null | 아니오 | 변경 시 유일해야 함 |
| body | email | string\|null | 아니오 | 변경 시 유일해야 함 |
| body | full_name | string\|null | 아니오 | 담당자명 |
| body | company | string\|null | 아니오 | 회사명 |
| body | business_type | string\|null | 아니오 | 업종 |
| body | license_codes | string[]\|null | 아니오 | 면허 코드 목록 |
| body | region_codes | string[]\|null | 아니오 | 지역 코드 목록 |
| body | annual_revenue | number\|null | 아니오 | 연매출, >=0 |
| body | capacity_score | number\|null | 아니오 | 수행능력 점수, 0~1 |
| body | total_awards | integer\|null | 아니오 | 누적 수주, >=0 |

**요청 예시**
```bash
curl -X PUT http://localhost:3000/api/v1/operator/profile \
  -H "Content-Type: application/json" \
  -d '{
    "company": "가나다건설",
    "business_type": "건설업",
    "license_codes": ["C1100", "C1200"],
    "region_codes": ["11", "41"],
    "annual_revenue": 4200000000,
    "capacity_score": 0.72,
    "total_awards": 18
  }'
```

**응답 200**
```json
{
  "operator_id": 1,
  "username": "operator",
  "email": "operator@example.com",
  "full_name": "운영자",
  "company": "가나다건설",
  "is_active": true,
  "created_at": "2026-01-05T09:12:00Z",
  "business_type": "건설업",
  "license_codes": ["C1100", "C1200"],
  "region_codes": ["11", "41"],
  "annual_revenue": 4200000000.0,
  "capacity_score": 0.72,
  "total_awards": 18,
  "profile_configured": true
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | 변경하려는 username이 이미 존재 (`Username already exists`) |
| 400 | 변경하려는 email이 이미 존재 (`Email already exists`) |
| 422 | 필드 범위 위반(capacity_score>1, annual_revenue 음수 등) |

---

## GET /api/v1/operator/strategy

싱글톤 운영자의 감시 전략을 반환한다. 감시할 카테고리/지역/키워드, 매칭·확률 임계, 우선순위 규칙 등 모니터링·알림에 쓰이는 규칙 전체를 조회한다. 전략 편집 화면 로딩 시 호출.

- 인증: 불필요(단일 운영자, `OperatorStrategy.user_id` unique).
- 도메인: `bid_now_threshold`/`review_threshold`는 우선순위 점수를 "즉시 투찰/검토" 액션으로 매핑하는 경계(review ≤ bid_now). `strategy_configured`는 기본값에서 하나라도 벗어났으면 true.

**파라미터**

(없음)

**요청 예시**
```bash
curl http://localhost:3000/api/v1/operator/strategy
```

**응답 200**
```json
{
  "operator_id": 1,
  "focus_categories": ["일반토목공사", "건축공사"],
  "focus_regions": ["11", "41"],
  "exclude_regions": ["50"],
  "required_keywords": ["도로", "포장"],
  "exclude_keywords": ["철거"],
  "min_budget_estimate": 100000000.0,
  "max_budget_estimate": 5000000000.0,
  "minimum_match_score": 0.6,
  "minimum_probability_score": 0.55,
  "bid_now_threshold": 0.75,
  "review_threshold": 0.55,
  "auto_workload_penalty_multiplier": 1.0,
  "category_priority_overrides": {"일반토목공사": 1.2},
  "notify_only_high_priority": true,
  "max_recommended_candidates": 10,
  "strategy_configured": true
}
```

---

## PUT /api/v1/operator/strategy

싱글톤 운영자의 감시 규칙을 부분 갱신한다. `null`/생략 필드는 기존 값을 유지한다. 전략 편집 화면 저장 시 호출.

- 인증: 불필요(단일 운영자).
- 도메인: 임계값 검증은 요청에 없으면 기존 저장값으로 합성해 `review_threshold ≤ bid_now_threshold`를 강제한다. `auto_workload_penalty_multiplier`는 0~2로 clamp, `category_priority_overrides`는 카테고리별 우선순위 가중치 맵. predictor guardrail(카테고리 낙찰하한)은 전략과 무관하게 가격 추천 단계에서 항상 적용된다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | focus_categories | string[]\|null | 아니오 | 감시 대상 카테고리 |
| body | focus_regions | string[]\|null | 아니오 | 감시 대상 지역 코드 |
| body | exclude_regions | string[]\|null | 아니오 | 제외 지역 코드 |
| body | required_keywords | string[]\|null | 아니오 | 필수 포함 키워드 |
| body | exclude_keywords | string[]\|null | 아니오 | 제외 키워드 |
| body | min_budget_estimate | number\|null | 아니오 | 추정가 하한, >=0 |
| body | max_budget_estimate | number\|null | 아니오 | 추정가 상한, >=0 |
| body | minimum_match_score | number\|null | 아니오 | 매칭 점수 하한, 0~1 |
| body | minimum_probability_score | number\|null | 아니오 | 낙찰 확률 하한, 0~1 |
| body | bid_now_threshold | number\|null | 아니오 | 즉시 투찰 경계, 0~1 |
| body | review_threshold | number\|null | 아니오 | 검토 경계, 0~1 (≤ bid_now) |
| body | auto_workload_penalty_multiplier | number\|null | 아니오 | 부하 패널티 배수, 0~2 (clamp) |
| body | category_priority_overrides | object{string:number}\|null | 아니오 | 카테고리별 우선순위 가중치 |
| body | notify_only_high_priority | boolean\|null | 아니오 | 고우선순위만 알림 |
| body | max_recommended_candidates | integer\|null | 아니오 | 추천 후보 최대 수, 1~100 |

**요청 예시**
```bash
curl -X PUT http://localhost:3000/api/v1/operator/strategy \
  -H "Content-Type: application/json" \
  -d '{
    "focus_categories": ["일반토목공사", "건축공사"],
    "focus_regions": ["11", "41"],
    "min_budget_estimate": 100000000,
    "max_budget_estimate": 5000000000,
    "bid_now_threshold": 0.75,
    "review_threshold": 0.55,
    "notify_only_high_priority": true,
    "max_recommended_candidates": 10
  }'
```

**응답 200**
```json
{
  "operator_id": 1,
  "focus_categories": ["일반토목공사", "건축공사"],
  "focus_regions": ["11", "41"],
  "exclude_regions": [],
  "required_keywords": [],
  "exclude_keywords": [],
  "min_budget_estimate": 100000000.0,
  "max_budget_estimate": 5000000000.0,
  "minimum_match_score": 0.6,
  "minimum_probability_score": 0.55,
  "bid_now_threshold": 0.75,
  "review_threshold": 0.55,
  "auto_workload_penalty_multiplier": 1.0,
  "category_priority_overrides": {},
  "notify_only_high_priority": true,
  "max_recommended_candidates": 10,
  "strategy_configured": true
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 400 | `review_threshold cannot be greater than bid_now_threshold` |
| 422 | 개별 필드 범위 위반(0~1, 0~2, 1~100 등) |

---

## GET /api/v1/operator/strategy/candidates

저장된 감시 전략을 기준으로 현재 진행 중(마감 전)인 프로젝트 중 매칭 후보를 **미리보기**한다. 입찰 판단·알림을 만들지 않는 읽기 전용 프리뷰로, 전략 편집 시 "이 규칙이면 지금 어떤 공고가 잡히나"를 확인하는 데 쓴다.

- 인증: 불필요(단일 운영자).
- 도메인: 후보마다 매칭/확률/우선순위 점수, 추천 액션·투찰가, 선정 사유(`strategy_reasons`)를 제공. `high_priority_only=true`면 고우선순위만.
- 실행 모델: 이 엔드포인트는 요청 경로에서 ML 스캔을 실행하지 않는다. 마지막 계산 결과를 담은 **스냅샷 행을 순수 읽기**하고 `limit`으로 슬라이스할 뿐이다. 스냅샷이 없거나(최초) 낡았으면(`OPERATOR_PREVIEW_SNAPSHOT_STALE_SECONDS` 초과, 또는 스냅샷 계산 이후 전략이 수정됨) 재계산 task를 단일비행 가드 하에 자동 큐잉하고 **기존 스냅샷을 즉시 반환**한다. 최초 호출은 빈 `candidates` + `snapshot_status="running"`이므로 클라이언트는 `computed_at`/`snapshot_status`를 보고 폴링한다. 재계산이 SIGKILL/재시작(예: `docker compose restart worker`)으로 `running`에 고착되면, 그 행의 `updated_at`이 회수창(`OPERATOR_PREVIEW_SNAPSHOT_RUNNING_RECLAIM_SECONDS`, 기본 300s)을 넘긴 뒤의 다음 GET이 이를 고아로 보고 자동 재큐잉한다(reconciler 임계 2100s와 분리 — 미리보기가 오래 wedged 되지 않게). 직전 재계산이 실패한 스냅샷은 짧은 쿨다운(`OPERATOR_PREVIEW_SNAPSHOT_FAILURE_COOLDOWN_SECONDS`) 동안 자동 재큐잉하지 않는다 — 즉시 재시도는 `POST /strategy/candidates/refresh`.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer\|null | 아니오 | 반환 후보 수, 1~100 (기본 미설정) |
| query | high_priority_only | boolean\|null | 아니오 | 고우선순위 후보만 필터 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/strategy/candidates?limit=20&high_priority_only=true"
```

**응답 200**
```json
{
  "operator_id": 1,
  "evaluated_project_count": 134,
  "returned_candidate_count": 2,
  "high_priority_only": true,
  "candidates": [
    {
      "project_id": 9012,
      "title": "○○시 도로포장 보수공사",
      "category": "일반토목공사",
      "budget_estimate": 820000000.0,
      "deadline": "2026-06-10T18:00:00Z",
      "matched_score": 0.81,
      "probability_score": 0.67,
      "priority_score": 0.78,
      "action": "bid_now",
      "recommended_amount": 742000000.0,
      "analysis_summary": "면허/지역 적합, 유사 낙찰 사례 다수",
      "strategy_reasons": ["focus_category match", "region match", "budget in range"]
    },
    {
      "project_id": 9020,
      "title": "△△구 보도블록 정비",
      "category": "일반토목공사",
      "budget_estimate": 310000000.0,
      "deadline": "2026-06-12T18:00:00Z",
      "matched_score": 0.74,
      "probability_score": 0.6,
      "priority_score": 0.7,
      "action": "review",
      "recommended_amount": 286000000.0,
      "analysis_summary": "확률 보통, 검토 권장",
      "strategy_reasons": ["keyword match: 포장"]
    }
  ],
  "computed_at": "2026-07-30T02:11:04.512000Z",
  "snapshot_status": "idle",
  "stale": false
}
```

스냅샷 메타 필드:

| 필드 | 타입 | 의미 |
|---|---|---|
| computed_at | string\|null | 마지막 **성공** 계산 시각. 아직 계산된 적 없으면 `null` |
| snapshot_status | `idle`\|`running`\|`failed` | 스냅샷 행 상태. `running`이면 재계산이 진행 중, `failed`면 마지막 재계산이 실패 |
| stale | boolean | 반환된 후보가 낡았는가(시간 초과 또는 계산 이후 전략 수정). `true`면 재계산이 이미 큐잉된 상태다 |

**에러**

| 코드 | 의미 |
|---|---|
| 422 | limit 범위(1~100) 위반 |

---

## POST /api/v1/operator/strategy/candidates/refresh

미리보기 스냅샷 재계산을 **명시적으로** 큐에 넣고 즉시 `202`로 반환한다. 결과는 별도 task-status 없이 `GET /strategy/candidates`를 재조회해 `snapshot_status`/`computed_at`으로 확인한다. 사용자가 "새로고침"을 눌렀을 때, 자동 재큐잉이 실패 쿨다운으로 억제된 상태에서 즉시 재시도할 때, 또는 재계산이 SIGKILL/재시작으로 `running`에 고착된 미리보기를 복구할 때 사용한다.

- 인증: 선택. target operator 규칙은 `GET /strategy/candidates`와 동일하다.
- 도메인: 단일비행 — 이미 재계산이 실행 중이면 새 task를 만들지 않고 그 task를 재사용한다(새로고침 연타가 스캔을 중복 실행하지 못한다). 자동 큐잉과 달리 실패 쿨다운을 우회하고, 자동 회수창(기본 300s)보다 짧은 force floor(`OPERATOR_PREVIEW_SNAPSHOT_FORCE_RECLAIM_SECONDS`, 기본 60s)를 넘긴 `running` 고아를 회수해 wedged 미리보기를 즉시 복구한다 — 단, force floor 안쪽의 갓 시작한 스캔은 그대로 재사용해 연타 스탬피드를 막는다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | high_priority_only | boolean\|null | 아니오 | 재계산할 스냅샷 키. 생략 시 전략 기본값 |
| query | operator_id | integer\|null | 아니오 | target 운영자 id. 생략 시 토큰 소유자, 토큰이 없으면 canonical `operator` |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/operator/strategy/candidates/refresh?high_priority_only=false" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 202**
```json
{
  "task_id": "9f2a7c31-4b8e-4c21-9d55-6a0f1e2b3c4d",
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "high_priority_only": false,
  "snapshot_status": "running",
  "detail": "미리보기 재계산을 큐에 등록했습니다.",
  "poll_url": "/api/v1/operator/strategy/candidates"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | privileged 호출자가 지정한 `operator_id`가 존재하지 않음 |

---

## POST /api/v1/operator/strategy/monitor

저장된 target 운영자 전략 실행을 **큐에 넣고 `202`로 즉시 반환**한다. 실행은 워커에서 매칭 후보를 평가하고, 각 후보의 입찰 판단(`BidDecisionRecord`)을 target 운영자에 영속화하며 운영자 알림을 생성한다. "지금 한 번 돌려" 동작에 사용. **부수효과가 있으므로** 미리보기(`/strategy/candidates`)와 구분된다.

- 인증: 선택. 토큰 없음 + `operator_id` 생략은 canonical 운영자로 실행한다. 토큰 없음 + canonical이 아닌 `operator_id`는 `403`; 토큰이 있어도 non-privileged 사용자의 cross-operator target은 `403`.
- 실행 모델: **동기 실행이 아니다.** 요청 경로 인라인 ML을 없애기 위해 이 경로는 `POST /strategy/monitor/async`와 같은 구현에 위임한다 — 응답은 async envelope이고, 최종 결과는 `poll_url`(`GET /strategy/monitor/tasks/{task_id}`) 폴링 또는 `GET /strategy/monitor/runs`로 읽는다. 운영자 트리거 실행의 `trigger_source`는 `manual_async`다.
- 도메인: 완료된 실행 결과는 이전 실행 대비 신규/연속/탈락 후보를 diff로 집계한다. `same_category_only`/`similar_limit`/`min_similarity`는 pgvector 유사 사례 검색 파라미터. 각 결과는 영속화된 `decision_record_id`와 (생성됐다면) `notification_id`를 포함한다. 결과 페이로드 전체 형태는 [GET /strategy/monitor/tasks/{task_id}](#get-apiv1operatorstrategymonitortaskstask_id)의 `result` 참고.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | operator_id | integer\|null | 아니오 | target 운영자 id. 생략 시 토큰 소유자, 토큰이 없으면 canonical `operator` |
| body | limit | integer\|null | 아니오 | 후보 상한, 1~100 |
| body | high_priority_only | boolean\|null | 아니오 | 고우선순위만 |
| body | max_active_bids | integer | 아니오 | 동시 진행 입찰 상한, >=1 (기본 3) |
| body | current_workload_score | number\|null | 아니오 | 현재 업무 부하, 0~1 |
| body | same_category_only | boolean | 아니오 | 동일 카테고리 유사 사례만 (기본 true) |
| body | similar_limit | integer | 아니오 | 유사 사례 수, 1~10 (기본 3) |
| body | min_similarity | number | 아니오 | 최소 유사도, 0~1 (기본 0.15) |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/operator/strategy/monitor?operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "limit": 20,
    "high_priority_only": false,
    "max_active_bids": 3,
    "same_category_only": true,
    "similar_limit": 3,
    "min_similarity": 0.15
  }'
```

**응답 202**
```json
{
  "task_id": "b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b",
  "monitor_run_id": 58,
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "task_name": "jobs.monitor_operator_strategy",
  "status": "queued",
  "detail": "작업이 큐에 등록되었습니다.",
  "poll_url": "/api/v1/operator/strategy/monitor/tasks/b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b"
}
```

실행 결과(평가 건수·후보 diff·`decision_record_id` 등)는 이 응답에 없다. `poll_url`을 폴링해 `status="completed"`가 된 뒤 `result`에서 읽거나, `GET /strategy/monitor/runs/{monitor_run_id}`로 조회한다.

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | privileged 호출자가 지정한 `operator_id`가 존재하지 않음 |
| 422 | 요청 필드 범위 위반 |

---

## GET /api/v1/operator/strategy/monitor/runs

target 운영자의 최근 전략 모니터링 실행 이력을 최신순으로 반환한다. 모니터링 결과 추적 또는 실패 실행 조회에 사용한다(대시보드의 "모니터링 실패" 카드가 `?status=failed`로 링크).

- 인증: 선택. 토큰 없음 + `operator_id` 생략은 canonical 운영자 이력을 반환한다. 토큰 없음 + canonical이 아닌 `operator_id`는 `403`; non-privileged 사용자의 cross-operator 조회도 `403`.
- 도메인: `trigger_source`는 실행 출처(동기/비동기 등). `status` 쿼리로 상태별 필터.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer | 아니오 | 반환 수, 1~100 (기본 20) |
| query | status | string\|null | 아니오 | 상태 필터(queued/running/completed/failed/cancelled) |
| query | operator_id | integer\|null | 아니오 | target 운영자 id. 생략 시 토큰 소유자, 토큰이 없으면 canonical `operator` |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/strategy/monitor/runs?limit=20&status=failed&operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 200**
```json
{
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "result_count": 1,
  "runs": [
    {
      "id": 57,
      "operator_id": 11,
      "current_operator_id": 11,
      "current_operator_username": "synthetic-aggressive",
      "task_id": null,
      "trigger_source": "operator.sync",
      "status": "failed",
      "high_priority_only": false,
      "limit_applied": 20,
      "evaluated_project_count": 0,
      "selected_candidate_count": 0,
      "persisted_candidate_count": 0,
      "notification_count": 0,
      "error_message": "predictor timeout",
      "created_at": "2026-05-28T03:00:00Z",
      "started_at": "2026-05-28T03:00:01Z",
      "completed_at": "2026-05-28T03:00:31Z"
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | privileged 호출자가 지정한 `operator_id`가 존재하지 않음 |
| 422 | limit 범위(1~100) 위반 |

---

## GET /api/v1/operator/strategy/monitor/runs/{run_id}

target 운영자에 속한 단일 모니터링 실행의 전체 상세를 반환한다. 실행 요약뿐 아니라 요청 페이로드, 전체 결과 객체, 이전 실행 대비 신규/연속/탈락 후보 목록까지 펼쳐 보여준다. 이력에서 한 건을 클릭해 상세를 볼 때 사용.

- 인증: 선택. `run_id`가 존재해도 resolved target 운영자 소유가 아니면 상세를 반환하지 않는다.
- 도메인: `result`는 해당 실행의 `OperatorStrategyMonitorResponse` 전체, `new_/continuing_/dropped_candidates`는 후보 diff 목록.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | run_id | integer | 예 | 모니터 실행 id |
| query | operator_id | integer\|null | 아니오 | target 운영자 id. 생략 시 토큰 소유자, 토큰이 없으면 canonical `operator` |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/strategy/monitor/runs/57?operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 200**
```json
{
  "id": 57,
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "task_id": null,
  "trigger_source": "operator.sync",
  "status": "completed",
  "high_priority_only": false,
  "limit_applied": 20,
  "evaluated_project_count": 134,
  "selected_candidate_count": 5,
  "persisted_candidate_count": 5,
  "notification_count": 2,
  "error_message": null,
  "created_at": "2026-05-28T03:00:00Z",
  "started_at": "2026-05-28T03:00:01Z",
  "completed_at": "2026-05-28T03:00:09Z",
  "previous_run_id": 56,
  "new_candidate_count": 2,
  "continuing_candidate_count": 3,
  "dropped_candidate_count": 1,
  "request_payload": {
    "limit": 20,
    "high_priority_only": false,
    "max_active_bids": 3,
    "same_category_only": true,
    "similar_limit": 3,
    "min_similarity": 0.15
  },
  "result": {
    "operator_id": 11,
    "current_operator_id": 11,
    "current_operator_username": "synthetic-aggressive",
    "evaluated_project_count": 134,
    "selected_candidate_count": 5,
    "persisted_candidate_count": 5,
    "notification_count": 2,
    "new_candidate_count": 2,
    "continuing_candidate_count": 3,
    "dropped_candidate_count": 1,
    "high_priority_only": false,
    "limit_applied": 20
  },
  "new_candidates": [
    {
      "project_id": 9012,
      "title": "○○시 도로포장 보수공사",
      "decision_record_id": 312,
      "notification_id": 451,
      "action": "bid_now",
      "decision_status": "planned",
      "priority_score": 0.78,
      "probability_score": 0.67,
      "matched_score": 0.81,
      "recommended_amount": 742000000.0,
      "analysis_summary": "면허/지역 적합",
      "is_new_candidate": true,
      "notification_created": true,
      "strategy_reasons": ["focus_category match"]
    }
  ],
  "continuing_candidates": [],
  "dropped_candidates": []
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | 해당 target의 run_id 실행 없음 또는 privileged 호출자가 지정한 `operator_id` 없음 |
| 422 | run_id가 정수가 아님 |

---

## POST /api/v1/operator/strategy/monitor/async

전략 모니터링을 비동기로 큐에 넣고, 진행 상황을 폴링할 수 있는 task id와 poll URL을 즉시 반환한다. `POST /strategy/monitor`가 이 구현에 위임하므로 두 경로의 응답은 같다 — 이쪽은 큐잉 의도가 경로에 드러나는 명시적 별칭이다.

- 인증: 선택. target operator 규칙은 `POST /strategy/monitor`와 동일하다.
- 도메인: 먼저 `queued` 상태의 monitor_run을 만들고 Celery 작업을 enqueue한다. memory broker 환경에서는 eager 실행되어 즉시 완료될 수 있다. 요청 본문은 `POST /strategy/monitor`와 동일.

**파라미터**

`OperatorStrategyMonitorRequest` (위 `POST /strategy/monitor`의 body 파라미터와 동일) + query `operator_id`.

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/operator/strategy/monitor/async?operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"limit": 20, "same_category_only": true}'
```

**응답 200**
```json
{
  "task_id": "b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b",
  "monitor_run_id": 58,
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "task_name": "jobs.monitor_operator_strategy",
  "status": "queued",
  "detail": "작업이 큐에 등록되었습니다.",
  "poll_url": "/api/v1/operator/strategy/monitor/tasks/b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | privileged 호출자가 지정한 `operator_id`가 존재하지 않음 |
| 422 | 요청 필드 범위 위반 |

---

## GET /api/v1/operator/strategy/monitor/tasks/{task_id}

비동기 모니터링 작업의 현재 상태와 (완료 시) 최종 결과를 조회한다. `async` 호출로 받은 `poll_url`을 주기적으로 폴링해 완료를 확인할 때 사용한다.

- 인증: 선택. target operator 규칙은 `POST /strategy/monitor`와 동일하다.
- 도메인: `ready`/`successful`로 완료·성공 여부를 판별하고, 성공 시 `result`에 `OperatorStrategyMonitorResponse`가 채워진다. `task_id`가 다른 operator 소유이면 `404`로 숨긴다. 실패 시 `error`에 사유.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | task_id | string | 예 | async 호출이 반환한 task id |
| query | operator_id | integer\|null | 아니오 | target 운영자 id. 생략 시 토큰 소유자, 토큰이 없으면 canonical `operator` |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/strategy/monitor/tasks/b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b?operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 200**
```json
{
  "task_id": "b3f1c2a4-5e6d-47a8-9b01-2c3d4e5f6a7b",
  "monitor_run_id": 58,
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-aggressive",
  "task_name": "jobs.monitor_operator_strategy",
  "status": "completed",
  "raw_status": "SUCCESS",
  "ready": true,
  "successful": true,
  "detail": "작업이 완료되었습니다.",
  "error": null,
  "result": {
    "operator_id": 11,
    "current_operator_id": 11,
    "current_operator_username": "synthetic-aggressive",
    "evaluated_project_count": 134,
    "selected_candidate_count": 5,
    "persisted_candidate_count": 5,
    "notification_count": 2,
    "new_candidate_count": 2,
    "continuing_candidate_count": 3,
    "dropped_candidate_count": 1,
    "high_priority_only": false,
    "limit_applied": 20
  }
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 제공됐으나 유효하지 않음 |
| 403 | 무인증 또는 non-privileged 호출자가 다른 운영자 `operator_id`를 target으로 지정 |
| 404 | `task_id`가 해당 target 운영자 소유가 아니거나 privileged 호출자가 지정한 `operator_id` 없음 |
| 422 | 경로/쿼리 파라미터 검증 실패 |

---

## GET /api/v1/operator/dashboard

웹 대시보드 메인 화면을 한 번의 호출로 채우는 종합 페이로드를 반환한다. 개요 지표, 카드 5종, 최근 입찰 판단, 최근 모니터링 실행, 예측 피드백 요약, 각 영역으로 가는 링크 맵을 포함한다.

- 인증: 불필요(단일 운영자).
- 도메인: `cards`는 고정 키 5종(profile_configured / active_bid_decisions / unread_notifications / monitor_failures / recommendation_error_rate). `recommendation_error_rate` 카드 status는 오차율 임계로 매핑(≤0.03 healthy, ≤0.08 watch, 그 외 critical, 데이터 없음 info). `recent_decisions`는 `BidDecisionRecord`를 updated_at 내림차순 limit개.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer | 아니오 | 집계 기간(일), 1~365 (기본 30) |
| query | limit | integer | 아니오 | 최근 항목 수, 1~20 (기본 5) |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/dashboard?days=30&limit=5"
```

**응답 200**
```json
{
  "operator_id": 1,
  "generated_at": "2026-05-29T01:00:00Z",
  "period_days": 30,
  "overview": {
    "operator_id": 1,
    "project_count": 1280,
    "bid_count": 42,
    "active_bid_count": 5,
    "prediction_count": 318,
    "unread_notification_count": 3,
    "recent_event_count": 96,
    "profile_configured": true
  },
  "cards": [
    {"key": "profile_configured", "label": "전략 프로필", "value": 1, "unit": "state", "status": "healthy", "detail": "운영자 프로필이 설정되었습니다.", "href": "/api/v1/operator/profile"},
    {"key": "active_bid_decisions", "label": "진행 중 판단", "value": 5, "unit": "count", "status": "info", "detail": "planned/reviewing 상태의 입찰 판단 수입니다.", "href": "/api/v1/operations/bid-decisions"},
    {"key": "unread_notifications", "label": "미확인 알림", "value": 3, "unit": "count", "status": "watch", "detail": "웹 대시보드에서 확인할 알림 수입니다.", "href": "/api/v1/operator/notifications"},
    {"key": "monitor_failures", "label": "모니터링 실패", "value": 0, "unit": "count", "status": "healthy", "detail": "최근 30일 내 실패한 전략 모니터링 실행 수입니다.", "href": "/api/v1/operator/strategy/monitor/runs?status=failed"},
    {"key": "recommendation_error_rate", "label": "추천 오차율", "value": 0.041, "unit": "ratio", "status": "watch", "detail": "낙찰 결과가 연결된 추천 금액의 평균 절대 오차율입니다.", "href": "/api/v1/analytics/prediction-feedback"}
  ],
  "recent_decisions": [
    {
      "decision_record_id": 312,
      "project_id": 9012,
      "project_title": "○○시 도로포장 보수공사",
      "action": "bid_now",
      "decision_status": "planned",
      "priority_score": 0.78,
      "probability_score": 0.67,
      "recommended_amount": 742000000.0,
      "updated_at": "2026-05-28T03:00:09Z",
      "detail_href": "/api/v1/operations/bid-decisions/312",
      "analysis_href": "/api/v1/operations/opportunity-analysis"
    }
  ],
  "recent_monitor_runs": [
    {
      "monitor_run_id": 57,
      "status": "completed",
      "trigger_source": "operator.sync",
      "persisted_candidate_count": 5,
      "notification_count": 2,
      "created_at": "2026-05-28T03:00:00Z",
      "completed_at": "2026-05-28T03:00:09Z",
      "detail_href": "/api/v1/operator/strategy/monitor/runs/57"
    }
  ],
  "feedback_summary": {
    "result_count": 24,
    "prediction_sample_count": 24,
    "recommendation_sample_count": 20,
    "average_prediction_error_rate": 0.052,
    "average_recommendation_error_rate": 0.041,
    "recommendation_better_than_prediction_count": 14,
    "href": "/api/v1/analytics/prediction-feedback"
  },
  "action_hrefs": {
    "opportunity_analysis": "/api/v1/operations/opportunity-analysis",
    "decision_list": "/api/v1/operations/bid-decisions",
    "strategy_candidates": "/api/v1/operator/strategy/candidates",
    "strategy_monitor": "/api/v1/operator/strategy/monitor",
    "strategy_monitor_runs": "/api/v1/operator/strategy/monitor/runs",
    "prediction_feedback": "/api/v1/analytics/prediction-feedback",
    "operations_dashboard": "/api/v1/analytics/operations-dashboard"
  }
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | days(1~365)/limit(1~20) 범위 위반 |

---

## GET /api/v1/operator/overview

대시보드 상단에 쓰이는 컴팩트한 단일 사용자 요약 지표를 반환한다. 전체 프로젝트 수, 운영자의 입찰/진행 중 입찰/예측 수, 미확인 알림 수, 최근 활동 이벤트 수를 한눈에 본다.

- 인증: 불필요(단일 운영자).
- 도메인: `active_bid_count`는 status가 submitted/reviewed인 입찰. `recent_event_count`는 최근 days일 활동이되 내부 텔레그램 이벤트(`telegram.delivery`, `telegram.strategy.pending_edit`)는 제외한다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer | 아니오 | 집계 기간(일), 1~90 (기본 7) |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/overview?days=7"
```

**응답 200**
```json
{
  "operator_id": 1,
  "project_count": 1280,
  "bid_count": 42,
  "active_bid_count": 5,
  "prediction_count": 318,
  "unread_notification_count": 3,
  "recent_event_count": 21,
  "profile_configured": true
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | days(1~90) 범위 위반 |

---

## GET /api/v1/operator/accounts

현재 bearer-token 소유자가 볼 수 있는 운영자 계정 목록을 반환한다. 관리자 surface의 operator switcher에서 사용한다.

- 인증: Bearer 토큰은 선택이다. 토큰이 없으면 canonical `operator` 기준으로 동작한다.
- 도메인: canonical `operator` 또는 admin은 canonical, synthetic, 본인 계정을 볼 수 있다. 일반 운영자는 자기 계정만 받는다.

**파라미터**

(없음)

**요청 예시**
```bash
curl http://localhost:3000/api/v1/operator/accounts \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 200**
```json
{
  "current_operator_id": 1,
  "current_operator_username": "operator",
  "is_privileged": true,
  "operator_count": 2,
  "operators": [
    {
      "operator_id": 1,
      "username": "operator",
      "company": "가나다건설",
      "is_synthetic": false,
      "is_active": true,
      "profile_configured": true
    },
    {
      "operator_id": 11,
      "username": "synthetic-sw-small-seoul",
      "company": "서울 소프트웨어 테스트",
      "is_synthetic": true,
      "is_active": true,
      "profile_configured": true
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 401 | Bearer 토큰이 잘못됨 |

---

## GET /api/v1/operator/notification-channels

해결된 operator context의 알림 채널 메타데이터를 masked 형태로 반환한다. G-2 운영 검증에서 사업자별 Telegram/app 알림 대상이 분리되어 있는지 확인할 때 사용한다.

- 인증: Bearer 토큰은 선택이다. 토큰이 없으면 canonical `operator` 기준으로 동작한다.
- 도메인: `target_label`은 raw chat id나 secret target을 노출하지 않는 masked 값이다. canonical legacy Telegram 설정만 있는 경우에는 `source=legacy_settings` 항목으로 노출한다. `operator_notification_channels` row가 있으면 해당 row의 `dry_run_only`, `is_active`, `verified_at` 상태를 반환한다.
- 권한: canonical `operator` 또는 admin만 `?operator_id=`로 다른 운영자 채널을 조회할 수 있다. 일반 운영자의 cross-operator 조회는 `403`이다.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | operator_id | integer | 아니오 | 조회할 target operator id. privileged 호출에서만 다른 operator 조회 가능 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/notification-channels?operator_id=11" \
  -H "Authorization: Bearer <ACCESS_TOKEN>"
```

**응답 200**
```json
{
  "operator_id": 11,
  "current_operator_id": 11,
  "current_operator_username": "synthetic-sw-small-seoul",
  "channel_count": 1,
  "channels": [
    {
      "channel_id": 7,
      "operator_id": 11,
      "channel_type": "telegram",
      "route_key": "telegram:synthetic-sw-small-seoul",
      "target_label": "chat ********0346",
      "is_active": true,
      "dry_run_only": true,
      "source": "operator_notification_channels",
      "verified_at": null,
      "created_at": "2026-06-19T02:10:00Z",
      "updated_at": "2026-06-19T02:10:00Z"
    }
  ]
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 403 | 다른 operator 채널을 조회할 권한이 없음 |
| 404 | operator_id에 해당하는 운영자가 없음 |
| 422 | operator_id가 1 이상 정수가 아님 |

---

## GET /api/v1/operator/notifications

싱글톤 운영자의 웹 대시보드용 최근 알림 목록을 최신순으로 반환한다. 알림 패널/벨 아이콘 목록을 채울 때 사용한다.

- 인증: 불필요(단일 운영자).
- 도메인: `unread_only=true`면 미확인만, `notification_type`이 주어지면 `Notification.type` 정확 일치 필터. created_at 내림차순.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer | 아니오 | 반환 수, 1~100 (기본 20) |
| query | unread_only | boolean | 아니오 | 미확인만 (기본 false) |
| query | notification_type | string\|null | 아니오 | 알림 타입 정확 일치 필터 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/operator/notifications?limit=20&unread_only=true"
```

**응답 200**
```json
[
  {
    "id": 451,
    "title": "신규 입찰 후보",
    "message": "○○시 도로포장 보수공사가 전략에 매칭되었습니다.",
    "type": "strategy.candidate",
    "is_read": false,
    "created_at": "2026-05-28T03:00:09Z"
  }
]
```

**에러**

| 코드 | 의미 |
|---|---|
| 422 | limit 범위(1~100) 위반 |

---

## PUT /api/v1/operator/notifications/{notification_id}/read

지정한 알림을 읽음 처리하고 갱신된 알림을 반환한다. 알림 목록에서 한 건을 읽음 표시할 때 사용한다.

- 인증: 불필요(단일 운영자).
- 도메인: 해당 알림은 운영자 본인 소유여야 한다(`Notification.user_id == operator.id`). 읽음 처리는 `OperatorNotificationService.mark_as_read`에 위임.

**파라미터**

| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | notification_id | integer | 예 | 읽음 처리할 알림 id |

**요청 예시**
```bash
curl -X PUT http://localhost:3000/api/v1/operator/notifications/451/read
```

**응답 200**
```json
{
  "id": 451,
  "title": "신규 입찰 후보",
  "message": "○○시 도로포장 보수공사가 전략에 매칭되었습니다.",
  "type": "strategy.candidate",
  "is_read": true,
  "created_at": "2026-05-28T03:00:09Z"
}
```

**에러**

| 코드 | 의미 |
|---|---|
| 404 | 해당 id 알림이 없거나 운영자 소유가 아님 |
| 422 | notification_id가 정수가 아님 |
