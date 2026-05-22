# Paper Bidding Test Plan

작성일: 2026-05-21

## 목적

`bid-vector`가 실제 입찰에 참여했을 때 낙찰 가능성이 있는지 검증하기 위해, 실제 나라장터 투찰 없이 **가상 입찰 업체가 실제 진행 중인 입찰건에 페이퍼로 참여하는 테스트**를 운영한다.

이 테스트의 목표는 단순히 예측가가 낙찰가와 가까운지 보는 것이 아니라, 다음 질문에 답하는 것이다.

- 가상 업체가 실제로 참여 가능한 공고를 올바르게 선별하는가?
- 공고 마감 전에 만든 투찰 판단과 가상 투찰금액이 실제 낙찰 결과와 얼마나 유사한가?
- 가격 기준으로 보았을 때 실제 입찰에 참여했다면 낙찰 가능성이 있었는가?
- 어떤 전략, 업종, 발주처, 예산 구간에서 낙찰 가능성이 높거나 낮은가?

## 핵심 결론

추천 방식은 **Shadow Bidding / Paper Bidding**이다.

실제 나라장터에 투찰하지 않고, 공고 마감 전 시점에만 볼 수 있는 정보로 `참여 여부`, `가상 투찰금액`, `전략/모델 버전`, `판단 근거`를 고정 저장한다. 이후 실제 개찰/낙찰 결과가 수집되면 가상 투찰 기록과 비교해 낙찰 가능률과 가격 오차를 계산한다.

## 안전 원칙

1. 실제 투찰은 하지 않는다.
   - 첫 검증 단계에서는 실제 나라장터 제출을 금지한다.
   - 모든 기록은 내부 DB의 가상 투찰 기록으로만 남긴다.

2. 마감 전 정보만 사용한다.
   - 가상 투찰금액을 만든 시점 이후의 `TenderResult`, 낙찰금액, 개찰 결과, 경쟁사 정보는 모델 입력에서 제외한다.
   - 각 판단에는 `data_cutoff_at`을 저장한다.

3. 판단 결과는 수정하지 않는다.
   - `paper_bid_amount`, `strategy_version`, `model_version`, `reasoning`은 생성 후 잠근다.
   - 이후 전략을 바꾸면 기존 기록을 수정하지 않고 새 전략 버전으로 새 기록을 만든다.

4. 낙찰 판정은 보수적으로 분리한다.
   - `낙찰가와 근접함`과 `실제로 낙찰받았을 것`은 다르다.
   - 적격심사, 협상, 종합평가, 자격요건 등 가격 외 요소가 불명확하면 `would_have_won=unknown`으로 둔다.

## 현재 코드베이스에서 사용할 수 있는 기반

현재 repo에는 페이퍼 입찰 테스트에 필요한 기반 기능이 이미 있다.

- 공고/개찰/낙찰 수집: `POST /api/v1/operations/crawl`
- 전략 후보 선별: `GET /api/v1/operator/strategy/candidates`
- 전략 실행 및 판단 저장: `POST /api/v1/operator/strategy/monitor`
- 입찰 판단 저장/조회: `POST/GET /api/v1/operations/bid-decisions`
- 가격 예측: `POST /api/v1/predictions/price`
- 투찰 기록: `POST /api/v1/bids/`
- 낙찰 결과 피드백: `GET /api/v1/analytics/prediction-feedback`
- 대시보드 결과 확인: `/dashboard`, `/api/v1/dashboard/*`

단, 현재 `Bid`는 실제 제출에 가까운 의미이므로, 실전 검증에는 별도 `paper_bid_*` 도메인을 추가하는 것이 안전하다.

## 테스트 대상 정의

### 가상 업체 프로필

테스트 시작 전에 가상 입찰 참여 업체의 조건을 고정한다.

- 업종
- 면허/자격 코드
- 참여 가능 지역
- 제외 지역
- 관심 키워드
- 제외 키워드
- 최소/최대 예산
- 수행 가능 규모
- 최대 동시 입찰 수
- 보수/기준/공격 투찰 정책

