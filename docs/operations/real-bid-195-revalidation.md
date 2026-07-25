# 실투찰 2건 #195 발주처 밴드 가설 재검증 (2026-07-25)

## 목적

Phase 3 착수 후속 ①(로드맵 "다음 우선순위" 1번)의 트리거 — 실투찰 2건의 낙찰자
확정 수집 — 이 발생함에 따라, #195 발주처 밴드 base 정합화 가설을 확정 낙찰가
기준으로 재검증한다. 이 문서는 재검증 **결과**와 재캘리브레이션 **결정(보류)**,
그리고 다음 구체 가설을 기록한다.

정직 명세(§2) 준수: 아래 수치는 DB 실측값과 predictor 재현값이며, 낙찰 확률·승률을
조작하지 않는다. 시크릿·사업자 개인정보는 포함하지 않는다.

## 대상

| 항목 | 60395 | 60401 |
|---|---|---|
| 공고번호 | `R26BK01627948` | `R26BK01628093` |
| `BidDecisionRecord` id | 15082 | 15083 |
| 발주처 | 한국수산자원공단동해본부 | 한국수산자원공단동해본부 |
| 카테고리 | service | service |
| operator_id | 1 (canonical) | 1 (canonical) |
| submitted_floor_rate | 0.88 | 0.88 |
| submitted_at | 2026-07-17 | 2026-07-17 |
| `award_outcome` (영속화) | `lost` | `lost` |

두 건 모두 동일 발주처(동해바다숲 사업)·동일 밴드(`service_price_competitive`)의
복수예비가격 적격심사(floor-bound) 구간이다.

## 확정 낙찰자·낙찰가 (2026-07-20 수집, `award_outcome=lost` 영속화·텔레그램 통지)

낙찰률(`TenderResult.winning_rate`)은 **낙찰가 / 예정가** 기준으로 저장된다.

| | 60395 | 60401 |
|---|---|---|
| 낙찰자 | (주)해림 | (주)해담 |
| 낙찰가 | 77,308,840원 | 48,094,470원 |
| 낙찰률(예정가 대비) | 88.001% | 88.019% |
| 예정가(추정, `budget_estimate`·낙찰가/낙찰률 역산 일치) | 87,849,956원 | 54,641,009원 |
| 기초금액(사업금액, predictor `bid_base`) | 88,090,450원 | 55,011,750원 |
| 사정률(예정가/기초금액) | 0.99727 | 0.99326 |
| 낙찰가/기초금액 | 87.761% | 87.426% |

낙찰자는 두 건 모두 실현 예정가의 **88%(법정 낙찰하한)에 착지**했다(floor-bound
게임에서 최저적격). 사정률 < 1이므로 예정가 < 기초금액이고, 같은 88%라도
**기초금액 기준으로는 87.76% / 87.43%** 로 내려앉는다.

> 데이터 caveat: `TenderResult.winner_name`은 이 두 행에서 미저장(NULL)이며, DB에
> 영속화된 것은 `winning_amount`·`winning_rate`·`award_outcome=lost`다. 낙찰자
> 상호(해림/해담)는 낙찰 통지·기존 검증 기록([[real-bid-award-verify-gaps]])에서
> 확인된 값이다. 기초금액은 `HistoricalData.base_amount_basis='derived-yega'`의
> reserve-recovered 추정치(`base_amount_estimated`)이므로 개찰 시 공표된 실기초금액과
> 소폭 다를 수 있다(사정률 측정 불확실성의 원인).

## 내 실투찰가 · 패찰

| | 60395 | 60401 |
|---|---|---|
| 실투찰가 | 77,529,790원 | 48,475,270원 |
| 예정가 대비 | 88.253% | 88.716% |
| 낙찰가 대비 | +220,950원 (+0.252%p) | +380,800원 (+0.697%p) |
| 결과 | 패찰(적격이나 낙찰가보다 높아 밀림) | 패찰(동일) |

두 건 모두 **적격 구간 안**이지만 낙찰가보다 높게 투찰해 밀렸다(적격 + 순위 밀림).
낙(실격)은 아니다.

## 현재 predictor 추천 비교 (독립 재현, 2026-07-25)

재현 경로: `OpportunityAnalysisService().analyze_project(db, project,
OpportunityAnalysisRequest(project_id=pid, legal_floor_bid_rate=0.88))` — api 컨테이너,
main 저장소 코드(`0f2ab80`, 이 워크트리 base와 동일). predictor 코드는 이 PR에서
변경하지 않았다.

