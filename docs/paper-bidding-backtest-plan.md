# Paper Bidding Backtest Plan

작성일: 2026-05-21

## 목적

가상 입찰 참여 업체가 과거 공고에 참여했다고 가정했을 때, 실제 낙찰 결과와 비교해 `bid-vector`의 입찰 선별, 추천금액, 낙찰 가능성을 검증한다.

이 문서는 현재 repo와 DB 상태에서 백테스트가 가능한 범위와, 실제 낙찰률 추정까지 가기 위한 실행 계획을 정리한다.

## 가능 여부 판단

현재 데이터 기준으로 **가격 예측/낙찰가 근접도 백테스트는 바로 가능**하다.

다만 **가상 업체가 실제로 투찰했다면 낙찰됐는지**를 검증하는 완전한 Paper Bidding 백테스트는 현재 그대로는 불완전하다. 이유는 과거 시점의 가상 투찰 판단과 투찰 기록이 아직 저장되어 있지 않기 때문이다.

### 확인한 현재 DB 상태

2026-05-21 기준 로컬 DB에서 확인한 값이다.

| 항목 | 값 | 판단 |
| --- | ---: | --- |
| 전체 프로젝트 | 19,824 | 충분 |
| 진행/재공고 프로젝트 | 304 | 향후 forward paper test 가능 |
| 전체 `HistoricalData` | 19,833 | 충분 |
| `bid_rate > 0` 이력 | 19,624 | 가격 백테스트 가능 |
| 전체 `TenderResult` | 19,831 | 충분 |
| `winning_amount > 0` 낙찰 결과 | 19,620 | 정산 가능한 결과 충분 |
| `project_id` 연결 낙찰 결과 | 19,620 | 공고-결과 비교 가능 |
| 저장된 가격 예측 | 13 | 기존 예측 피드백 표본은 부족 |
| 저장된 입찰 판단 | 0 | 과거 의사결정 백테스트는 재구성 필요 |
| 저장된 실제 투찰 | 0 | 과거 투찰 승패 백테스트는 재구성 필요 |

업종별 정산 가능한 낙찰 결과는 다음과 같다.

| 업종 | 표본 수 | 판단 |
| --- | ---: | --- |
| construction | 9,911 | 백테스트 가능 |
| service | 9,707 | 백테스트 가능 |
| software | 2 | 업종 단독 백테스트 불가 |

## 현재 가능한 백테스트 유형

### 1. 가격 예측 rolling holdout 백테스트

가능 상태: **즉시 가능**

이미 `app/ai/predictor_backtest.py`에 가격 predictor를 과거 `HistoricalData.bid_rate` holdout으로 비교하는 rolling backtest helper가 있다. `PRICE_PREDICTION_PREFERRED_PREDICTOR=auto`를 사용하면 예측 응답에 selector/backtest metadata가 포함된다.

검증 질문:

- 예측 투찰률이 실제 낙찰 투찰률과 얼마나 가까운가?
- 업종별 평균 절대 오차가 얼마인가?
- 최근 holdout에서 어떤 predictor가 가장 낮은 오차를 내는가?

주요 지표:

- `sample_count`
- `average_absolute_error_rate`
- `max_absolute_error_rate`
- `within_0_1pct`, `within_0_3pct`, `within_1pct`
- 업종별, 발주처별, 예산 구간별 오차

### 2. 저장된 예측/추천금액 피드백

가능 상태: **제한적으로 가능**

`GET /api/v1/analytics/prediction-feedback`는 저장된 `PricePrediction`, `BidDecisionRecord.recommended_amount`, 실제 `TenderResult.winning_amount`를 비교한다.

현재 `price_predictions`는 13건이고 `bid_decision_records`는 0건이라 전체 성능 판단에는 부족하다. 이 기능은 forward paper test가 쌓인 뒤 운영 리포트로 쓰는 것이 적합하다.

### 3. 과거 공고 기준 가상 업체 참여 재구성 백테스트