이 값은 `operator_profile`, `operator_strategy`에 저장하고, 테스트 기간 중 변경 시 `strategy_version`을 올린다.

### 참여 가능한 입찰건

아래 조건을 모두 만족하는 공고만 테스트 대상으로 본다.

- 상태가 `open` 또는 `re_notice`
- 마감 전
- 가상 업체의 업종/면허/지역 조건 충족
- 예산 범위 충족
- 제외 키워드/제외 발주처 규칙에 걸리지 않음
- 공고 정보가 낙찰 결과와 아직 연결되지 않음

## 데이터 모델 제안

### `paper_bid_runs`

페이퍼 입찰 실행 단위.

| 필드 | 의미 |
| --- | --- |
| `id` | 실행 ID |
| `strategy_version` | 전략 버전 |
| `model_version` | 모델 버전 |
| `data_cutoff_at` | 사용 가능한 데이터 기준 시각 |
| `started_at` | 실행 시작 |
| `completed_at` | 실행 종료 |
| `status` | `running`, `completed`, `failed` |
| `candidate_count` | 후보 공고 수 |
| `paper_bid_count` | 가상 투찰 수 |
| `notes` | 실행 메모 |

### `paper_bids`

공고별 가상 투찰 기록.

| 필드 | 의미 |
| --- | --- |
| `id` | 가상 투찰 ID |
| `run_id` | `paper_bid_runs.id` |
| `project_id` | 연결 프로젝트 |
| `notice_number` | 공고 번호 |
| `decision_record_id` | 기존 판단 기록 연결 |
| `action` | `bid_now`, `review`, `skip` |
| `decision_status` | `planned`, `reviewing`, `submitted`, `skipped` |
| `paper_bid_amount` | 가상 투찰금액 |
| `paper_bid_rate` | 기초/예정가 대비 투찰률 |
| `scenario` | `conservative`, `base`, `aggressive` |
| `priority_score` | 우선순위 |
| `probability_score` | 낙찰 가능성 |
| `eligibility_snapshot` | 자격 판정 스냅샷 JSON |
| `input_snapshot` | 모델 입력 스냅샷 JSON |
| `reasoning` | 판단 근거 |
| `decided_at` | 판단 시각 |
| `locked` | 수정 금지 여부 |

### `paper_bid_settlements`

개찰/낙찰 결과 수집 후 정산 기록.

| 필드 | 의미 |
| --- | --- |
| `id` | 정산 ID |
| `paper_bid_id` | 가상 투찰 ID |
| `tender_result_id` | 실제 낙찰 결과 |
| `winning_amount` | 실제 낙찰금액 |
| `winning_rate` | 실제 낙찰률 |
| `winning_company` | 실제 낙찰 업체 |
| `absolute_error_amount` | 절대 금액 오차 |
| `absolute_error_rate` | 절대 오차율 |
| `signed_error_rate` | 방향성 포함 오차율 |
| `within_0_1pct` | 0.1% 이내 여부 |
| `within_0_3pct` | 0.3% 이내 여부 |
| `within_1pct` | 1% 이내 여부 |
| `price_compatible` | 가격상 유효 가능 여부 |
| `would_have_won_price_only` | 가격 기준 승리 가능 여부 |
| `would_have_won_confidence` | `high`, `medium`, `low`, `unknown` |
| `settled_at` | 정산 시각 |

## 평가 지표

### 데이터 수집 지표

- `candidate_count`: 참여 가능 후보 수
- `paper_bid_count`: 실제 가상 투찰 수
- `result_link_count`: 실제 낙찰 결과와 연결된 수
- `result_link_rate = result_link_count / paper_bid_count`

### 가격 정확도 지표

- `absolute_error_rate = abs(paper_bid_amount - winning_amount) / winning_amount`
- `signed_error_rate = (paper_bid_amount - winning_amount) / winning_amount`
- `within_0_1pct_rate`
- `within_0_3pct_rate`
- `within_1pct_rate`
- 평균 절대 오차율
- 중앙값 절대 오차율