> 왜 `legal_floor_bid_rate=0.88`을 넘기는가: 0.88은 **운영자가 실제 투찰한 하한율
> (`submitted_floor_rate=0.88`)이자 KONEPS 공고 API로 확인된 이 두 공고의 실제 법정
> 낙찰하한(소액수의견적 88%)** 이다(낙찰자 88.001%/88.019%와도 정합). 따라서 이 재현은
> "우리가 이길 수 있었나(would-have-won)"를 정확히 모델링한 것이며, 아래 +0.34%p/
> +0.68%p 과추천 결론은 이 실 하한 기준에서 유효하다. **주의**: 이 override는 실제
> 라이브 monitor 경로가 넘기는 값이 아니다 — 라이브 경로(override 없음)는 아래
> "라이브 경로 갭"에서 별도로 재현하며, 이 두 settled 공고에서 정반대 방향(87.x%,
> 실격 리스크)으로 갈린다.

| 값 | 60395 | 60401 |
|---|---|---|
| `recommended_amount` | 77,607,690원 | 48,465,360원 |
| ┗ 예정가 대비 | 88.341% | 88.698% |
| ┗ 기초금액 대비 | 88.100% | 88.100% |
| ┗ **확정 낙찰가 대비** | **+298,850원 (+0.340%p)** | **+370,890원 (+0.679%p)** |
| `pp.floor_price` | 77,519,596원 | 48,410,340원 |
| ┗ = 0.88 × 기초금액(정확 일치) | 0.88 × 88,090,450 | 0.88 × 55,011,750 |
| ┗ 예정가 대비 | 88.241% | 88.597% |
| ┗ 확정 낙찰가 대비 | +210,756원 (+0.240%p) | +315,870원 (+0.578%p) |
| `pp.floor_bid_rate` | 0.88 | 0.88 |
| `pp.safe_floor_bid_rate` | 0.881 | 0.881 |
| `pp.predicted_price` (raw, rate 0.90) | 79,281,400원 | 49,510,570원 |
| `procurement_rate_band` | service_price_competitive | service_price_competitive |
| `floor_from_agency` / `ceiling_from_agency` | false / false | false / false |
| `floor_guardrail_source` | legal | legal |
| `bid_target_menu` | null | null |

재현값은 상위 에이전트가 실측한 값(recommended/floor/predicted)과 **원 단위까지
일치**한다. `recommended_amount`는 `safe_floor_price`(0.881 × 기초금액을 **10원 단위로
올림**, ceil-to-10원: 77,607,686.45→77,607,690 / 48,465,351.75→48,465,360)와 같다 —
즉 추천가 = 안전마진 얹은 법정 하한이다.

## floor 앵커 base 규명 (핵심)

`pp.floor_price`는 **0.88 × 기초금액(사업금액)** 에 앵커된다. 예정가가 아니다.

- `pp.floor_price = _guardrail_price(budget, floor_bid_rate) = round(budget × floor_bid_rate)`
  — `app/ai/price_prediction.py:845-848`. 여기서 `budget`은
  `_build_price_prediction`이 넘긴 `bid_base = resolve_notice_bid_base(db, project)`
  = 기초금액/사업금액이다 — `app/services/opportunity_analysis.py:506, 517`.
- `floor_bid_rate = max(configured_floor, normalized_legal_floor)`
  = `max(0.87 카테고리, 0.88 법정) = 0.88` — `app/ai/price_prediction.py:760-764`
  (`_max_optional_rate`) + `app/ai/guardrail_core.py:124-199`.
- 검산: 0.88 × 88,090,450 = 77,519,596(정확), 0.88 × 55,011,750 = 48,410,340(정확).

즉 하드 floor는 예정가가 아니라 기초금액에 88%를 곱한다. 실현 법정 하한은 88% ×
**예정가**인데, 예정가 = 사정률 × 기초금액(사정률 0.9973/0.9933 < 1)이므로,
0.88 × 기초금액 > 0.88 × 예정가 = 실현 하한이다. 그 차이가 60395 +0.24%p /
60401 +0.58%p(예정가 기준)이며, 낙찰자는 실현 하한에 딱 붙었으므로 하드 floor —
그리고 그 위의 모든 추천 — 은 낙찰가 위에 뜬다.

## 근본원인

법정 낙찰하한율 0.88은 **예정가**의 비율인데 predictor는 이를 **기초금액**에 곱한다
(`floor_price = 기초금액 × 0.88`). 사정률 = 예정가/기초금액 < 1일 때, predictor
하드 floor(0.88 × 기초금액)가 실현 하한(0.88 × 예정가)을 (1 − 사정률) × 0.88 ×
기초금액 ≈ +0.24%p / +0.58%p 만큼 구조적으로 초과한다. 추천가는 여기에 +0.1%p
안전마진(safe_floor 0.881)을 더해 낙찰가 대비 +0.34%p / +0.68%p 위에 착지한다.

