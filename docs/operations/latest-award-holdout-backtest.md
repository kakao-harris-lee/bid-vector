# Latest Award Holdout Backtest

이 문서는 가격 예측 알고리즘을 개선한 뒤, 같은 방식으로 개선 여부를 확인하기 위한
업무구분별 최신 낙찰결과 홀드아웃 백테스트 절차를 기록한다.
세부 조달 세그먼트별 개선 후보는 `docs/operations/procurement-segment-improvement-notes.md`를
함께 본다.

## 목적

- 업무구분(`construction`, `service`, `goods`)별로 낙찰결과가 발표된 최신 공고를 1건씩 고른다.
- 해당 공고의 실제 투찰값/낙찰값을 예측 입력에서 제외한다.
- 업무구분별 과거 확정 낙찰 데이터만 학습/히스토리 입력으로 사용한다.
- 현재 predictor가 산출한 추천 투찰가가 실제 낙찰가에 얼마나 가까운지 확인한다.

이 검증은 rolling holdout 전체 평균을 보는 `scripts/backtest_price_predictors.py`와 다르다.
운영자가 실제로 최근 발표 결과를 놓고 "이번 개선이 최신 공고에서 나아졌는가"를 보는
스냅샷 검증이다.

## 실행

기본 실행은 업무구분별 최신 발표 공고를 다시 추출한다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py
```

리포트는 기본적으로 `models/reports/latest-award-holdout-<timestamp>.json`에 저장된다.
운영 검증 증적으로 남길 때는 명시 경로를 사용한다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --out models/reports/latest-award-holdout-after-<change-tag>.json
```

데이터 수집이 진행되어 "최신 공고"가 바뀌면 개선 전후 비교가 흔들릴 수 있다. 코드 개선 효과만
분리해서 보고 싶을 때는 이전 리포트의 `targets[].notice_number`를 고정한다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --notice-numbers R26BK01613560,R26BK01613052,R26BK01613845 \
  --out models/reports/latest-award-holdout-fixed-after-<change-tag>.json
```

더 넓은 범위를 보려면 업무구분별 최신 N건을 고정 홀드아웃으로 선택한다. 전체 타깃 상세는
JSON에 저장하고, 표준출력은 요약만 보려면 `--print-target-limit 0`을 사용한다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --targets-per-group 50 \
  --candidate-limit 50000 \
  --history-limit 1000 \
  --out models/reports/latest-award-holdout-wide-50-per-group.json \
  --print-target-limit 0 \
  --worst-limit 15
```

### 기관(발주처/수요기관) group holdout

`--group-by agency`는 최신 타깃을 업무구분이 아니라 **기관별로** 뽑는다. 고카디널리티
feature 과적합(로드맵 11번)을 잡기 위한 축이다. `--out`을 생략하면 고정 경로
`models/reports/latest-award-holdout-agency.json`에 저장되므로 개선 전후를 같은 명령으로
비교하고 한 파일만 diff 하면 된다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --group-by agency \
  --targets-per-group 3 \
  --min-agency-samples 3 \
  --candidate-limit 50000 \
  --history-limit 1000 \
  --print-target-limit 0