가능 상태: **구현 후 가능**

과거 공고를 시간순으로 되감아, 각 시점에 볼 수 있었던 `Project`, `HistoricalData`만 사용해 가상 업체가 참여할 공고를 선별한다. 이 방식은 저장된 과거 `BidDecisionRecord`가 없어도 실행할 수 있다.

필수 조건:

- 각 테스트 시점마다 `data_cutoff_at`을 강제한다.
- `announced_at`, `winning_amount`, 미래 `HistoricalData`는 입력에서 제외한다.
- 같은 공고의 실제 `TenderResult`는 정산 단계에서만 사용한다.

이 방식으로 알 수 있는 것:

- 가상 업체 조건으로 참여 후보를 얼마나 잘 걸러내는가
- 추천금액이 실제 낙찰금액에 얼마나 근접하는가
- 가격만 보면 낙찰 가능 구간에 들어갔는가

이 방식으로 아직 단정할 수 없는 것:

- 적격심사, 협상, 실적, 지역제한, 공동수급 등 비가격 요소를 포함한 실제 낙찰 여부
- 경쟁사별 실제 투찰 분포에서 우리 금액이 정확히 몇 위였는지

### 4. 실제 낙찰률 추정 Paper Bidding 백테스트

가능 상태: **도메인 추가 구현 후 가능**

실제 승률을 추정하려면 과거 데이터 재구성만으로는 부족하다. 다음 기록을 별도 도메인으로 저장해야 한다.

- `paper_bid_runs`
- `paper_bids`
- `paper_bid_settlements`
- `strategy_version`
- `model_version`
- `data_cutoff_at`
- `paper_bid_amount`
- `paper_bid_rate`
- `would_have_won_price_only`
- `would_have_won_final`
- `settlement_reason`

이 도메인이 생기면 과거 백테스트와 앞으로 진행되는 Shadow Bidding을 같은 리포트 체계로 비교할 수 있다.

## 백테스트 원칙

### 시간 누수 방지

백테스트에서 가장 중요한 조건은 미래 정보를 쓰지 않는 것이다.

각 공고 평가 시점은 다음 중 하나로 잡는다.

- 공고 생성 시점: `Project.created_at`
- 마감 24시간 전: `Project.deadline - interval '24 hours'`
- 마감 2시간 전: `Project.deadline - interval '2 hours'`

모델 입력에는 `data_cutoff_at` 이전 데이터만 포함한다.

제외해야 할 입력:

- 해당 공고의 `TenderResult`
- `data_cutoff_at` 이후 생성된 `HistoricalData`
- `data_cutoff_at` 이후 저장된 `PricePrediction`
- 낙찰업체명, 낙찰금액, 개찰결과

### 정산 가능한 결과 필터

정산에는 실제 낙찰 결과로 볼 수 있는 row만 사용한다.

필수 조건:

- `TenderResult.project_id IS NOT NULL`
- `TenderResult.winning_amount IS NOT NULL`
- `TenderResult.winning_amount > 0`

`winning_amount = 0` 또는 `announced_at IS NULL`인 pending/opening snapshot은 결과 백테스트에서 제외한다. 단, `announced_at`이 없더라도 `winning_amount > 0`이고 신뢰 가능한 결과라면 보조 정산 대상으로 둘 수 있다.

### 업종별 분리

업종별 표본 차이가 크므로 한 번에 평균을 내면 성능이 왜곡된다.

1차 대상:

- `construction`
- `service`

보류 대상:

- `software`: 현재 낙찰 결과 2건이라 통계적 판단 불가

### 가격 승리 판정

실제 조달 낙찰은 가격만으로 결정되지 않는다. 그래서 판정은 단계별로 나눈다.

| 판정 | 의미 |
| --- | --- |
| `price_close` | 실제 낙찰금액과 일정 오차 이내 |
| `price_competitive` | 낙찰률/기초금액 기준으로 경쟁 가능한 구간 |
| `would_have_won_price_only` | 가격만 보면 낙찰 가능성 있음 |
| `would_have_won_final` | 비가격 요건까지 반영한 최종 추정 |
| `unknown` | 자격/심사 정보 부족으로 단정 불가 |

