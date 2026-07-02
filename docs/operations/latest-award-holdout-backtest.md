# Latest Award Holdout Backtest

이 문서는 가격 예측 알고리즘을 개선한 뒤, 같은 방식으로 개선 여부를 확인하기 위한
업무구분별 최신 낙찰결과 홀드아웃 백테스트 절차를 기록한다.

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