```

`--min-agency-samples` 미만인 기관은 리포트에서 `_etc` 버킷으로 **합산**된다(침묵 제외가
아니다 — 합산된 기관 수/표본 수는 `summary.agency_axis`에 남는다). 기관명이 없는 공고는
`_unknown` 버킷으로 따로 모인다.

`--exclude-agency-history`를 함께 주면 타깃 기관의 과거 낙찰까지 예측 히스토리에서 빼는
**진짜 group holdout**이 된다. 시간 축 홀드아웃만으로는 잡히지 않는 기관 단위 암기를
측정하는 용도이며, 기본값은 꺼짐(기존 측정과 비교 가능성 유지)이다.

제외 판정은 **키 하나가 아니라 집합**으로 한다. `HistoricalData.agency_name`은 수집 시
`opening_demand_agency or demand_agency or issuing_agency` 순으로 적재되는데(수요기관 우선)
리포트 분할 키는 발주기관 우선이라 발주≠수요 공고(조달청 경유 등)에서 둘이 갈린다. 단일
키로 필터하면 타깃 기관의 과거 낙찰이 다른 이름으로 저장돼 필터를 통과 = 누수이므로,
타깃의 발주기관·수요기관·그리고 그 공고 자신의 `agency_name`을 모두 키로 묶어 비교한다.

남는 한계(정직 표기): 어떤 히스토리 행이 타깃의 세 이름 중 **어느 것과도 다른**
`opening_demand_agency`로 저장돼 있으면 여전히 통과한다. 이를 막으려면 히스토리 시리즈가
각 행의 발주/수요 기관을 함께 실어야 하는데 그 직렬화는 라이브 예측 경로와 공유라 이
스코프에서 건드리지 않았다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --group-by agency --exclude-agency-history \
  --out models/reports/latest-award-holdout-agency-unseen.json
```

기관 축 리포트 블록:

| 블록 | 의미 |
|------|------|
| `summary.agency_axis` | 최소 표본 수, 서로 다른 기관 수, `_etc`로 합산된 기관/표본 수, `_unknown` 표본 수 |
| `breakdowns.by_agency` | 기관(버킷)별 요약 |
| `agency_displays` | 정규화 키 → 사람이 읽는 기관명 |
| `worst_agency_groups` | 추천 평균 절대오차율이 큰 기관 순. `is_residual`이 `true`면 단일 기관이 아니라 `_etc`/`_unknown` 잔여 버킷이다 |

## 누수 방지 규칙

- 타깃 공고의 `notice_number`와 `project_id`는 historical input에서 제거한다.
- 학습 컷오프는 `event_at`과 `available_at` 중 더 이른 시각의 1초 전으로 잡는다.
- `explicit_bid_rate_only=True`로 실제 낙찰률/낙찰금액이 있는 확정 데이터만 사용한다.
- 기본 `--timestamp-grace-hours 9`는 로컬 DB에 KST/UTC가 섞여 저장된 source timestamp를
  최신 공고 선정에서 배제하지 않기 위한 허용폭이다. 엄격한 UTC 기준 검증이 필요하면 `0`으로 실행한다.

## 비교 지표

리포트의 `summary.recommended`를 기본 개선 지표로 본다.

| 지표 | 의미 | 개선 판단 |
|------|------|-----------|
| `mean_absolute_amount_error_pct` | 추천 투찰가와 실제 낙찰가의 평균 절대오차율 | 낮아져야 함 |
| `mean_absolute_bid_rate_error_bp` | 추천 투찰률과 실제 낙찰률의 평균 절대오차(bp) | 낮아져야 함 |
| `within_counts.0.3%` | 낙찰가 대비 0.3% 이내에 들어온 건수 | 늘어나야 함 |
| `within_counts.1.0%` | 낙찰가 대비 1.0% 이내에 들어온 건수 | 늘어나야 함 |

`summary.closest`는 conservative/base/aggressive 후보 중 사후적으로 가장 가까운 후보를 보여준다.
이 값이 개선되고 `recommended`가 개선되지 않으면, 모델의 가격 생성보다 시나리오 선택 정책이
문제일 가능성이 높다.

개별 타깃에서 `prediction_metadata.high_rate_tail_adjustment`가 채워져 있으면 최근 고율 낙찰
분포 또는 소액 공사 상단 밴드 보정이 추천값에 반영된 것이다.

확장 모드에서는 다음 보조 블록도 함께 확인한다.

| 블록 | 의미 |
|------|------|
| `breakdowns.by_group` | 업무구분별 요약 |
| `breakdowns.by_amount_bucket` | 기초금액대별 요약 |
| `breakdowns.by_procurement_rate_band` | 조달 세그먼트 밴드별 요약 |
| `breakdowns.by_data_quality_flag` | 금액/율 불일치, 저율 이상치 등 데이터 품질 플래그별 요약 |
| `worst_recommended_targets` | 추천값 기준 오차가 큰 공고 목록 |