초기 버전에서는 `would_have_won_final`을 과도하게 채우지 않고, 대부분 `unknown`을 허용한다.

## 실행 계획

### 0단계. 데이터 감사

목표: 백테스트 대상 기간과 업종별 표본 수를 고정한다.

실행 항목:

1. `HistoricalData`, `TenderResult`, `Project` 연결률을 확인한다.
2. `winning_amount > 0` 결과만 정산 대상으로 필터링한다.
3. 업종, 발주처, 예산 구간별 표본 수를 집계한다.
4. `announced_at`, `opened_at`, `deadline`, `created_at`의 시간 순서 이상치를 찾는다.
5. 중복 `TenderResult`가 있는 프로젝트는 최신 usable award row만 선택한다.

예시 SQL:

```sql
SELECT COUNT(*) FROM historical_data WHERE bid_rate > 0;

SELECT COUNT(*)
FROM tender_results
WHERE project_id IS NOT NULL
  AND winning_amount IS NOT NULL
  AND winning_amount > 0;

SELECT p.category, COUNT(*)
FROM tender_results tr
JOIN projects p ON p.id = tr.project_id
WHERE tr.winning_amount > 0
GROUP BY p.category
ORDER BY COUNT(*) DESC;
```

산출물:

- `models/reports/backtest-data-audit-YYYYMMDD.json`
- 업종별 사용 가능 표본 수
- 제외 사유별 row count

### 1단계. 가격 예측 baseline 백테스트

목표: 현재 predictor가 실제 낙찰률을 어느 정도 맞추는지 확인한다.

실행 항목:

1. 업종별 `HistoricalData.bid_rate` series를 시간순으로 정렬한다.
2. rolling window로 train/holdout을 나눈다.
3. 기존 historical predictor와 사용 가능한 experimental predictor를 비교한다.
4. 평균 절대 오차, 최대 오차, 구간별 정확도를 계산한다.
5. 표본이 부족한 업종은 자동 제외한다.

기존 활용 코드:

- `app/services/prediction_dataset.py`
- `app/ai/predictor_backtest.py`
- `PRICE_PREDICTION_PREFERRED_PREDICTOR=auto`

권장 추가 스크립트:

```bash
python scripts/backtest_price_predictors.py \
  --category construction \
  --start-date 2024-09-02 \
  --end-date 2026-05-15 \
  --holdout-size 200 \
  --out models/reports/price-backtest-construction-20260521.json
```

성공 기준:

- `construction`, `service` 각각 1,000건 이상 평가
- 평균 절대 낙찰률 오차 `<= 0.01` 우선 목표
- `within_1pct >= 70%` 우선 목표
- 데이터 누수 검사가 0건

### 2단계. 과거 공고 후보 선별 재구성

목표: 가상 업체가 과거 공고 중 어떤 건을 입찰 후보로 골랐을지 재현한다.

실행 항목:

1. 가상 업체 프로필을 고정한다.
2. 과거 `Project`를 deadline 기준 시간순으로 순회한다.
3. 각 프로젝트의 `data_cutoff_at`을 만든다.
4. 해당 시점에 볼 수 있는 정보만으로 참여 가능 여부를 판단한다.
5. `bid_now`, `review`, `skip`을 저장한다.
6. 판단 근거와 rule hit를 JSON으로 남긴다.

필요 구현:

- `app/services/paper_bidding_backtest.py`
- `scripts/backtest_paper_bidding.py`
- 시간 cut-off가 적용된 dataset loader

성공 기준:

- 참여 후보와 제외 후보가 모두 재현 가능해야 한다.
- 각 판단 row에 `data_cutoff_at`, `strategy_version`, `input_snapshot_hash`가 있어야 한다.
- 같은 설정으로 재실행하면 같은 결과가 나와야 한다.

