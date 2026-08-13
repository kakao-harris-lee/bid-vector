# Phase 3 (ML 3축 재편 — 낙찰가 축) 설계 — KDE·기대가치

> 상태: **설계 확정(2026-08-13 운영자 승인)** — 1차 목적함수 = 옵션 A(win-proxy 곡선만),
> 경쟁 데이터 = winner-only 유지(참가자수 백필 안 함), EV(원가율 가중)는 후속 분리.
> 3축 재편(2026-08-09 운영자 확정)의 세 번째 축(로드맵의 "Phase 3 — 제한 실증 서비스"
> 단계명과 다른, **ML 트랙 3축의 세 번째**다 — n2). 선행: Phase 1 예정가 분포 엔진(#362),
> Phase 2 낙찰률 GBM(#363/#364/#366/#367).
> 이 문서는 증거와 권고까지다. 배선 실행 결정은 게이트(§9) 통과 후 운영자 소관이다.

## 결론 요약 (정직 헤드라인)

1. **winner-only 데이터로 정직하게 식별되는 추정 대상은 정확히 하나다: `would_have_won_final=="eligible_favorable"`(§2·§3) 의 투찰률 함수 `WW(b)`.** 낙찰이란 "하한 이상 최저가 생존자"이므로, 관측된 낙찰자 착지점 하나만으로도 "내 후보 `b` 를 그 개찰에 끼워 넣었으면 최저 생존자였는가"라는 **반사실(counterfactual) 삽입** 질문은 편향 없이 계산된다(코드 술어 `floor_price ≤ our ≤ winning_amount`, `settlement.py` 와 문자 그대로 동일). 참가자 전체 분포는 필요 없다. 단 `_final` 의 가격 외 적격심사(자격·서류)는 모형화하지 않는다 — 관측 낙찰자는 정의상 통과자다(§3.2·§11).
2. 반대로 **winner-only 로는 식별 불가능한 것**: 내 진입이 경쟁자 행동을 바꾸는 균형(게임이론)효과, 참가자 전체의 투찰 분포, 승리 마진(runner-up 거리). Phase 3 는 전자만 추정하고 후자는 명시적으로 제외한다 — 이것이 정직 명세의 핵심 경계다.
3. **불확실성의 출처는 두 축으로 분리된다.** (a) 하한 생존 = 사정률 추첨 `a` — 메커니즘이 완전 열거라 분포 `F_a` 자체의 형태는 이론적으로 특정되나, 열린 공고 서빙에선 유사 공고 사후분포로 추정(train/serve skew 안전). (b) 경쟁(낙찰자 하한 초과 마진 `Δ`) = winner-only **표본 추정**(KDE/ECDF). 두 축의 **서빙 합성**은 `a ⊥ Δ` 가정에 의존하며, 정산 코퍼스 실측 `corr(a,Δ)=−0.0045`(n=2,152)가 이를 지지한다. 단 **정산 코퍼스 채점은 분해 없이 결합 경험추정(층 A)** 으로 하므로 이 가정이 틀려도 접지 진리는 흔들리지 않는다.
4. **1차 슬라이스의 산출은 마진이 아니라 `WW(b)` 곡선 하나다.** 원가 데이터가 없으므로 기대가치(EV)는 운영자 선언 원가율 `c`(OperatorStrategy 선언 필드)가 생긴 뒤의 **얇은 후속 층**이다. 기본 권고는 소박한 `argmax_b WW(b)` 가 아니라 **실격 예산 제약** `b* = argmax_{b: P(φ>b) ≤ α} WW(b)` 다(`WW` 는 실격과 패찰을 등가 취급하지만 운영자는 아니다 — §7·M2). 이 곡선은 현행 이산 3점(conservative/base/aggressive)을 **연속 곡선으로 포섭**한다.
5. **배선 가능성은 코드가 아니라 데이터 문제다.** 정산 코퍼스 clean-basis 쌍은 2,153(실측 2026-08-13)이지만, 추정 대상은 **가격경쟁(floor_bound) 레짐**으로 좁혀지고 다시 `공종 × 금액대` 부분셀로 쪼개진다. candidate-selector 판정이 못박은 **부분셀 n≥150** 게이트는 용역 쪽에서 여전히 부족할 개연이 높고, 결론은 Phase 2c 처럼 "아직 판정할 수 있는 셀이 없다"일 수 있다 — **이 가능성을 사전에 인정한다.**
6. 법정 하한 red line(#221 max()-only)은 구조적으로 보존된다: 하한 미만 `b` 는 생존항이 0 으로 죽고, 최종 추천은 guardrail 이 게시 법정하한으로 clamp 한다. Phase 3 는 하한을 **선점·우회하지 않는다.**

---

## 1. 배경

Phase 1(#362)이 예정가를 시계열이 아니라 복권형 생성 메커니즘으로 못박고(추첨 4/15 완전 열거 `DrawMeanDistribution.support`), 그 본문이 예고한 후속 표면이 이것이다 — "완전 열거 지지집합이 **하한 부근 경쟁 밀도 추정**의 입력". Phase 2(#364~#367)는 낙찰률 GBM 으로 `winning_amount/신뢰 기초금액` 을 예측하되 승격 판정 창을 아직 못 얻었다(성숙도 embargo 대기).

두 개의 판정문이 공히 **남는 개선 축**을 같은 곳으로 지목한다:

- `docs/operations/floor-anchor-recalibration-verdict.md`: "정확한 floor 와 안전한 floor 는 추첨 랜덤 때문에 양립 불가. **남는 개선 축은 앵커가 아니라 recommended candidate selector** — 하한 위 어디에 설지의 선택 정책."
- `docs/operations/candidate-selector-rederivation-verdict.md`: 유일 생존 후보 P1'(service 한정→conservative)이 부분셀 n=80<150 으로 draft 보류. "floor_bound 정책은 **반드시 group-scoped**, argmin 셀 표 금지(다중비교)."

Phase 3 는 이 selector 트랙의 **원리적 후계자**다: 휴리스틱 스탠스(3점 이산 승격)를 데이터로 다시 세우는 대신, **낙찰자 착지점 분포에서 유도한 win-proxy 곡선**으로 하한 위 착지점을 연속적으로 고른다.

---

## 2. 도메인 물리학과 표기

| 기호 | 정의 | 투찰 시점 관측 | 비고 |
|---|---|---|---|
| `B` | 기초금액 (`base_amount`, clean) | **O** | 서빙이 통제·관측하는 축(§Phase 2 SERVING_DENOMINATOR clean-base) |
| `{r_j}` | 게시 복수예비가 15개 | ✕ (열린 공고) | 정산 공고에서만 |
| `E` | 예정가 = 추첨 4개 예비가 평균 | ✕ | 개찰 후 확정 |
| `a = E/B` | 사정률 | ✕ | ±~1%p 추첨. 07-27 코어(532): mean 0.9974·>1 37.6% / 현 clean 2,152: mean 0.9913·median 0.9975·>1 37.0% (m5) |
| `f` | 게시 하한율 (`award_floor_rate`, era-correct #197/#198) | **O** | 공사 87.495~89.745% |
| `φ = f·a` | 실현 하한율(기초-basis) = `L/B`, `L=f·E` | ✕ | `a` 를 통해 랜덤 |
| `ω = winning_amount/B` | 낙찰자 투찰률(기초-basis) | — | 정산 코퍼스 관측, `winning_amount>0` 필터 필수 |
| `w = winning_rate = winning_amount/E` | 보고 낙찰률(예정가-basis) | — | `= ω/a`. 보고율=예정가-basis 는 floor-anchor 문서가 rel-err 1e-5 로 독립 검증 |
| `Δ = w − f` | 낙찰자 하한 초과 마진(예정가-basis), `≥ 0` | — | **KDE 추정 대상**. `a` 소거는 basis 불변성 진술일 뿐 독립성 논거가 아님 — 독립 논거는 실측 `corr(a,Δ)=−0.0045`(§6) |
| `b` | 후보 투찰률(기초-basis) `= P/B` | — | **선택 변수** |

낙찰 판정(적격심사): 하한 이상(`≥ φ`) 최저가부터 캐스케이드 — 즉 **최저 생존자가 낙찰**. 수의(견적)는 개찰 1위 확정으로 하한 경쟁이 없다 → **추정 대상에서 제외**(레짐 하드 필터).

낙찰자는 하한 바로 위에 밀집한다: 하한 대비 중앙값 공사 +55.5bp, 용역 +13.3bp. 실투찰 패배 2건의 낙찰자는 실현 하한 +0.1bp/+1.9bp 에 착지했고, 3번째 실투찰은 사정률 +0.558% 추첨으로 실격했다 — **하한 밀착은 축소 불가능한 추첨 게임**(floor-anchor 판정).

---

## 3. Estimand 와 정직 명세 매핑

### 3.1 반사실 삽입 win-proxy — winner-only 로 정확 식별

후보 `b` 를 **실현된 투찰 집합에 그대로 끼워 넣었을 때** 최저 생존자가 되는가:

- `b < φ` (하한 미만): 실격 → 패.
- `φ ≤ b ≤ ω`: 현 낙찰자보다 낮거나 같은 생존자 → **내가 새 최저 생존자 → 승**(경계 `b=ω` 포함, m2).
- `b > ω`: 낙찰자가 여전히 최저 → 패.

따라서
```
WW(b) = P( φ ≤ b ≤ ω )                                   (식 1)
```
이 사건은 개찰당 **낙찰자 `ω` 와 하한 `φ` 두 값만** 필요하다. 참가자 전체 분포가 필요 없다 — winner-only 데이터가 이 estimand 를 **정확히 식별**한다. 그리고 식 1 은 문자 그대로 저장 라벨 `would_have_won_final=="eligible_favorable"` 의 정의다: `app/services/paper_bidding_backtest/settlement.py` 의 `_settle_verdict` 가 `floor_price ≤ our_amount ≤ winning_amount` 를 그 라벨로 판정한다(식 1 과 동일). **`would_have_won_price_only` 가 아니다**(B1) — 후자는 `|b_rate − w_rate| ≤ 0.003/0.01` 의 **양측 근접 밴드**라 모양조차 다르다(±0.3%p 봉우리 vs `WW` 의 고원). Phase 3 의 산출 필드·설명은 `_final` 계열 의미론에 못박는다.

| 정직 명세 축 | Phase 3 매핑 |
|---|---|
| `probability_score` = 가격 적합도, P(낙찰) 아님 | `WW(b)` 는 **반사실 가격 우위 확률**이지 실제 낙찰 확률이 아님. 필드명·설명에 명시 |
| `would_have_won_final == "eligible_favorable"` | `WW(b)` 가 **정확히 이 라벨의 `b`-함수**(식 1 = 코드 술어). 새 필드는 `_final` 계열 어휘 재사용 |
| `would_have_won_price_only`(양측 근접 밴드) | Phase 3 대상 **아님** — 모양이 다름(±0.3%p 봉우리 vs 고원). `WW` 와 혼동 금지 |
| `_final` 의 가격 외 적격심사 | Phase 3 **모형화 안 함**(§3.2·§11) — 관측 낙찰자는 정의상 자격·서류 통과자 |
| guardrail 우회 금지 | `WW` 는 하한을 선점하지 않음. guardrail max()-only 그대로 |

### 3.2 winner-only 가 **못** 하는 것 (명시적 비-estimand)

- **P(랜덤 참가자 이김)**: 낙찰자는 참가자의 최소 순서통계량이지 무작위 표본이 아니다. `1 − G(δ)` 는 "낙찰 마진이 δ 초과" = "실현 최저 생존자가 내 마진 위" 이지 "임의 경쟁자를 이김"이 아니다.
- **승리 마진 / runner-up 거리**: 2등 이하 투찰액 미관측(참가자수 `opening_participant_count` = 3/91,364, 실측 2026-08-13).
- **균형 효과**: 내 진입이 타 참가자 투찰을 바꾸는 게임이론적 반응. 반사실은 "타 투찰 고정" 가정이다 — 이 가정을 설명에 공시.
- **`_final` 의 가격 외 적격심사(자격·서류·PQ)** (B1): 코드 `_settle_verdict` 는 `floor_price ≤ our ≤ winning` **가격 조건만** 판정한다. 관측 낙찰자는 **정의상** 적격 통과자이므로, `WW` 는 "적격 통과 조건 하의 가격 경쟁 승리"만 추정하고 **적격 통과 확률 자체는 모형화하지 않는다**.

---

## 4. 데이터와 품질 사다리

읽기 전용 실측(2026-08-13):

| 단계 | 건수 | 근거 |
|---|---|---|
| `award_floor_rate` 보유 공고 | 12,127 | |
| `winning_rate>0` tender_results | 60,050 | |
| **쌍 (floor_rate × winning_rate>0)** | **3,182** | 07-27 판정 당시 1,031 → 3배 |
| 그중 예비가 보유 | 3,073 | |
| 그중 **basis clean** | **2,153** | 하한 앵커 판정의 clean 714 대비 3배 |
| `opening_participant_count` 보유 | **3** | winner-only 제약 확정 |

**품질 사다리는 신설하지 않고 재사용한다**(#199 basis 태깅 / #358 suspect-ratio / #274·#296 적용범위·게시하한 개연 / floor-anchor 문서의 코퍼스 사다리). clean 2,153 에서 추가 필터(적용범위 applicable, 게시하한 개연, 예비가-기초 정합, **floor_bound 레짐 한정**, `winning_amount>0`)를 거치면 07-27 코어(532)의 논리로 볼 때 **가격경쟁 코어는 대략 1,200~1,600 추정 — 정확값은 재현 시 실측 필요**. 그 코어를 `공종 × 금액대` 부분셀로 쪼갠 **하드 필터 전 상한** 실측(clean 2,152, 아래 표)에선 **12셀 중 5셀만 n≥150** 이고, floor_bound·게시하한 개연·`winning_amount>0` 필터를 더하면 통과 셀은 더 준다. §4 초안의 "용역 쪽 미달"은 **과소진술**이었다 — **공사도 3셀이 미달**(상한 기준)이다.

**셀 깊이 상한(하드 필터 전, clean 2,152, 실측 2026-08-13 — m6)**:

| 공종 | 금액대별 n (상한) | n≥150 셀 |
|---|---|---|
| construction | 30m_100m 648 · 100m_300m 451 · 300m_1b 169 · 10m_30m 85 · gte_1b 41 · lt_10m 13 | **3/6** |
| service | 30m_100m 425 · 10m_30m 154 · 100m_300m 73 · lt_10m 57 · 300m_1b 23 · gte_1b 14 | **2/6** |

**함정 반영**: `winning_amount` 미정산=**0.0(NULL 아님)** → `>0` 필터 필수. `winning_rate`=예정가-basis 전제는 floor-anchor 문서가 독립 검증. 07월 시기 집중·공사 수집 2026-06-30 개시(레짐 단절) → era-correct `f` + 성숙도/날짜창(§8)으로 처리.

**추가 함정(리뷰 실측)**:
- **n5 — `winning_rate` basis 주석 오류**: `settlement.py:49-52` 주석은 `winning_rate` 를 base-relative(`winning_amount/base_amount`)로 단언하고 결측 시 base 로 폴백하지만, 실측은 **예정가-basis** 다(상대오차 5.64e-06 vs base 가정 5.64e-03, 2,152/2,152). PR2 는 이 폴백 대신 `winning_amount/B`(clean)를 직접 재계산해 `ω` 를 얻는다.
- **m1 — `award_floor_rate==1.0` 오염**: 마진 `Δ<0` 실측 4/2,152 건은 전부 `award_floor_rate==1.00000` 오적재(`floor_applicability.py` 알려진 함정)다. 커널은 이를 **reject**(clamp 금지)하고, PR2 품질 사다리에 `f==1.0` 필터를 명시한다.
- **m4 — 저장 `_final` 라벨 하한 불일치**: 저장 라벨의 하한은 `_resolve_floor_bid_rate(category)`(≈0.87 상수)×**예정가**라 게시 `award_floor_rate` 와 **실측 100% 불일치**(공사 1,407/1,407·용역 746/746, 지배 격차 2.745%p ≈ 마진 중앙값의 6배). PR2 는 저장 라벨과 직접 대조하지 않고 **게시 `f` 로 술어를 재계산해 채점**한다.

---

## 5. 추정기 명세

### 5.1 층위 (estimand/backtest = 층 A → serving = 층 C)

**층 A — Estimand/backtest(가정 0개, 정산 코퍼스)**: `WW(b)` 를 개찰당 관측 `(φ_i, ω_i)` 로 직접 결합 경험추정. 각 행은 **자기 `f_i`** 를 그대로 담는다(`φ_i = f_i·a_i`).
```
WW_emp(b) = (1/N) Σ_i 1[ φ_i ≤ b ≤ ω_i ]        (셀 내, b·B 는 rate 축이라 공고 크기 무관 비교 가능)
```
독립 가정도 KDE 도 필요 없다. 이것이 곡선의 **접지 진리(ground truth)**이며 백테스트 채점 기준이다 — 단 자기 `f_i` 를 담으므로 **자기 모집단 채점에만** 유효하고, 다른 `f` 의 대상 공고로 전이하려면 §6 의 f-재기준이 필요하다(M5).

**층 B(폐기) — 생존항만 추첨 CDF 로 적분하는 "정밀화"는 편향을 넣는다**(M1): 실현 `ω_i` 를 고정한 채 `φ_i` 만 Phase 1 정확 CDF 로 적분하는 것은 Rao-Blackwell 화가 **아니다**. `ω` 는 `a` 의 함수이기 때문이다(하한이 움직이면 최저 생존자가 갈아탄다) — 실측 `corr(a, ω) = +0.587`(clean n=2,152). 낮은 추첨 `a` 는 하한 `φ=f·a` 와 낙찰자 `ω=a(f+Δ)` 를 **함께** 낮추는데, `ω` 를 고정하고 `φ` 만 낮추면 생존 구간 `[φ, ω]` 가 실제보다 넓어져 **`WW` 를 과대추정**한다. 올바른 RB 화는 결합 `(φ(a), ω(a))` 위에서 적분해야 하고, 그 반사실 `ω(a)`(다른 추첨 하의 낙찰자 착지)는 §3.2 가 **식별 불가**로 선언한 대상이다. 따라서 정산 코퍼스 채점은 분해 없이 **층 A 결합추정**으로만 한다.

**층 C — Serving(어려운 부분)**: 열린 공고는 `φ, ω` 둘 다 미관측. 모형화:
- **생존 `φ`**: Phase 1 사정률 예측분포 `F_a`(distribution predictor 의 계층 수축 사후 + 추첨 분산; 대상 공고 예비가 미사용 → train/serve skew 안전) → `P(a ≤ b/f)`.
- **낙찰자 마진 `Δ`**: 셀 단위 **경계보정 KDE/ECDF** `G_cell(δ)`(예정가-basis, `a`-free 라 공고 간 전이 가능).
- 합성은 §6.

### 5.2 낙찰자 마진 분포 `G` — 경계 보정

`Δ = w − f ≥ 0` 는 0 에서 **절단**되고 0+ 에 **뾰족한 스파이크**(하한 밀착)를 가진다. 우리가 실제로 소비하는 것은 밀도가 아니라 **CDF `G(δ)`** 이므로:

- **기본 추정기 = 경험 CDF(ECDF)**: 단조·경계 안전·가정 최소. 스파이크를 왜곡 없이 표현한다.
- **평활 = 경계보정 KDE**(반사법 reflection 또는 `log(Δ+ε)`·Beta 변환): 희소 셀·서빙 전이에서만 사용. **소박한 가우시안 KDE 금지**(0 아래로 질량 누출 → 스파이크 과소·마진 과대 → 낙관 편향).
- **셀 스코프**: `floor_bound 레짐`(하드 필터) → `공종 × 금액대`. 조달방식(near_100/수의)은 마진 정의 자체가 성립 안 하므로 **레짐이 셀 키가 아니라 게이트**다.
- **계층 수축(Phase 1 κ *패턴* 재사용, *값* 재사용 금지)**: 얕은 셀의 마진 표본을 상위(공종→전역)로 수축. κ 는 사정률 중심 축(`AGENCY_PRIOR_STRENGTH`=12·`CATEGORY_PRIOR_STRENGTH`=40, `app/domain/assessment_shrinkage.py` — n3)과 **다른 축**이므로 이 데이터에서 재도출 — 분포 수축은 `n/(n+κ)` 가중으로 셀 표본과 부모 표본을 blend 하거나 대역폭을 계층 pool. **κ 값은 릴리스 단위 재추정, 상수 하드코딩은 사후 산물 금지**(`award_rate_windows` 의 `GATE_MIN_EVALUATION_ROWS` 교훈).

### 5.3 §4.5/§4.7 배치

- 순수 커널은 **2모듈 분할**(각 500줄 한도, PR1 에서 이미 구현): `app/domain/award_margin_distribution.py`(Δ축 — 마진 추출·ECDF/경계KDE·수축)와 `app/domain/award_landing_distribution.py`(합성축 — Phase 1 `F_a` 와 `WW(b)`·`survival_at_least` 합성). **I/O 0 순수 함수**, stdlib+numpy, 값표 테스트, Phase 1 커널(`reserve_draw_distribution`/`assessment_shrinkage`)과 같은 mypy strict 아일랜드(pyproject override 등재 — n1).
- 매직값(κ, 대역폭, 스파이크 ε, 셀 경계, MDE 임계)은 Settings/constants 선언. 셀 정책은 선언 데이터.
- 원가율 `c` 등 운영자 튜닝값은 `OperatorStrategy` 필드류 선언 데이터(§4.5-3).

---

## 6. Phase 1 과의 합성

`b` 를 기초-basis rate 로 고정하고 `a ⊥ Δ` 가정 하에 식 1 을 적분:

```
WW(b) = ∫_{a ≤ b/f}  P( Δ ≥ b/a − f )  dF_a(a)          (식 2)
```

유도: 생존 `φ=f·a ≤ b ⟺ a ≤ b/f`. 이김 `b ≤ ω = a(f+Δ) ⟺ Δ ≥ b/a − f`. `a ≤ b/f` 영역에선 `b/a − f ≥ 0` 이라 항상 `Δ` 의 지지집합 안(무모순).

**표기 주의(m2)**: `P(Δ ≥ δ)` 는 **inclusive** 다 — ECDF 원자(같은 마진 다수)에서 `1 − G(δ)`(strict)와 실제로 갈리므로 커널은 `survival_at_least`(≥)로 구현한다(PR1 이미 그렇게 수렴).

**각 항의 불확실성 출처 (요구된 구별)**:

| 항 | 출처 | 정산 코퍼스 채점 | 열린 공고(서빙) |
|---|---|---|---|
| `F_a` (생존/추첨) | 복권 메커니즘(완전 열거로 형태 특정) | **층 A 결합추정에 흡수**(분해·정확 CDF 대입 안 함 — M1) | 유사 공고 사후분포 추정(계층 수축) |
| `G` (경쟁/마진) | winner-only 표본 | **층 A 결합추정에 흡수** | 셀 경계KDE + 수축(전이) |
| `a ⊥ Δ` | 가정 | 채점엔 불필요(층 A 는 결합) — 진단만: 실측 `corr(a,Δ)=−0.0045`(n=2,152) | 서빙 합성이 이 가정에 의존, 완전 검증 불가 |

**독립 가정 진단**: 정산 코퍼스에서 `corr(a, Δ)` 를 산출해 공시한다(실측 **−0.0045**, n=2,152 — 가정 지지). **서빙 fallback 정정(M5)**: 셀 내 `f` 는 이질적이므로(실측 service 셀: `f=0.88` 66.8%·`0.90` 14.3%·`0.87745` 7.2%·기타) `WW_emp` 는 각 행의 `f_i` 를 담아 **그대로 다른 `f` 의 대상 공고로 전이할 수 없다**. 강상관이거나 서빙 fallback 이 필요하면 곱 분해 대신 **f-재기준 결합 경험추정**을 쓴다:
```
WW_fallback(b; f_tgt) = (1/N) Σ_i 1[ a_i ≤ b/f_tgt ] · 1[ Δ_i ≥ b/a_i − f_tgt ]     (식 3)
```
이는 식 2 가 가정으로 지우는 `(a, Δ)` 결합 의존을 보존하면서 대상 공고의 `f_tgt` 로 옮긴다. 자기 `f_i` 를 담는 층 A 원형(`WW_emp`)은 **자기 모집단 백테스트 접지 진리로만** 유지한다.

**Phase 1 이 point-estimate 를 대체**: distribution predictor 는 현재 `낙찰율/실현 사정률 비 중앙값`(`bid_ratio`)으로 사정률 축→투찰율 축을 **점 환산**한다. Phase 3 은 그 median-ratio 한 점을 **낙찰자 착지 분포 `G` 전체**로 승격한다(같은 신호의 분포 버전).

---

## 7. 기대가치 목적함수 (옵션과 권고)

원가 데이터 없음. 세 옵션:

| 옵션 | 목적 | 필요 데이터 | 평가 |
|---|---|---|---|
| **A. win-proxy 곡선만** | `WW(b)` 산출, **α-제약** `b*=argmax_{b: P(φ>b)≤α} WW(b)` | α(Settings 선언) | 비용 0·정직·현행 3점 포섭. 소박한 argmax 는 실격측을 방치(아래 실측) |
| **B. EV(선언 원가율)** | `EV(b)=WW(b)·(b−c)·B`, `b*=argmax EV` | `c` = OperatorStrategy 선언 필드(공종별) | 진짜 사업 목적(승률×마진). `c` 선언 후 얇은 층 |
| C. 참가자수 백필 후 완전 경쟁모형 | P(랜덤 참가자 이김)·마진 분포 | KONEPS 쿼터+운영자 승인 | **별도 선택지**(§10) |

**권고: 1차 슬라이스 = 옵션 A(α-제약).** 근거: ①원가 부재에서 `WW(b)` 는 정직한 원시량, EV 는 그 위 얇은 곱. ②이산 3점(∓1.28σ)을 연속 곡선의 3표본으로 포섭.

**α-제약이 필요한 이유(M2)**: `WW` 는 실격(하한 미달)과 패찰을 **등가로 취급**하지만 운영자는 아니다(3번째 실투찰이 사정률 추첨 실격 사고). 층 A 실측 — 소박한 `argmax WW_emp` 지점의 실격확률 `P(φ>b)` 는 **공사 32.5%**(argmax 0.89775, n=1,405) / **용역 52.6%**(argmax 0.87825, n=743) 다. 그래서 `b* = argmax_{b: P(φ>b) ≤ α} WW(b)`, `α` 는 사후 튜닝 방지를 위해 **Settings 에 릴리스 단위로 선언**하고 G8 과 함께 판정한다. α-제약은 옵션 A 를 깨지 않는 **호환 수정**이다(옵션 B 는 실격 비용을 EV 에 명시적으로 넣는 후속 층).

**자기 캘리브레이션 주장 삭제(M3)**: "`argmax` 가 낙찰자 밀집을 재현하므로 내부 검증"은 성립하지 않는다 — 실측 `argmax − median(ω)` 는 공사 **−15.4bp** / 용역 **−36.2bp** 계통 편차이고, 마진이 좁아(median Δ=47bp) 거의 항등식이라 **판별력이 없다**.

**옵션 B 는 `c` 선언 필드 신설 + A 게이트 통과 후 후속.** EV 는 승률×**운영자 선언** 마진의 **조건부 추정**임을 표면에 새긴다(이윤 보장 아님).

---

## 8. 서빙 통합과 guardrail

### 8.1 통합 지점

두 판정문이 지목한 **candidate selector** 가 자연 통합점. 계층적으로 셋 다:

1. **순수 도메인 커널**(`award_landing_distribution.py`): `WW(b)` 곡선·`b*` 계산.
2. **predictor 키**(`award_landing` 제안) 또는 selector 메타데이터: 곡선을 payload 로 노출하되 **가격을 움직이지 않음**. `AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS` 에 등재 + `.env` 명시 선호로만 실행.
3. **연속 selector**: 게이트 통과 후에만 `_select_recommended_candidate`(현행 = 항상 base)를 곡선 기반 `b*` 로 대체. 이산 base/conservative/aggressive → 연속 곡선 위 한 점.

**현행 동작 불변(red line #5)**: 게이트 통과 전까지 recommended candidate = base, 가격 경로 무변경.

### 8.2 guardrail (red line 보존)

- **법정 하한(#221 max()-only)**: 곡선은 `b ≥ 게시 법정하한` 구간에서만 평가. `b < f` 는 식 2 의 생존항이 0 → 곡선이 스스로 하한 아래를 배제. 최종 추천은 기존 `_apply_prediction_guardrails` 가 게시 법정하한으로 clamp. Phase 3 은 하한을 **선점·우회하지 않음**.
- **앵커 하향 재캘리브레이션 재론 금지**(기각 판정): Phase 3 은 앵커를 건드리지 않고 **하한 위 착지점**만 다룬다.
- **시간 누수(#3)**: `G` 는 과거 정산 공고만(as-of), `F_a` 는 과거 공고 사후, 대상 공고 예비가 미사용, era-correct `f`, 셀 배정은 투찰 시점 관측 피처(공종/금액대/레짐)만.
- **group-scoped 강제**(selector 판정): whole-cell 배선 금지. 공사(base 정답)와 용역(conservative 정답)이 서로 지운다.

### 8.3 아티팩트·서빙 비용 (요청당 적합 금지 — M6)

셀별 `G`·`κ`·수축표는 **버전·계약을 가진 릴리스 아티팩트로 영속화**한다(Phase 2 패턴: `app/ai/predictors/artifact_contracts.py` 의 `read_persisted_artifact` + manifest). 서빙은 그 아티팩트를 로드해 `F_a` 와 합성(식 2)만 하고, **요청 경로에서 KDE/수축을 적합하지 않는다** — API 인라인 ML 은 금지다(api 8.4GiB OOM→좀비 리스너 17h 행 사고, `--reload` 제거·`mem_limit` 도입의 원인). 아티팩트는 표본 정의(코퍼스·era·품질 사다리 버전)를 스스로 신고해, 다른 모집단으로 학습된 옛 산출물이 새 코드에 조용히 로드되는 것을 막는다(Phase 2 `AWARD_RATE_SAMPLE_SCOPE` 선례, fail-closed).

---

## 9. 게이트 (사전 선언)

Phase 2c 인프라 **재사용**(`award_rate_windows`·`settlement_maturity`·`award_rate_diagnostics`) + selector 판정 게이트. **배선 전에 통과를 사전 선언**한다:

| # | 게이트 | 출처 | 임계 |
|---|---|---|---|
| G1 | 성숙도 embargo | `settlement_maturity` | 평가 창 정산비율 ≥ 0.70 (winner-landing 은 정산 의존 → 동일 선택편향) |
| G2 | 겹치지 않는 날짜 창 | `plan_evaluation_windows` | 최근 ≤5 창, 07월 집중 붕괴 방지 |
| G3 | 부분셀 표본 깊이 | selector 판정 | **배선 대상 `floor_bound×공종` 부분셀 n ≥ 150**(현 병목) |
| G4 | red line | max()-only #221 | 법정 하한 미하회 **0건** (전 정책·전 스코프) |
| G5 | 시간분할 OOS | 로드맵 12 | random split 승인 금지, older 적합/newer 채점 |
| G6 | MDE·seed 안정성 | `award_rate_diagnostics` | 관측 개선 > MDE(50% 기준) **그리고** seed 부호 일관 |
| G7 | as-of 재예측 | selector 판정 | replay ≠ as-of — 배선 replay 를 as-of 로 재현 |
| G8 | 실격측 불팽창 | selector 판정 | 곡선 `b*`(α-제약) 가 always-base 대비 실격(**하한 미달** `P(φ>b)`)측 비율 **불증가**. 기준선은 selector 판정문(39.7%)과 정의가 다르므로(그쪽=낙찰가-아래) **새로 측정**(M2) |

**배선 판정식(사전 선언)**: `OOS mean|bp| 개선 > MDE` ∧ `실격측 불증가(G8)` ∧ `red line 0(G4)` ∧ `배선 부분셀 n≥150(G3)` ∧ `seed 부호 일관(G6)` ∧ `as-of 재현(G7)`. 하나라도 미충족이면 draft 보류 — **"못 이겼다"와 "못 쟀다"를 MDE 로 구별**(Phase 2c 원칙).

---

## 10. 구현 슬라이스 (PR 분해)

| PR | 내용 | 유형 | 게이트 의존 |
|---|---|---|---|
| **PR1** | 순수 커널 **2모듈**(`award_margin_distribution.py` Δ축 / `award_landing_distribution.py` 합성축, 각 500줄 한도) — 마진 추출·ECDF/경계KDE·수축·`F_a` 합성·`survival_at_least`(≥). mypy strict 아일랜드(pyproject). 값표 테스트. **배선 0**. **이미 구현** — 후속으로 α-제약 argmax API·f-재기준 fallback(식 3) 추가 예정 | 코드-only | 없음 |
| **PR2** | 백테스트/리포트: 층 A `WW_emp` + 층 C 합성 셀별 산출. **게시 `f` 로 술어 재계산해 채점**(저장 `_final` 라벨과 직접 대조 금지 — M4 하한 불일치 100%). 품질 사다리 재사용 + `f==1.0` 오염 필터(m1)·`winning_amount/B` 재계산(n5). `corr(a,Δ)`·실격측 `P(φ>b)` 진단. reports gitignored | 코드-only | 없음(측정) |
| **PR3** | predictor 키 `award_landing`(AUTO_PROMOTION_EXCLUDED 등재) + `.env` 선호. 곡선 payload 노출, **recommended=base 불변** | 코드-only | 없음 |
| **PR4** | 게이트(G1~G8) 통과 부분셀에 한해 연속 `b*` 를 selector 에 배선. group-scoped | **게이트 의존** | §9 전부 — **데이터 성숙 대기**(Phase 2c 처럼 며칠~2주) |
| PR-opt | (옵션 B) `OperatorStrategy.cost_rate` 선언 필드 + `EV(b)` 층 | 코드-only(스키마+마이그레이션) | PR4 이후 |
| PR-sep | (옵션 C) 참가자수 백필 — winner-only → full-participant 승격 | **쿼터+운영자 승인** | 별도 결정 |

각 태스크당 fresh 서브에이전트 + 태스크 사이 리뷰(subagent-driven 표준). PR2 의 캘리브레이션 결과가 PR3/PR4 착수 여부를 gate 한다.

---

## 11. 알려진 한계 (정직 명세 §2)

1. **winner-only 근본 제약**: 균형·마진·랜덤참가자 승률 불가. 반사실은 "타 투찰 고정" 가정(공시).
2. **`a ⊥ Δ` 가정**: 부분 검증만 가능. 강상관 시 층 A 결합추정으로 후퇴.
3. **BID_RATIO/basis 혼재 유산**: `winning_rate` 는 예정가-basis(검증됨)지만 `HistoricalData.bid_rate` 는 base/예정가 혼재(48/52 — **리뷰 미검증 표기, `distribution_extraction` docstring 인용값**, n4) — Phase 3 은 `winning_amount/B` 직접 사용으로 이 혼재를 우회하되, 마진 정의는 예정가-basis 로 통일해 축 혼입 차단. `settlement.py:49-52` 의 base-relative 주석은 실측 예정가-basis 와 어긋나므로(n5) PR2 는 그 폴백을 쓰지 않는다.
4. **era 편중**: 07월 집중·공사 2026-06-30 개시. 성숙도/날짜창이 정직하게 처리하나, era 간 비교는 불성립(floor-anchor 한계와 동일).
5. **셀 희소**: 용역 부분셀 n<150 개연 → "판정 불가" 결론 가능. 임계 조정이 아니라 **시간**이 해법.
6. **스파이크 밀도 추정**: 하한 0+ 스파이크는 KDE 난제. CDF/ECDF 소비로 회피하되 서빙 전이 평활은 경계보정 필수.
7. **`argmax` 다봉/평탄**: `WW(b)` 가 평탄하면 `b*` 불안정 → 곡선 폭·2차 도함수 진단을 판정 옆에 공시(MDE 정신).
8. **`_final` 가격 외 적격심사 미모형(B1)**: `WW` 는 `eligible_favorable` 의 **가격 조건만** 추정한다 — 관측 낙찰자가 정의상 자격·서류 통과자라 적격 통과 확률은 표본에서 보이지 않는다(§3.2). "적격 통과 조건 하의 가격 승리"로 읽는다.
9. **층 B 편향(M1)**: 생존항만 추첨 CDF 로 적분하는 정밀화는 `corr(a,ω)=+0.587` 때문에 `WW` 를 과대추정한다 → 폐기. 정산 채점은 결합 경험추정(층 A)만.

---

## 12. 운영자 결정 기록 (2026-08-13)

1. **1차 목적함수 = 옵션 A(win-proxy 곡선만) 확정.** EV(원가율)는 후속으로 분리.
2. **원가율 선언(옵션 B)**: 미결 — EV 층 착수 시점에 공종별 원가율 `c` 를
   `OperatorStrategy` 선언 필드로 둘지 재질의한다. 선언 전까지 EV 층 보류.
3. **참가자수 백필(옵션 C) = 하지 않음 확정.** winner-only 유지 — 반사실 estimand 가
   참가자수 없이 정확 식별되고, "우리가 낙찰인가" 최소수집 원칙과 정합.
4. **셀 스코프**: `공종 × 금액대` 시작 + 발주기관은 수축 상위 계층으로만(권고안 채택,
   Phase 1 실측 91.9%가 n<10 인 표본 구조 근거). 이견 시 재론.
5. **판정 불가 수용**: PR2 캘리브레이션이 "아직 잴 수 있는 셀 없음"으로 나오면 PR4 는
   Phase 2c 선례대로 데이터 성숙 대기(임계 조정 금지) — 설계 승인에 포함.
6. **실격 예산 `α` 초기값(신규 미결 — M2)**: `b* = argmax_{b: P(φ>b)≤α} WW(b)` 의 `α` 를
   Settings 에 릴리스 단위로 선언해야 한다. 소박한 argmax 실측 실격률(공사 32.5%·용역
   52.6%)을 감안한 초기값 후보와 판정 근거는 PR2 리포트가 제시하고, 값 확정은 운영자
   결정이다(사후 튜닝 방지 위해 데이터를 보고 정하지 않고 사전 선언).

---

## 부록 A — 재현 실측 쿼리 (읽기 전용, 2026-08-13)

```sql
-- 쌍 (floor_rate × winning_rate>0): 3,182 / 예비가 보유 3,073 / clean 2,153
-- participant_count 보유: 3 (winner-only 확정)
SELECT count(DISTINCT p.id) FROM projects p
JOIN tender_results tr ON tr.project_id = p.id
JOIN historical_data h ON h.project_id = p.id
WHERE p.award_floor_rate IS NOT NULL AND tr.winning_rate > 0
  AND h.reserve_prices <> '[]' AND length(h.reserve_prices) > 5
  AND h.base_amount_basis = 'clean';   -- 2153
```

## 부록 B — 참조

- Phase 1: `app/domain/reserve_draw_distribution.py`, `app/domain/assessment_shrinkage.py`, `app/ai/predictors/distribution.py`, `app/ai/predictors/distribution_extraction.py`, `app/ai/predictors/scenario_spec.py`
- Phase 2c 게이트: `app/services/ml_training/award_rate_windows.py`, `app/domain/settlement_maturity.py`, `app/services/ml_training/award_rate_diagnostics.py`
- predictor 포트/레지스트리: `app/ai/predictors/base.py`, `app/ai/predictors/registry.py`, `app/core/constants.py`(`AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS`)
- 판정문: `docs/operations/floor-anchor-recalibration-verdict.md`, `docs/operations/candidate-selector-rederivation-verdict.md`
- 정직 명세: `CLAUDE.md` §2, 설계 규칙 §4.5, 테스트 용이성 §4.7