#195의 E[사정률] 변환(`convert_yega_band_to_base`)은 이 basis 불일치를 고치도록
설계됐고, 실제로 (a) 발주처 밴드 floor/ceiling edge(`app/ai/guardrail_core.py:196, 248`)
**와** (b) **공사 법정-tier 하드 floor**(`guardrail_core.py:186` — 공사 카테고리에서는
하드 법정 floor도 이미 E[사정률] 변환을 받는다)에 적용된다. 그러나 이 두 공고가 걸리는
**service `legal_floor_bid_rate` override 경로**(=award_floor_rate / 요청 override 0.88)에는
변환이 없이 `max()`로 folded 되므로(`app/ai/price_prediction.py:760-764`) E[사정률]가
적용되지 않는다. 즉 "하드 법정 floor엔 미적용"은 **공사가 아니라 service override
경로에 한해** 성립하며, 그 범위에서 상위 에이전트의 **비대칭 가설이 코드로 확인**된다.
(따라서 아래 "다음 가설" 0.88×E[예정가]는 사실상 **line-186의 공사 변환 패턴을 service
override floor 경로로 확장**하는 것과 같다.)

게다가 이 두 공고에서는 변환된 발주처 밴드
(0.8806~0.882 × E[사정률] 0.9952 = 0.87656~0.87777 of 기초)가 법정 floor 0.88 **아래**라,
`floor = max(0.87656, 0.88) = 0.88`로 **#195 보정이 max()에 의해 지워진다**. 요약하면
#195는 이 floor-bound 공고(공표 하한 ≥ 변환된 발주처 밴드)에서 inert다.

### 라이브 경로 갭 (override 없이 재현 — 위 0.88 재현과 방향이 반대)