### 3단계. 가상 투찰금액 생성

목표: 후보 공고마다 실제 제출했을 가상 투찰금액을 고정한다.

실행 항목:

1. 가격 predictor로 `predicted_price`, scenario band를 계산한다.
2. 전략별 투찰 모드를 적용한다.
3. 최저 투찰률 guardrail을 적용한다.
4. `paper_bid_amount`, `paper_bid_rate`, `scenario`를 저장한다.
5. 금액 생성 후 결과 row 수정은 금지한다.

추천 전략:

| 전략 | 설명 |
| --- | --- |
| conservative | 낙찰 가능성보다 방어적 가격 우선 |
| base | 현재 predictor 중심 |
| aggressive | 낙찰률 근접도를 높이는 가격 |

성공 기준:

- 모든 `paper_bid_amount`가 예산/기초금액 범위와 guardrail을 통과해야 한다.
- 결과 발표 전에는 정산 컬럼이 비어 있어야 한다.
- 동일 run은 immutable하게 유지해야 한다.

### 4단계. 실제 결과 정산

목표: 가상 투찰금액과 실제 `TenderResult`를 비교해 성과를 계산한다.

실행 항목:

1. 각 `paper_bid`의 `project_id`로 usable `TenderResult`를 찾는다.
2. `winning_amount`, `winning_rate`, `winning_company`를 정산 snapshot에 복사한다.
3. 금액 오차와 낙찰률 오차를 계산한다.
4. 가격 기준 승리 가능 여부를 보수적으로 판정한다.
5. 비가격 요소가 불충분하면 `would_have_won_final=unknown`으로 둔다.

주요 지표:

- 참여 후보 수
- 가상 투찰 수
- 정산 완료 수
- 평균 절대 금액 오차
- 평균 절대 낙찰률 오차
- `within_0_1pct`
- `within_0_3pct`
- `within_1pct`
- `would_have_won_price_only_rate`
- 업종별/발주처별/예산구간별 성과

성공 기준:

- 정산 대상 row의 95% 이상이 usable result와 연결되어야 한다.
- pending/opening snapshot이 결과로 섞이지 않아야 한다.
- 승률 리포트는 `price_only`와 `final_estimated`를 분리해야 한다.

### 5단계. 리포트와 운영 대시보드 연결

목표: 백테스트 결과를 사람이 판단할 수 있는 형태로 고정한다.

권장 산출물:

- `models/reports/paper-bidding-backtest-YYYYMMDD.json`
- `models/reports/paper-bidding-backtest-YYYYMMDD.md`
- 업종별 CSV export
- 대시보드 요약 API

리포트 필수 항목:

- 테스트 기간
- 사용 전략 버전
- 사용 모델 버전
- 데이터 cutoff 정책
- 대상 업종
- 제외 row 수와 제외 사유
- 전체 성과
- 업종별 성과
- worst 20 cases
- best 20 cases
- 누수 검사 결과

### 6단계. Forward Paper Bidding과 연결

목표: 과거 백테스트에서 검증한 전략을 실제 진행 공고에 paper로 적용한다.

실행 항목:

1. 매일 진행/재공고 프로젝트를 수집한다.
2. 마감 전 paper run을 생성한다.
3. 가상 투찰 판단과 금액을 잠근다.
4. 결과 발표 후 자동 정산한다.
5. 과거 백테스트 성과와 forward 성과를 분리 비교한다.

성공 기준:

- 최소 4주간 forward paper run을 누적한다.
- 정산 완료 후 전략별 가격 오차와 승률 추정치를 계산한다.
- 실제 투찰 전환 여부는 forward paper 성과가 안정화된 뒤 판단한다.

## 권장 구현 순서

