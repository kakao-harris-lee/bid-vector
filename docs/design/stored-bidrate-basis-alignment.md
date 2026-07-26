# 저장된 낙찰률 라벨의 예정가↔기초금액 basis 정합 (설계·측정)

분석·측정·설계 문서다. **라벨/학습 데이터·predictor·guardrail 코드는 변경하지 않는다.**
밴드/floor 재캘리브레이션 트랙(아래 §6)의 완결 조건 중 하나로 지목된 basis 잔여
비대칭(#261 리뷰 nit)의 방향·크기를 실측하고, 정합 옵션과 착수 게이트를 정리한다.

재현: `docs/operations/` 절차 없이 단독 실행 가능한 읽기 전용 계측 스크립트
`scripts/measure_stored_bidrate_basis.py` (아래 §7).

## 1. 배경 — 남은 basis 비대칭

가격 학습 데이터셋(`app/services/prediction_dataset.py`)은 공고 한 건을 두 축으로
표현한다:

- **base 피처** — 투찰 base. `resolve_notice_bid_base`(라이브)와 데이터셋 조립 모두
  `get_reliable_base`(#199/#225)를 거쳐 **기초금액-basis**로 정렬돼 있다(예정가-역산
  오염된 `base_amount`를 clean/reserve-복구 기초금액으로 대체).
- **rate 라벨** — 과거 낙찰률. `_resolve_bid_rate`의 우선순위로 고른다:
  1. `HistoricalData.bid_rate` (있으면 최우선)
  2. `TenderResult.winning_rate` 정규화 (`tender_result_winning_rate`)
  3. `winning_amount / reliable_base` (`tender_result_winning_amount`)

#199/#225가 base 피처와 **경로 3**(파생-rate 폴백)을 기초금액-basis로 맞췄지만,
**경로 1의 `winning_rate`(=낙찰가/예정가)는 여전히 예정가-relative**다. 그리고 실제로는
경로 1보다 앞선 `HistoricalData.bid_rate`가 라벨의 절대다수를 차지한다. 이 값은 단일
고정 basis가 아니라 `app/services/koneps/scsbid.py`가 **조건부로 계산**한다: 실 기초금액
(reserve detail의 `base_amount`)이 있으면 `winning_amount / base_amount`(기초금액-relative),
없으면 `success_rate`(낙찰가/예정가)로 폴백. 다만 지배 표본은 실 기초금액이 대부분 부재해
**예정가-relative로 실현**된다(§3 실측: `winning_rate`와 |평균차| 0.0006).

`winning_amount ÷ winning_rate = 예정가`라는 관계는 `app/services/base_amount_basis.py`
(`BASIS_DERIVED_YEGA = "win ÷ winning_rate 역산 = 예정가-basis"`)와
`app/services/koneps/scsbid.py`(`낙찰가/success_rate = 예정가` 주석)가 확정한다. 즉
저장된 낙찰률은 **낙찰가/예정가**이고, 서빙은 이 rate를 **기초금액**에 곱한다:

```
predicted_price = 기초금액 × learned_rate           # historical.py: budget × base_rate
learned_rate  ← 낙찰가/예정가 라벨에서 학습          # 예정가-relative
⇒ predicted = 낙찰가 × (기초금액/예정가) = 낙찰가 / 사정률
```

사정률 = 예정가/기초금액. 사정률이 1보다 작으면(예정가 < 기초금액) 추천가가 실현
낙찰가보다 **구조적으로 위로 뜬다**(초과추천). 이 basis mismatch가 남은 비대칭의 실체다.

## 2. 측정 방법과 confound

settled 행에서 두 라벨을 모두 계산해 비교한다:

- `path1` = `_normalize_bid_rate_value(TenderResult.winning_rate)` — 예정가-relative
- `path3` = `winning_amount / get_reliable_base(...)` — 기초금액-relative
- 비대칭 지표 = `path3 - path1`, 그리고 함의된 **사정률 = path3/path1 = 예정가/기초금액**

**중요한 confound.** `get_reliable_base`는 clean/명시적-non-clean+reserve복구 행에만
정직한 기초금액을 내고, **derived-yega인데 reserve 추정치가 없는 행은 원본
`base_amount`(=예정가-역산 오염값)로 폴백**한다. 그런 행은 path3의 분모가 예정가라
`path3 == path1`이 되어 비대칭이 spurious하게 0으로 보인다. 따라서 정직한 신호는
**reliable-base source가 `clean-base` 또는 `reserve-estimate`인 행에서만** 읽어야 한다.
스크립트는 이 버킷을 분리 집계한다.

## 3. 실측 결과 (라이브 DB, 2026-07-26)

모집단: project 연결 `HistoricalData` 전수. settled 링크는 project당 최신
`TenderResult`(winning_amount>0 OR winning_rate>0).

**라벨 소스 우선순위 (지배 라벨은 `HistoricalData.bid_rate`):**

| first-priority 소스 | 행 수 |
|---|---|
| `historical_data` (예정가-relative) | 59,974 |
| `tender_result_winning_amount` (기초금액) | 277 |
| `tender_result_winning_rate` (예정가) | 15 |
| none | 11,979 |

경로 3(기초금액-basis 파생 rate)은 라벨 소스로는 **사실상 휴면**(277행)이다. 라벨의
99.5%는 예정가-relative(`HistoricalData.bid_rate`)이고, `HistoricalData.bid_rate`는
`winning_rate`(예정가)와 |평균차| 0.0006 수준으로 동일하고 path3(기초금액)와는
|평균차| 0.006으로 어긋난다 → **지배 라벨은 예정가-relative임이 실측으로 확정**.

**비대칭 (confound 분리):**

| 버킷 | N | mean(p3−p1) | 사정률 mean | 함의 초과추천 |
|---|---|---|---|---|
| base-fallback \| derived-yega (confound) | 27,632 | +0.00001 | 1.00002 | −0.00% |
| reserve-estimate \| derived-yega (정직) | 17,180 | −0.00082 | 0.99908 | +0.09% |
| clean-base \| clean (정직) | 8,605 | −0.00785 | 0.99132 | +0.88% |
| **정직 집계 (clean+reserve)** | **25,794** | **−0.00316** | **0.99649** | **+0.35%** |
| confound 집계 (base-fallback) | 27,654 | +0.00002 | 1.00002 | −0.00% |

confound 버킷은 예상대로 0(측정 artifact). 정직한 기초금액 base 행에서만:
- 기초금액-basis 라벨(path3)이 예정가-basis 라벨(path1)보다 **평균 0.32%p 낮다**.
- 함의된 사정률 mean 0.9965 → 예정가-relative 라벨을 기초금액에 곱하면 평균
  **+0.35% 초과추천**.
- **중앙값은 0**(median 사정률 1.0): 비대칭은 mean 편향이며, 예정가가 기초금액을
  하회하는 tail이 몬다. 정직 행의 **41.5%가 |사정률−1|>0.5%p, 15.0%가 >1.0%p** —
  per-notice 편차는 작지 않다.

**카테고리별 (정직 base 행):**

| 카테고리 | N | 사정률 mean | 함의 초과추천 |
|---|---|---|---|
| **construction** | 11,952 | **0.99345** | **+0.66%** |
| service | 7,559 | 0.99876 | +0.13% |
| goods | 6,275 | 0.99957 | +0.04% |

**공사(construction)의 비대칭이 가장 크다(+0.66% 초과추천)**. 공사는 초기 실수요
고객인 **해양엔지니어링협회 세그먼트**이고, 이 +0.66%는 실투찰 2건 재검증에서 확정
낙찰가 대비 관측된 **+0.34%p/+0.68%p 과추천**([[rebid-diagnosis-base-overwrite]],
`docs/operations/real-bid-195-revalidation.md`)과 정합한다. 즉 이 라벨 basis mismatch가
관측된 초과추천의 구조적 성분을 상당 부분 설명한다.

## 4. 학습·서빙 skew의 방향과 크기

- **방향:** 예정가-relative 라벨 × 기초금액 base → 사정률<1인 만큼 **초과추천**(추천가가
  실현 낙찰가보다 위). 적격심사 밀집 게임에서 위로 뜨는 추천은 낙찰가보다 높아 밀린다.
- **크기:** 전체 평균 +0.35%, 공사 +0.66%. mean 편향이며 median은 0. (초과추천은
  `1/mean(사정률)−1` 로 집계한다 — `mean(1/사정률)` 이 아니라 1/x 볼록성(Jensen)으로 참
  평균 초과율을 소폭 과소평가하는 **보수적** 추정이다.)
- **관측 불가성:** 예정가는 투찰 마감 후 복수예비가격에서 추첨되므로 투찰 시점엔
  **개별 사정률을 알 수 없다**. 따라서 이론적으로 정당한 보정은 개별값이 아니라
  **E[사정률](카테고리/발주처별 기대 사정률) 상수 보정**뿐이다. 이는 #195가 발주처
  밴드에 이미 도입한 E[사정률] 변환과 정확히 같은 축의 문제다(라벨 측 대응물).

## 5. 정합 옵션

### 옵션 A — 라벨을 기초금액-basis로 환산 (relabel)

reliable base가 있는 행은 라벨을 path3(`winning_amount / reliable_base`)로 대체하거나,
예정가-relative 라벨에 E[사정률]을 곱해 기초금액-basis로 환산한다. 서빙 base(기초금액)와
라벨 basis가 직접 정합돼 mismatch가 사라진다.

- 게이트: **재학습 필요**(라벨이 summary 통계·모델 적합에 들어감) + **홀드아웃 재검증**
  (clean-only, guardrail red line 불변·공사 오차 미회귀 증명) + 밴드/floor 트랙과의
  **순서 조율**(§6). confound 행(base-fallback derived-yega)은 path3==path1이라 no-op.
- 리스크: **이중 보정.** 밴드/floor 트랙이 별도로 E[사정률]/E[예정가] 보정을 적용하면
  사정률이 두 번 곱해진다. 반드시 한 번만 적용되도록 조율해야 한다.

### 옵션 B — dual-label (양 basis 병기 + basis 태그)

예정가-relative와 기초금액-relative 라벨을 **둘 다** 데이터셋에 싣고 명시적 basis
필드를 노출한다. 트레이너/백테스트가 basis를 선택하고 A/B 비교가 가능하다.

- 게이트: 데이터셋 스키마 변경 + 트레이너/백테스트 소비부 변경. 재학습 자체는 선택
  시점에만. 되돌리기 쉽고 재캘리 트랙이 두 basis를 나란히 검증할 수 있음.
- 리스크: 복잡도 증가, 소비부 두 경로 유지 부담. 최종 서빙 basis 결정은 여전히 필요.

### 옵션 C — 현상 유지 + 재캘리 시 일괄 (E[사정률] 단일 보정으로 흡수)

라벨은 지금 건드리지 않고, 서빙 측 초과추천을 밴드/floor 재캘리브레이션 트랙(#195
후속)에서 **E[사정률]/E[예정가] 단일 보정**으로 흡수한다. 라벨 basis mismatch가 만드는
초과추천은 그 보정 상수 안에 포함된다.

- 게이트: 지금 변경 0. 보정은 floor-bound 개찰 표본이 쌓여 재캘리 판단이 서면 적용.
- 리스크: 라벨 basis는 여전히 예정가-relative로 남아 개념적 부채가 지속(문서로 관리).
  단, 관측 불가성(§4) 때문에 어차피 상수 보정만 정당하므로 실질 손실은 작다.

## 6. 권고안과 착수 조건

**권고: 옵션 C를 1차로 채택하되, 실제 라벨 정합(옵션 A: reliable base 있는 행을 path3
선호로 환산)은 밴드/floor 재캘리브레이션 트랙의 재학습 패스 안에서 함께 수행한다.**
독립적인 라벨 relabel을 지금 단독으로 하지 않는다. 근거:

1. **이중 보정 방지.** #195 다음 가설은 "floor 앵커 base 정합 = 0.88×E[예정가]"이다
   (`docs/operations/real-bid-195-revalidation.md`, roadmap 460행). 라벨을 별도로
   기초금액-basis로 환산하면서 floor도 E[예정가]로 옮기면 사정률이 두 번 반영된다.
   두 보정은 **한 번의 조율된 패스**로 묶어 사정률을 정확히 한 번만 적용해야 한다.
2. **지배 confound 행은 no-op.** 라벨의 다수(base-fallback derived-yega)는 path3==path1
   이라 단독 relabel의 실익이 없다. 정직한 이득은 clean/reserve 행(≈26k)에 한정된다.
3. **재학습·홀드아웃은 게이트 활동.** 별도 재학습을 지금 트리거하기보다, 이미
   floor-bound 표본 대기 중인 재캘리 트랙에 편승하는 것이 효율적 시퀀싱이다.
4. **관측 불가성.** 투찰 시점 개별 사정률은 알 수 없어 어차피 E[사정률] 상수 보정만
   정당하다(§4). 옵션 C의 상수 보정이 이론 최적과 일치한다.

**착수 조건 (전부 충족 시 재캘리 패스에 편입):**

- floor-bound 개찰 표본 축적 — 밴드/floor 재캘리브레이션과 **동일 게이트**
  (n=2 과적합 방지, roadmap 464행).
- 라벨 정합·floor 앵커 base 정합을 **한 패스**로 수행, 홀드아웃 재검증에서
  (a) guardrail red line(카테고리/법정 하한 `max()` 구조) 불변,
  (b) 공사 추천 오차 미회귀(오히려 −0.66%p 개선 기대) 증명.
- E[사정률] 적용 지점 단일화(라벨 or floor 중 한 곳, 이중 금지)를 ml-reviewer가 확인.

**중간 조치(코드 변경 없음):** 본 문서 + `scripts/measure_stored_bidrate_basis.py`로
비대칭을 상시 재측정 가능하게 남긴다. 표본이 쌓이면 카테고리별 E[사정률]을 재실측해
보정 상수의 근거로 쓴다.

## 7. 재현 (읽기 전용 계측)

```bash
docker compose exec -T api python scripts/measure_stored_bidrate_basis.py
docker compose exec -T api python scripts/measure_stored_bidrate_basis.py --by-category
```

DB read-only(write/commit 없음), 외부 호출 없음. 정규화·base 선택 **프리미티브**
(`PredictionDatasetService._normalize_bid_rate_value`, `get_reliable_base`)와 유효범위
게이트 상수는 프로덕션과 동일하게 재사용해 드리프트를 막는다. 다만 **우선순위
dispatch·result 선택은 단순화 미러**다: (a) tier-4 `predicted_price` 폴백
(`explicit_bid_rate_only=False`에서만 작동)을 생략해 §3 'none' 카운트가 프로덕션 실제
미라벨보다 과다 계상된다(지배 라벨 결론은 불변), (b) result 선택이 project당 id-desc라
다중-결과 프로젝트에서 프로덕션 `_is_better_result`(usable-first→최신 announced)와 갈릴
수 있다. confound(base-fallback) 버킷을 분리 집계하고 정직한 기초금액 base 행에서만
비대칭을 읽는다.

## 8. 비목표 / red line

- 이 문서·스크립트는 **라벨·predictor·guardrail·데이터셋 코드를 변경하지 않는다**.
- predictor guardrail의 카테고리/법정 하한 `max()` 구조는 어떤 정합 옵션에서도 불변이다.
- pgvector 384 차원, 시간 누수 차단 규칙 불변.
- 라벨 relabel/재캘리는 반드시 홀드아웃 재검증과 함께, 밴드/floor 트랙과 조율된
  한 패스로만 수행한다(단독 재학습 금지).

## 9. 로드맵 연계

- 밴드/floor 재캘리브레이션 트랙: `docs/roadmap.md`(§ Phase 3 후속, 실투찰 재검증)와
  `docs/operations/real-bid-195-revalidation.md`. 본 분석은 그 트랙의 **라벨 측
  완결 조건**이다.
- 관련 메모리: [[rebid-diagnosis-base-overwrite]](floor 앵커 base 불일치·재캘리 보류),
  [[agency-band-yega-basis-alignment]](#195 E[사정률] 밴드 정합),
  [[prediction-bid-base-business-amount]](투찰 base=기초금액),
  [[recurring-bug-root-cause-basis]](basis 표현 부재 근본원인).