위 표는 "우리가 이길 수 있었나"를 실 법정하한 0.88로 모델링한 것이다. 실제 라이브
추천을 생성하는 monitor/telegram 경로(`app/services/opportunity_monitoring.py:982`)는
`OpportunityAnalysisRequest`에 **`legal_floor_bid_rate` override도, `agency_name`도**
넣지 않는다. 그리고 이 두 공고는 settled·#201 이전 수집이라
**`Project.award_floor_rate`가 DB에서 NULL**이다(#202 backfill은 open 공고만 대상).
따라서 라이브 경로는 0.88이 아니라 **0.87 service 카테고리 floor로 폴백**한다.

override 없이 재현한 결과(`analyze_project(db, project,
OpportunityAnalysisRequest(project_id=pid))`):

| 값 | 60395 | 60401 |
|---|---|---|
| `award_floor_rate` (DB) | NULL | NULL |
| `floor_bid_rate` | 0.87 | 0.87 |
| `floor_guardrail_source` | category | category |
| `legal_floor_bid_rate` | null | null |
| `recommended_amount` | 76,726,790원 | 47,915,240원 |
| ┗ 예정가 대비 | 87.339% | 87.692% |
| ┗ **확정 낙찰가(88.00/88.02%) 대비** | **−0.662%p** | **−0.327%p** |
| `floor_from_agency` / `bid_target_menu` | false / (n/a) | false / (n/a) |

즉 라이브 경로 추천(safe_floor 0.871 × 기초금액 = 87.1% of 기초 = 87.34%/87.69% of
예정가)은 **88% 실현 낙찰하한 아래**에 착지한다 — 이는 위 0.88 재현의 "과추천"과
**정반대 방향인 실격(낙) 리스크**다. 0.88(실투찰)과 0.87(라이브 폴백)의 차이가 이 두
공고에서 방향을 가른다.

**따라서 라이브 갭은 이중이다** — 둘 다 캘리브레이션이 아니라 backend 배선 이슈(별건):

- (a) **발주처 밴드 dormant**: 라이브 경로가 `agency_name`을 미전달해 발주처 밴드가
  애초에 매칭되지 않는다(`floor_from_agency=false`·`bid_target_menu=null`로 실증). 단,
  이 두 공고에서는 변환된 발주처 밴드가 0.88 아래라 매칭됐어도 실 하한 위 착지는
  못 시킨다.
- (b) **award_floor_rate 미배선 → 0.87 폴백**: settled 공고에 published 하한이 NULL이면
  라이브 경로가 실현 88% 하한 아래(0.87 카테고리 floor)로 추천해 **실격 리스크**를
  낸다. 이는 위 basis 과추천의 거울상이다.

## 정직한 반대 관점 (버그가 아니라 캘리브레이션 트레이드오프)

기초금액-앵커 floor를 단순 "버그"로 단정하면 안 된다. floor-bound 적격심사는
**비대칭**이다 — 실현 하한 위 ε는 순위로 밀리지만(패찰), 실현 하한 **아래**는
실격(낙하)이다. 예정가는 개찰 시 복수예비가격 추첨으로 정해져 투찰 전에는 미지다.
따라서 0.88 × 기초금액은 **낙하-안전(보수적) posture**다: floor를 실현 하한 쪽으로
내리면 승률은 오르지만, 실현 사정률이 기대보다 높으면(예정가 상향) 하드 floor가
실현 하한 밑으로 내려가 실격 위험이 커진다. 이번 2건은 사정률<1로 predictor가
높게 떴지만, 사정률>1인 공고에서는 같은 posture가 낙하를 막는다.

## 재캘리브레이션 결정 — **보류 (이 PR에서 predictor 코드 변경 없음)**

표본은 2건(동일 발주처 동해바다숲 · 동일 밴드 `service_price_competitive` · 사정률
0.9973/0.9933)뿐이다. 정직 명세(§2)와 기존 교훈("2~4 표본 과적합 금지, 밴드
재캘리브레이션 보류")에 따라 밴드/floor 계수를 지금 바꾸지 않는다. 근거:

1. **표본 부족·과적합**: n=2, 단일 발주처, 단일 밴드. 기존 발주처 E[사정률]=0.9952도
   postmortem 2공고로 캘리브레이션된 HYPOTHESIS 값이며(`app/core/config.py` 주석),
   같은 2건으로 재도출·재검증하면 순환(leakage)이다.
2. **red line 인접**: 다음 가설(아래)은 하드 floor를 내리는 방향이라 낙하-안전과
   승률의 트레이드오프다. 하드 floor는 red line 인접 guardrail이므로, E[사정률]의
   평균뿐 아니라 **상단 tail(고-사정률 분위수)** 까지 다수 floor-bound 개찰로
   추정한 뒤에야 손대야 한다.
3. **측정 불확실성**: `bid_base` 자체가 reserve-recovered 추정치(basis=derived-yega)라
   사정률에 측정 오차가 있다.

### 다음 구체 가설 (고정 — 추가 표본 축적 후 검증)

> **floor 앵커 base 정합**: 법정/하드 floor도 예정가→기초금액 변환을 거치게 한다.
> `floor_price = 0.88 × E[예정가] = 0.88 × (E[사정률] × 기초금액)`. 즉 예정가-basis
> 법정 하한율 0.88을 기초금액-basis(0.88 × 사정률)로 낮춰 곱해, 하드 floor를 실현
> "88% of 예정가" 하한과 정합화한다.

단, 무조건 평균 E[사정률]로 내리지 말고 **보수적 고-사정률 분위수 + 버퍼**를 써
낙하 리스크를 통제한다. 검증 게이트:

- 다수 floor-bound 개찰(발주처·밴드 교차)로 사정률 분포(평균 + 상단 tail) 추정.
- clean-basis(`base_amount_basis='clean'`) 행만 사용(파생 base로 재도출 시 예정가-basis
  오염 재주입 — `app/core/config.py` WARNING).
- 홀드아웃 비교: 변환 적용 전/후로 (a) 낙찰가 근접 오차, (b) **낙하(실격) 건수**가
  red line을 넘지 않는지 동시 측정. 승률만 보고 내리지 않는다.
- 별건(백엔드) — 이중 라이브 배선 갭(위 "라이브 경로 갭" 참조): (a) 라이브 monitor
  경로가 `agency_name`을 project.issuing_agency로 채우도록 wiring할지, (b) settled
  공고의 `award_floor_rate` NULL로 인한 0.87 폴백(실현 88% 하한 아래 추천·실격 리스크)을
  어떻게 배선할지 결정. 둘 다 캘리브레이션이 아니라 배선 문제라 backend-builder 소관.

## 남은 리스크 / 후속

- **표본 축적 대기**: floor-bound 개찰 표본이 더 쌓여야 E[사정률] 분포와 낙하 tail을
  추정할 수 있다(로드맵 우선순위 6 "개찰 데이터 폭 확장"과 연결).
- **이중 라이브 배선 갭(별건·backend)**: (a) `agency_name` 미전달로 발주처 밴드
  dormant, (b) `award_floor_rate` NULL(settled·#201 이전)이면 라이브 경로가 0.87
  카테고리 floor로 폴백해 실현 88% 하한 **아래**로 추천(실격 리스크) — override로 실
  하한 0.88을 넣은 위 재현의 과추천과 정반대 방향. backend-builder와 배선 논의 필요.
- **낙찰자 상호 미저장**: `TenderResult.winner_name` NULL — 사후분석 폭 확장 시
  낙찰자·참가자 상호 수집 보강 필요.
- **재캘리브레이션은 코드 변경을 동반하므로** 착수 시 guardrail 회귀 테스트(하한
  미만 차단)와 낙하-건수 홀드아웃 게이트를 필수로 동반한다.

## 검증

- predictor 재현값(원 단위) = 상위 에이전트 실측값 일치(위 표). predictor 코드 무변경
  → 회귀 없음(app/ 변경 0).
- 변경 파일: 본 문서(신규) + `docs/roadmap.md`(상태 정정)뿐.