### 낙찰 가능성 지표

- `price_compatible_rate`
- `would_have_won_price_only_rate`
- `conservative_win_rate`
- `base_win_rate`
- `aggressive_win_rate`
- 업종별 낙찰 가능률
- 발주처별 낙찰 가능률
- 예산 구간별 낙찰 가능률

### 운영 품질 지표

- 마감 전 판단 성공률
- 중복 공고 처리율
- 결과 연결 지연 시간
- Telegram/대시보드 알림 성공률
- 후보는 많지만 투찰이 적은 사유
- 투찰은 많지만 결과 근접도가 낮은 사유

## 판정 규칙

### 결과 연결 대상

정산에는 실제 낙찰 결과로 볼 수 있는 `TenderResult`만 사용한다.

- `winning_amount > 0`
- `result_status`가 `awarded`, `closed`, `낙찰`, `계약완료` 계열
- 공고 번호, 프로젝트, 발주처, 제목, 예산/마감 정보가 안전하게 연결됨

개찰 대기, 유찰, 무응찰, 낙찰금액 0원, 결과 미확정 스냅샷은 정산에서 제외한다.

### 승리 가능성 등급

| 등급 | 의미 |
| --- | --- |
| `high` | 가격 중심 입찰이고 가상 투찰가가 유효 범위 내에서 실제 낙찰가와 매우 근접 |
| `medium` | 가격 기준으로는 유리하나 자격/평가 요소 불확실성 존재 |
| `low` | 낙찰가와 거리가 크거나 유효 범위 밖 |
| `unknown` | 낙찰 방식/자격/평가 정보 부족으로 판단 불가 |

## 실행 단계

### Phase 0. 준비 및 기준선 확정

목표: 실전 테스트 시작 전에 가상 업체와 전략 기준을 고정한다.

작업:

1. 운영자 프로필을 실제 가상 업체 조건으로 설정한다.
2. 관심 업종, 지역, 키워드, 예산 범위를 `operator_strategy`에 설정한다.
3. `PRICE_PREDICTION_PREFERRED_PREDICTOR` 값을 결정한다.
4. Telegram 알림과 대시보드 접근이 정상인지 확인한다.
5. 현재 DB를 백업하되, 백업 파일은 Docker 이미지에 포함되지 않도록 `backups/` 아래에 둔다.

완료 기준:

- `/api/v1/operator/profile`과 `/api/v1/operator/strategy`가 테스트 기준값을 반환한다.
- `/health`가 200을 반환한다.
- `/dashboard` 접속이 가능하다.
- Telegram 테스트 메시지가 도착한다.

### Phase 1. 과거 데이터 백테스트

목표: 모델이 낙찰가 근처의 가격을 만들 수 있는지 빠르게 확인한다.

작업:

1. `koneps-scsbid`로 과거 낙찰 결과를 수집한다.
2. `HistoricalData`, `TenderResult`, `Project` 연결 상태를 점검한다.
3. 과거 공고별로 결과를 가린 상태의 입력 스냅샷을 만든다.
4. 가격 예측과 입찰 판단을 재현한다.
5. 실제 `winning_amount`와 비교한다.

주의:

- 이 단계는 결과 데이터 누수 위험이 있으므로 참고용이다.
- 실전 성능 판단은 Phase 2 이후 데이터를 기준으로 한다.

완료 기준:

- 최소 50건 이상 정산 가능 샘플 확보
- 평균 절대 오차율 산출
- 업종/발주처/예산 구간별 약점 구간 확인

### Phase 2. 실시간 Paper Bidding

목표: 앞으로 뜨는 실제 진행 중 공고에 대해 마감 전에 가상 투찰을 기록한다.

작업:

1. 매일 정해진 시간에 KONEPS 열린 공고를 수집한다.
2. 전략 후보 선별을 실행한다.
3. `bid_now` 후보 또는 사전에 정한 `review` 후보에 대해 가상 투찰을 생성한다.
4. `paper_bids`에 판단과 투찰금액을 잠금 저장한다.
5. 마감 후 개찰/낙찰 결과를 주기적으로 수집한다.
6. 결과가 연결되면 `paper_bid_settlements`를 생성한다.

권장 주기:

- 공고 수집: 하루 2-4회
- 전략 모니터링: 공고 수집 직후
- 결과 수집: 하루 1-2회
- 정산 리포트: 매일 1회

완료 기준:

- 최소 4주 운영
- 최소 100건 이상의 가상 투찰 또는 충분한 업종별 샘플 확보
- 결과 연결률과 오차율이 대시보드/리포트로 확인 가능

### Phase 3. 전략 A/B 테스트

목표: 보수/기준/공격 투찰 정책 중 어떤 방식이 가장 좋은지 비교한다.

작업:

1. 같은 후보에 대해 여러 시나리오 금액을 함께 저장한다.
2. 실제 선택 정책은 하나만 `primary`로 지정한다.
3. 나머지는 `shadow_variant`로 저장해 분석에만 사용한다.
4. 결과 정산 시 시나리오별 오차율과 가격 기준 승리 가능률을 비교한다.

권장 시나리오:

- `conservative`: 낮은 리스크, 낮은 투찰가
- `base`: 기준 추천가
- `aggressive`: 낙찰 가능성을 높이기 위한 공격적 금액

완료 기준:

- 시나리오별 30건 이상 결과 연결
- 특정 시나리오가 일관되게 낮은 오차율 또는 높은 가격 기준 승리 가능률을 보임

### Phase 4. 승인 기반 실투찰 전 단계

목표: 자동 실투찰 전에 사람이 승인하는 제한적 운영 절차를 검증한다.

작업:

1. 모델이 `bid_now`로 판단한 후보만 승인 큐에 올린다.
2. 담당자가 공고 원문, 자격, 예산, 납기, 리스크를 확인한다.
3. 실제 투찰 여부는 사람이 결정한다.
4. 실제 투찰한 경우에도 `paper_bid`와 별도 `real_bid`를 구분한다.
5. 실제 결과와 비교해 모델 추천의 의사결정 기여도를 평가한다.

완료 기준:

- 승인 큐의 오탐/누락 원인이 기록됨
- 실제 투찰 전 사람이 확인해야 하는 필수 체크리스트가 안정화됨
- 자동화 가능한 영역과 반드시 사람이 봐야 하는 영역이 분리됨

## 일일 운영 루틴

1. `/health`와 `/api/v1/analytics/operations-dashboard` 확인
2. KONEPS 열린 공고 수집
3. 전략 후보 생성
4. 가상 투찰 생성 및 잠금
5. Telegram/대시보드 알림 확인
6. KONEPS 낙찰 결과 수집
7. 결과 연결 및 정산
8. 일일 리포트 확인

## 주간 리뷰 루틴

1. 전체 가상 투찰 수와 결과 연결률 확인
2. 평균/중앙값 절대 오차율 확인
3. `within_0_3pct_rate`, `within_1pct_rate` 확인
4. 업종/발주처/예산 구간별 성능 확인
5. `skip`했지만 낙찰 가능성이 높았던 공고 분석
6. `bid_now`였지만 오차가 컸던 공고 분석
7. 다음 주 전략 임계치 조정 여부 결정

## 리포트 형식

### 일일 리포트

```text
날짜:
수집 공고 수:
참여 가능 후보 수:
가상 투찰 수:
신규 결과 연결 수:
평균 절대 오차율:
0.3% 이내 건수:
1.0% 이내 건수:
가격 기준 승리 가능 건수:
주요 실패 사유:
다음 액션:
```

### 주간 리포트

```text
기간:
총 후보 수:
총 가상 투찰 수:
결과 연결 수:
결과 연결률:
평균 절대 오차율:
중앙값 절대 오차율:
0.3% 이내 비율:
1.0% 이내 비율:
가격 기준 승리 가능률:
업종별 성능:
발주처별 성능:
예산 구간별 성능:
전략 변경 제안:
실투찰 후보 검토 여부:
```