## 분모/법정하한 품질 플래그

오차 지표는 분모가 맞을 때만 의미가 있으므로, 각 타깃에 대해 아래 판정을 함께 남긴다
(`targets[].data_quality_flags` / `targets[].data_quality_details`).

| 플래그 | 판정 |
|--------|------|
| `amount_rate_mismatch` | 보고 낙찰률과 `winning_amount / base_amount`의 **상대** 불일치가 1%를 넘음 |
| `below_legal_floor` | 보고 낙찰률이 법정 낙찰하한을 5bp 넘게 하회. **하한 모델이 그 공고에 적용되고**(아래 적용 범위) **보고율이 독립 실측일 때만**(아래 basis 독립성) 판정한다 |
| `base_basis_contaminated` | `base_amount`가 clean이 아님(예정가 역산/VAT 파생/미상, #199) |
| `missing_reported_rate` | 소스가 낙찰률을 보고하지 않아 분모 정합/하한 판정 근거가 없음 |
| `missing_amount_derived_rate` | 금액 역산 낙찰률을 만들 수 없음 |
| `low_actual_rate` / `construction_low_rate_review` | 가격 백테스트 표본으로 쓰기 어려운 저율 구간 |

법정 하한은 공고 자신이 게시한 `award_floor_rate`를 우선 쓰고, 없으면 산림청 계열은 산림사업
하한(87.745%, 아래 `separate_regime`), 그 밖은 era-correct 공사 적격심사 tier(#197, 공고
기준일로 구/신율 선택 — 소급 없음)로 해석한다. 어떤 값이 쓰였는지는
`data_quality_details.legal_floor_source`(`published_award_floor_rate` /
`forestry_regime_spec` / `construction_era_tier` / `unresolved`)에 남는다.

**하한 판정은 보고 낙찰률이 있을 때만 수행한다.** 법정 하한율은 예정가격 기준인데 금액 역산
낙찰률은 기초금액 기준(#162)이라 사정률만큼 구조적으로 낮게 나오므로, 역산율로 하한을 재면
정상 낙찰이 대량 오탐된다.

### 하한 모델 적용 범위 (`floor_applicability`)

라이브 기준선(기관 축 2,798건, 2026-07-26)에서 `below_legal_floor` 49건을 뜯어보니 상당수가
**위법 낙찰이 아니라 적용 범위 밖 공고에 국가계약 era-tier를 일괄 적용해 생긴 오탐**이었다.
산학협력단·농업협동조합 같은 비국가기관은 국가계약법 적격심사 낙찰하한율 적용 대상이 아닌데
0.89745가 그대로 적용돼 9.9~46%p 하회로 잡혔다.

그래서 발주기관(부재 시 수요기관) 표기명으로 적용 범위를 판별하고, 결과를
`targets[].data_quality_details.floor_applicability`에 남긴다.

| 값 | 의미 | 하한 하회 판정 |
|----|------|----------------|
| `applicable` | 기본값(국가·지자체 기관 등) | 기존대로 수행 |
| `not_applicable` | 명백한 비국가기관(산학협력단, 협동조합/농협·수협·축협·신협, 학교법인) | 생략 |
| `uncertain` | 이름만으로 국공립/사립을 가를 수 없는 부류(대학교·전문대학) | 생략 |
| `separate_regime` | 국가기관이지만 별도 행정규칙 체계를 따르는 발주(산림청 계열) | 수행(산림사업 하한 87.745%) |

판별 규칙은 코드 분기가 아니라 `app/ai/floor_applicability.py`의 `_AGENCY_PATTERNS` 선언
테이블이다. **패턴 추가 = 테이블 한 줄**이며, 위에서부터 첫 매칭이 이기므로 `not_applicable`
항목이 `uncertain`보다 앞에 온다("○○대학교 산학협력단"은 비국가기관으로 확정). 짧은 약칭
(`농협`/`수협`)은 무관한 기관명 안에 substring으로 걸리므로(`농업용수협의체`) 기관명 말미에서만
매칭한다.

#### 산림사업 별도 규정 (`separate_regime`)

위 게이트 적용 후 남은 하회 중 6건이 전부 산림청 계열(국유림관리소·지방산림청·
국립산림품종관리센터)이었다. 복수예비가격 15개로 예정가를 재구성해도 낙찰률이 0.872~0.896으로
신율 tier(0.89745) 아래에 남고, 하단 2건은 구율 tier(0.87745) 바로 위에 몰려 있다. 산림사업은
국가계약 예규가 아니라 **산림청 산림사업 적격심사 세부기준**(별도 행정규칙)을 따르는 것으로
보이므로, 국가계약 era-tier를 그대로 대는 것 자체가 범주 오류다.

**하한율 원문 확정(2026-07-26) — 판정 보류 해제.** 산림사업 입찰설명서 원문(공고
`R26BK01490237`, 입찰 2026-04-27~05-06 = 국가계약 +2%p 개정 시행일 **이후**)이 "예정가격 이하로서
예정가격의 87.745% 이상 … 적격심사"를 명시하고 근거 규정을 **「산림청 산림사업 적격심사
세부기준」(산림청예규 제728호, 2025-12-01)**으로 특정한다. 즉 산림사업은 국가계약 개정을
추종하지 않았다. 그래서 `separate_regime` 행은 이제 era-tier 대신 **산림사업 하한 87.745%로 하회
판정을 수행**하고(`legal_floor_source = forestry_regime_spec`), 상수는
`app/ai/floor_applicability.py`의 `FORESTRY_REGIME_FLOOR_RATE` 한 줄로 선언된다. 라이브 정합:
2026-07 산림청 계열 6건의 보고율 0.87766~0.89576이 전부 이 하한 위였고 하단 2건이 바로 위에
군집한다(= 전건 적법으로 재현). 비국가기관(`not_applicable`)과 **같은 버킷으로 접지 않는다** —
산림청은 국가기관이 맞고 근거 규정이 다를 뿐이다.

**남는 한계:** 예규 별표가 추정가격 구간별로 하한율을 차등하는지는 미확인이다(실측 표본이 전부
3.3억 이하 소규모라 확인할 수 없었다). 지금의 flat rate는 **원문 1건으로 확인된 값**이지 예규
별표를 인코딩한 것이 아니므로, 대형 산림사업 표본이 쌓이거나 예규 별표를 확인하면 이 선언을
구간표로 승격한다. 게시 하한율(`published_award_floor_rate`)이 개연 범위 안이면 그쪽이 여전히
우선한다.

매칭은 `산림청` contains 한 줄이다(소속기관이 본청명을 접두로 달고 적재된다:
`산림청 북부지방산림청 홍천국유림관리소`, `산림청 국립산림품종관리센터`). substring 충돌이
없다는 근거는 라이브 기관명 2,799개 스캔이다 — `산림청`을 포함한 이름은 전부 산림청
소속기관이었고, 비국가기관인 산림**조합**(강릉시산림조합·산림조합중앙회 등 60여 개)은 이
토큰을 포함하지 않아 `applicable`로 남는다. 본청명 없이 적재된 표기를 위해 `국유림관리소`를
보강 패턴으로 둔다.

기관 축 판별이라 **사업 종류 축은 구분하지 못한다.** 산림청 소속기관이 발주한 일반 공사·용역도
같은 하한으로 재는데, 그쪽이 실제로는 국가계약 적격심사(0.89745) 대상이라면 더 낮은 하한을 댄
셈이라 **하회를 놓칠 수는 있어도 없는 위법을 만들지는 않는다**(오탐이 아니라 미검출 방향).

### 보고 낙찰률 basis 독립성 (`rate_basis_unverified`)

같은 기준선에서 잔여 하회 13건은 보고 낙찰률(`actual.reported_winning_rate`)이
`winning_amount ÷ base_amount` 산술값과 사실상 일치했고(상대오차 ≤1e-5, 6건은 정확히 0.0)
복수예비가격도 수집돼 있지 않았다. 즉 그 값은 독립적인 예정가-basis 실측이 아니라 우리가 이미
가진 금액비의 파생값일 수 있고, 그것을 예정가-basis 하한에 대면 사정률(~0.98)만큼 구조적으로
낮게 읽힌다(관측된 0.9~1.7%p 하회와 정합).

그래서 하한 판정 앞에 **rate 독립성 게이트**를 둔다.

- 보고율과 금액-역산율의 상대오차가 `RATE_BASIS_INDEPENDENCE_TOLERANCE`(1e-3) 미만이고,
- 복수예비가격이 `MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE`(5개) 미만이면

하한 판정을 생략하고 `data_quality_details.rate_basis_unverified = true`로 남긴다. 경계를
1e-3으로 둔 근거는 라이브에서 두 군집이 그 값을 사이에 두고 깨끗이 갈렸기 때문이다(파생 의심군
≤1e-5, 독립 확인군의 **최소** 상대오차 1.4e-3, 그 사이 값은 미관측).

예비가가 충분하면 예정가를 독립적으로 재구성할 수 있으므로 보고율이 금액비와 같아도(사정률≈1인
정상 케이스) 판정을 유지한다. 판단 근거는 행마다 `reserve_price_count`와
`rate_basis_independence_tolerance`로 함께 남는다.

공고 게시 하한율(`published_award_floor_rate`)도 **개연 범위** 안일 때만 판정에 쓴다. 라이브에
`1.00000`으로 적재된 값이 있는데(예정가 전액 이상 투찰 = 하한의 의미가 아님) 그대로 쓰면 정상
낙찰이 하회로 잡힌다. 범위 밖이면 era-tier로 폴백하고 `published_floor_implausible`을 남긴다.
경계 상수는 같은 모듈의 `PUBLISHED_FLOOR_MIN_PLAUSIBLE`(0.30) / `PUBLISHED_FLOOR_MAX_PLAUSIBLE`
(0.995)이며, 하한을 0.5로 올리지 않는 이유는 라이브에 실제 게시값 `0.47995`가 있기 때문이다.

스킵 규모는 침묵하지 않고 요약에 싣는다.

| 키 | 의미 |
|----|------|
| `summary.floor_applicability_counts` | 집계 스코프(기본 clean-only)의 적용 범위 상태별 건수 |
| `summary.evaluated_floor_applicability_counts` | 평가된 전체 타깃 기준 상태별 건수 |
| `summary.published_floor_implausible_count` / `evaluated_published_floor_implausible_count` | 게시 하한율이 개연 범위 밖이라 쓰지 않은 건수 |
| `summary.rate_basis_unverified_count` / `evaluated_rate_basis_unverified_count` | 보고 낙찰률 basis가 미검증이라 하한 판정을 생략한 건수 |

`rate_basis_unverified`는 **그 게이트 때문에 판정이 생략된 행**만 센다. 적용 범위 밖이거나
하한 자체가 해석되지 않아 애초에 비교하지 않은 행은 포함하지 않으므로, 두 축의 생략 규모를
겹치지 않게 읽을 수 있다.

**남는 한계(정직 표기):** 위 두 게이트 밖의 얕은 하회(농어촌공사 지사 등 지방계약·공공기관·
수의견적 가능성)는 해소되지 않는다. 저장된 데이터만으로 어느 티어였는지 단정할 수 없어 그대로
`below_legal_floor`로 남긴다. `separate_regime`은 원문 확정(2026-07-26)으로 판정을 재개했지만
예규 별표의 구간 차등은 여전히 미확인이다(위 절). 이 판별은 전부 분석 전용이며 라이브 예측
guardrail(게시 하한을 `max()`로만 접어 올리는 경로)에는 관여하지 않는다.

clean/flag 분리 비교는 `summary.quality_flag_partition`을 본다. `all` / `flag_free` /
`flagged` 3분할이라 `flag_free + flagged == all`로 검산할 수 있고, 플래그 표본을 뺐을 때
오차가 얼마나 내려가는지 한 블록에서 확인된다.

**건수는 두 스코프를 구분해서 읽는다.**

| 키 | 스코프 | 주의 |
|----|--------|------|
| `summary.quality_flag_counts` | 집계 스코프(기본 clean-only) | 여기서 `base_basis_contaminated`는 **구조적으로 항상 0**이다. 오염이 없다는 뜻이 아니라 오염 행이 이미 집계에서 빠졌다는 뜻이다 |
| `summary.evaluated_quality_flag_counts` | 평가된 전체 타깃 | 실제 basis 오염 건수는 여기서 본다(#199 기준 저장 데이터 오염 비율 ~66%) |
| `summary.quality_flag_scope` | 두 스코프의 건수와 제외 건수 | `excluded_from_aggregation`으로 차이를 검산한다 |

오차 지표(`by_flag`, `partition`)는 설계대로 집계 스코프 기준을 유지한다 — 오염 표본이
오차 평균을 흔들지 않게 하려는 기존 규칙이다.

## 2026-07-02 기준선

로컬 DB 기준, 발표/개찰시각 기준 최신 공고 3건을 선택해 실행한 기준선이다.

| 업무구분 | 공고번호 | 실제 낙찰가 | 추천 예측가 | 오차 | 오차율 | 1.0% 이내 |
|----------|----------|-------------|-------------|------|--------|-----------|
| 공사 | R26BK01613560 | 38,000,000 | 36,945,475 | -1,054,525 | 2.775% | 실패 |
| 용역 | R26BK01613052 | 4,755,000 | 4,657,653 | -97,347 | 2.047% | 실패 |
| 물품 | R26BK01613845 | 27,540,000 | 25,928,012 | -1,611,988 | 5.853% | 실패 |

요약 기준선:

- `recommended.mean_absolute_amount_error_pct`: 3.559%
- `recommended.mean_absolute_bid_rate_error_bp`: 346.2bp
- `recommended.within_counts.0.3%`: 0/3
- `recommended.within_counts.1.0%`: 0/3
- `closest.mean_absolute_amount_error_pct`: 1.628%

개선 후에는 같은 DB 스냅샷 또는 `--notice-numbers` 고정 모드에서 위 기준선보다
`recommended` 지표가 낮아지고, 0.3%/1.0% 이내 건수가 늘어나는지 확인한다.

## 해석 메모

2026-07-02 기준선에서는 세 그룹 모두 추천값이 실제 낙찰가보다 낮게 나왔다. 특히 물품은 실제
낙찰률이 99%대인데 추천값은 93%대에 머물렀다. 업무구분 분리만으로는 충분하지 않으므로,
이후 개선은 낙찰하한율, 계약방식, 고율 낙찰 패턴, 시나리오 선택 정책을 함께 검증해야 한다.

## 2026-07-02 세그먼트/금액단위 개선 후 확인

해양/엔지니어링 가격경쟁 세그먼트, 명시적 수의시담 세그먼트, 최종 10원 단위 투찰가 보정을
반영한 뒤 같은 최신 홀드아웃 방식을 다시 실행했다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --out /tmp/latest-award-holdout-final-10won.json
```

요약:

- `recommended.mean_absolute_amount_error_pct`: 0.5545%
- `recommended.mean_absolute_bid_rate_error_bp`: 52.39bp
- `recommended.within_counts.0.3%`: 2/3
- `recommended.within_counts.1.0%`: 2/3

개별 결과:

| 업무구분 | 공고번호 | 실제 낙찰가 | 추천 예측가 | 오차율 |
|----------|----------|-------------|-------------|--------|
| 공사 | R26BK01613560 | 38,000,000 | 37,432,500 | 1.493% |
| 용역 | R26BK01613052 | 4,755,000 | 4,762,750 | 0.163% |
| 물품 | R26BK01613845 | 27,540,000 | 27,541,930 | 0.007% |

## 2026-07-02 확장 홀드아웃 150건

업무구분별 최신 50건씩, 총 150건으로 확장 실행했다. 아래 수치는 물품 견적/2단계 가격경쟁 밴드와
용역 2단계/수학여행/버스 예외 밴드를 반영한 뒤의 최종 확인값이다.

```bash
.venv/bin/python scripts/backtest_latest_award_holdouts.py \
  --targets-per-group 50 \
  --candidate-limit 50000 \
  --history-limit 1000 \
  --out /tmp/latest-award-holdout-wide-50-per-group-after-distortion-fix-v4.json \
  --print-target-limit 0 \
  --worst-limit 15
```

개선 전후 요약:

| 기준 | 타깃 | 개선 전 추천 평균 절대오차율 | v3 개선 후 | v4 추가 왜곡 억제 후 | v4 1.0% 이내 | v4 closest 평균 절대오차율 |
|------|------|------------------------------|------------|-------------------------|----------------|----------------------------|
| 전체 | 150 | 6.869% | 5.935% | 5.347% | 77/150 | 4.356% |
| clean 표본 | 141 | 3.510% | 2.631% | 1.837% | 76/141 | 1.011% |
| 공사 저율 review | 5 | - | - | 58.893% | 0/5 | 55.115% |
| 저율 이상치 | 4 | 77.440% | 73.831% | 73.831% | 0/4 | 69.442% |
| 금액-율 불일치 | 3 | 72.884% | 72.884% | 72.884% | 1/3 | 69.839% |

업무구분별 clean 표본 개선 후:

| 업무구분 | 타깃 | 추천 평균 절대오차율 | 1.0% 이내 | closest 평균 절대오차율 | closest 1.0% 이내 |
|----------|------|----------------------|-----------|-------------------------|-------------------|
| 공사 | 43 | 1.408% | 28/43 | 1.087% | 33/43 |
| 용역 | 48 | 2.581% | 11/48 | 1.346% | 22/48 |
| 물품 | 50 | 1.492% | 37/50 | 0.624% | 40/50 |

해석:

- 전체 평균은 데이터 품질 플래그 9건이 크게 끌어올린다. 확장 백테스트에서는 clean 표본과
  데이터 품질 플래그 표본을 반드시 분리해서 본다.
- 물품은 `소액수의 견적`, `2단계`, `규격·가격`, `국내도서`, 일부 품목형 구매를
  `goods_price_competitive`로 분리해 과대 추천을 줄였다.
- `2단계`와 `급식`/`농산물`이 결합된 물품은 `goods_deep_discount`로 분리한다. 단, 현재
  `goods` 카테고리 최저 guardrail 84%와 안전마진 때문에 최종 추천은 84.1% 아래로 내려가지 않는다.
- `건축기획`, `정밀안전진단`, `정밀안전점검`, `내진성능평가`, `석면조사`, `성능점검`,
  `시설 및 안전관리`, `경비용역`, `예초 용역`, `비용분석` 용역은 최근 고율 tail 보정을 차단하고
  `service_price_competitive`로 보낸다.
- 물품 `계측제어`는 `"(계측제어)"`처럼 좁은 제목 신호이고 `관급자재`/`프로세스`/`계장`이 없을
  때만 가격경쟁형으로 보낸다.
- `관급자재`, `구매설치`는 99~100% 낙찰도 많아 단독 가격경쟁 신호로 쓰지 않는다.
- 용역은 버스 운영/수학여행 위탁 같은 2단계·규격가격분리 공고를 `service_price_competitive`로
  우선 분류해 `service_high_negotiated` 과대 추천을 줄였다.
- 공사 실제 낙찰률 85% 미만은 `construction_low_rate_review`로 분리한다. 이는 모델 성능 개선이
  아니라 일반 공사 하한 가정과 충돌하는 평가 표본을 clean 지표에서 분리하기 위한 품질 플래그다.
- `closest`가 `recommended`보다 크게 좋은 구간은 후보 생성보다 시나리오 선택 정책이 우선 개선 대상이다.