1. `scripts/backtest_price_predictors.py` 추가
2. `scripts/backtest_data_audit.py` 추가
3. `app/services/backtest_cutoff.py` 추가
4. `app/services/paper_bidding_backtest.py` 추가
5. `paper_bid_runs`, `paper_bids`, `paper_bid_settlements` 모델/마이그레이션 추가
6. `scripts/backtest_paper_bidding.py` 추가
7. `GET /api/v1/backtests/paper-bidding/runs` API 추가
8. dashboard 결과 탭에 backtest/forward paper 성과 추가

## 초기 실행 범위 추천

처음부터 모든 업종을 대상으로 하지 않는다.

1차:

- 기간: 2024-09-02부터 2026-05-15까지
- 업종: `construction`, `service`
- 방식: 가격 예측 baseline + 후보 선별 재구성
- 목적: 낙찰률 오차와 가격 근접도 확인

2차:

- 가상 업체 프로필 적용
- `bid_now`, `review`, `skip` 의사결정 재구성
- strategy별 paper bid amount 비교

3차:

- forward paper bidding 자동화
- 실제 진행 공고 대상 일일 실행
- 결과 발표 후 자동 settlement

## 구현된 실행 명령

현재 repo에는 1차 실행에 필요한 CLI와 저장 모델이 추가되어 있다.

데이터 감사:

```bash
python scripts/backtest_data_audit.py \
  --category construction \
  --out models/reports/backtest-data-audit-construction.json
```

가격 predictor rolling holdout 백테스트:

```bash
python scripts/backtest_price_predictors.py \
  --category construction \
  --limit 5000 \
  --holdout-size 200 \
  --min-training-samples 30 \
  --out models/reports/price-backtest-construction.json
```

과거 낙찰 결과 기반 paper bidding dry-run:

```bash
python scripts/backtest_paper_bidding.py \
  --category construction \
  --limit 100 \
  --scenario base \
  --settle-actions bid_now,review \
  --out models/reports/paper-bidding-backtest-construction.json
```

DB에 `paper_bid_runs`, `paper_bids`, `paper_bid_settlements`를 저장하려면 `--persist`를 추가한다.

```bash
python scripts/backtest_paper_bidding.py \
  --category construction \
  --limit 100 \
  --scenario base \
  --settle-actions bid_now,review \
  --persist
```

`--persist`는 운영 DB에 가상 투찰/정산 row를 남긴다. 실험 전략이 확정되기 전에는 먼저 dry-run으로 JSON 리포트를 확인한다.

## 리스크와 보완점

### 현재 부족한 점

- 과거 `BidDecisionRecord`가 0건이라 기존 운영 판단의 승률은 측정할 수 없다.
- `Bid`가 0건이라 실제 제출 이력 기반 승패 분석은 불가능하다.
- `software` 업종은 결과 표본이 2건뿐이라 단독 성능 평가가 불가능하다.
- 경쟁사 전체 투찰분포, 적격심사, 면허/실적 요건이 충분히 구조화되어 있지 않다.

### 보완 방향

- 백테스트는 먼저 가격 근접도와 가격 기준 가능성만 평가한다.
- 실제 승률 표현은 `price_only`와 `final_estimated`를 분리한다.
- 가상 업체 자격조건을 구조화해 참여 가능 여부를 보수적으로 필터링한다.
- KONEPS 개찰/낙찰 수집에서 pending/opening snapshot과 실제 낙찰 결과를 명확히 분리한다.

## 결론

현재 repo와 DB 상태만으로도 **construction/service 중심의 가격 예측 백테스트는 바로 가능**하다. 표본 수가 충분하고 `HistoricalData`, `TenderResult`, `Project` 연결도 갖춰져 있다.

하지만 사용자가 원하는 “가상 업체가 참여했을 때 실제 낙찰을 받을 수 있는지”를 정량화하려면 **Paper Bidding 전용 실행/정산 도메인**이 필요하다. 기존 `BidDecisionRecord`와 `Bid`가 0건이므로, 첫 실전 테스트는 과거 공고 재구성 백테스트와 forward paper bidding을 병행하는 방식이 가장 현실적이다.