## 구현 작업 목록

### 1. 데이터 모델 추가

- `PaperBidRun`
- `PaperBid`
- `PaperBidSettlement`

### 2. API 추가

- `POST /api/v1/paper-bidding/runs`
- `GET /api/v1/paper-bidding/runs`
- `GET /api/v1/paper-bidding/runs/{run_id}`
- `POST /api/v1/paper-bidding/runs/{run_id}/settle`
- `GET /api/v1/paper-bidding/settlements`
- `GET /api/v1/paper-bidding/report/daily`
- `GET /api/v1/paper-bidding/report/weekly`

### 3. 서비스 추가

- 후보 선별 서비스
- 가상 투찰 생성 서비스
- 데이터 누수 방지 검증 서비스
- 결과 연결 서비스
- 정산 서비스
- 리포트 서비스

### 4. 스케줄러 추가

- 열린 공고 수집 job
- 전략 후보 생성 job
- 가상 투찰 생성 job
- 낙찰 결과 수집 job
- 정산 job
- 일일 리포트 job

### 5. 테스트 추가

- 마감 전 정보만 사용되는지 검증
- `winning_amount=0` 결과가 정산에서 제외되는지 검증
- 동일 공고 중복 가상 투찰 방지
- 잠긴 가상 투찰 수정 방지
- 결과 연결 후 오차율 계산 검증
- 시나리오별 정산 검증

## 첫 실행 계획

### Day 1

1. 가상 업체 프로필 확정
2. 전략 설정 확정
3. Telegram/대시보드 운영 확인
4. `paper_bid_*` 모델/API 설계 확정

### Day 2-3

1. `PaperBidRun`, `PaperBid`, `PaperBidSettlement` 구현
2. 마이그레이션 또는 schema ensure 함수 추가
3. 기본 API와 단위 테스트 추가

### Day 4-5

1. 가상 투찰 생성 서비스 구현
2. 결과 정산 서비스 구현
3. 일일 리포트 API 구현
4. 회귀 테스트 추가

### Week 2

1. 실제 KONEPS 수집 기반 dry run
2. 매일 가상 투찰 생성
3. 결과 연결 모니터링
4. Telegram 요약 알림 추가

### Week 3-4

1. 최소 표본 100건 목표 운영
2. 업종/발주처/예산 구간별 성능 분석
3. 보수/기준/공격 시나리오 비교
4. 전략 임계치 조정 제안 작성

### Week 5+

1. 사람이 승인하는 실투찰 후보 큐 운영
2. 실제 투찰 전 체크리스트 확정
3. 자동화 가능 범위와 수동 검토 범위 분리
4. 제한적 실투찰 파일럿 여부 결정

## 성공 기준

초기 성공 기준은 다음과 같다.

- 4주 이상 실시간 페이퍼 입찰 운영
- 가상 투찰 100건 이상 또는 핵심 업종별 충분한 샘플 확보
- 결과 연결률 60% 이상
- `within_1pct_rate`가 안정적으로 측정됨
- 가격 기준 승리 가능률이 업종/예산 구간별로 설명 가능
- 실패 원인이 모델 문제, 자격 문제, 데이터 누락, 낙찰 방식 차이 중 어디인지 분류 가능

## 실투찰 전 필수 조건

실제 투찰을 검토하려면 최소한 아래 조건이 충족되어야 한다.

- 페이퍼 입찰 결과가 4주 이상 안정적으로 축적됨
- 데이터 누수 방지 검증이 자동화됨
- 낙찰 결과 연결 품질이 검증됨
- 실투찰 후보에 대해 사람이 자격/공고문/리스크를 최종 확인함
- 투찰금액 산정 근거와 책임 경계가 문서화됨
- 실제 제출은 자동이 아니라 승인 기반으로만 진행됨
