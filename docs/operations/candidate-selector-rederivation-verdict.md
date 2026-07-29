# candidate selector 정책 재도출 판정 (2026-07-28)

## 결론

- **구 Option A(P1: `floor_bound` 전체 → conservative 승격)는 배선 금지로 확정한다.** #312 라벨 재정의로 floor_bound 모집단이 바뀌면서 pre-#312에서 관측된 개선이 소멸했고, OOS에서 중립~악화 + 실격측(낙찰가 아래) 비율 팽창이 실측됐다.
- **유일 생존 후보는 P1'(`floor_bound`×`service` 한정 → conservative, 공사는 base 유지)이다.** 전 평가 스코프에서 개선이 일관되고 red line(법정 하한 미하회) 0을 유지했다. **단 실질 표본 게이트 미충족(해당 부분셀 n=80 < 150)이라 배선하지 않고 draft로 보류한다.**
- 현행 동작(`_select_recommended_candidate` = 항상 base)은 유지한다. 이 판정으로 코드·가격 경로는 변경하지 않았다.
- **일반 규칙**: #312 이후 `floor_bound`는 공사 행이 다수다. 향후 "floor_bound → X" 류 정책은 **반드시 group-scoped**로 선언해야 한다. whole-cell 배선은 base가 정답인 공사 다수를 조용히 오배정한다.

## 배경

1. **2026-07-27 1차 분석** (`reports/candidate-selector-analysis-20260727.json`, gitignored): 사후 argmin 셀 정책 표는 시간 분할 OOS에서 악화(268.0bp vs always-base 243.7bp)로 금지 판정. 유일 생존 후보 = Option A(floor_bound→conservative, OOS 243.7→239.8bp). 단 clean 표본 67건 < 사전 선언 게이트 150이라 보류.
2. **#312(2026-07-28 머지)**: 공사×수의(direct_negotiated 단독) near_100 오탐 470/826건을 floor_bound로 재라우팅(라벨 한정, 가격 불변). floor_bound 표본 풀이 커져 게이트 재확인 대상이 됐다.
3. **모집단 반전 문제**: post-#312 재실행에서 floor_bound clean 표본은 642건으로 게이트를 명목상 통과했지만, 그 다수는 재라벨된 공사 수의 행이고 해당 셀의 역사적 최적 스탠스는 base였다. 라벨 재정의가 Option A의 전제(당시 floor_bound ≈ 용역)를 바꿨으므로, **새 라벨 분포 기준으로 스탠스 최적성·정책 표를 재도출한 뒤에만 배선을 판단**하기로 했다. 본 문서가 그 재도출의 판정 기록이다.

## 방법 (정직 명세)

- 데이터: post-#312 홀드아웃 `reports/latest-award-holdout-agency-tpg5.json`(2026-07-28 06:14 KST 재생성, 6,121 targets) + 라이브 post-#312 코드로 레짐 라벨 재계산(공고 텍스트 DB read-only 조회). pre-#312 사본과 대조해 라벨 이동을 검증.
- 평가: 저장된 후보 스탠스(base/conservative/aggressive) 중 어느 것을 recommended로 승격하는지만 바꾸는 **오프라인 replay**. **as-of 재예측이 아니다** — 배선 전 as-of 재현 게이트가 별도로 남는다.
- OOS 규율: 정책 표는 시간 분할 전반(older)에서 동결하고 후반(newer)에서만 채점. 보조로 agency-excluded 분할 재현 확인. in-sample 수치는 천장(ceiling) 참고로만 사용. random split 평균으로 승인하지 않는다(로드맵 12번 split 정책 준수).
- 클린 필터·표본 기준은 2026-07-27 1차 분석과 동일(quality flag-free).

## 핵심 수치

### 라벨 이동 sanity check (평가셋 6,121건)

- near_100 2,796 → 906, floor_bound 486 → 2,376. 이동은 단 한 종류: near_100 → floor_bound [construction] **1,890건**(하한 미해석 fallback 0). 직전 실측(905/1,889)과 ±1 정합.
- 가격 불변: recommended==base 6,121/6,121 (현행 selector는 가격을 움직이지 않음).

### 새 floor_bound clean flag-free 642건 = 정반대 하위 모집단의 합성

| 하위 모집단 | n | argmin 스탠스 | mean\|bp\| (base / cons) | signed bias | 비고 |
|---|---|---|---|---|---|
| #312로 이동해 온 공사 수의 | 561 | **base** | 126.3 / 140.9 | base −94bp (이미 낙찰가 이하) | 실낙찰 중앙값 하한+52.9bp, 79%가 하한+100bp 이내. conservative 강제 시 +14.6bp 악화 |
| 원래 floor_bound (service) | 80 | **conservative** | 393.0 / 265.7 | base +282bp (낙찰가 한참 위) | conservative −127.3bp 개선, 근접 점유 78.8% |
| 합산 (whole-cell) | 642 | 상쇄 | 159.6 / 156.6 | — | 두 하위 모집단이 서로 지움. 근접 점유는 base가 우위(44.2% vs 37.4%) |

(goods 1건은 생략. 공사 수의가 base 정답인 이유: #312 판정대로 이 행들은 하한 밀착 가격 행동이고, base가 이미 하한+53bp 근처에 착지한다.)

### 정책 replay — P0 대비 mean|bp| 델타 (음수=개선)

| 스코프 | P1 (floor_bound 전체→cons) | P1' (service 한정→cons) |
|---|---|---|
| clean flag-free (in-sample) | −1.3 | **−6.9** |
| 시간분할 older (적합) | −3.9 | **−8.8** |
| **시간분할 newer (OOS)** | **+1.3 (악화)** | **−4.9** |
| **agency-excluded** | **+2.7 (악화)** | **−3.7** |
| clean all | +1.5 (악화) | **−6.5** |

- newer OOS 절대치(n=744): P0 254.8bp / median 66.7 / 낙찰가 아래 39.7% — P1 256.1 / **75.5** / **58.5%(실격측 급증)** — P1' **249.9 / 61.1 / 40.5%(실격측 거의 불변)**.
- red line(법정 하한 미하회): 모든 정책·모든 스코프에서 **0건**.
- 참고(ceiling): older 적합 per-cell argmin 표가 이번 분할에선 OOS로 P0를 이겼다(245.9 vs 254.8). 그 표가 독립적으로 floor_bound|construction→base + floor_bound|service→conservative(=P1'의 분해)를 골랐기 때문이다. 유리한 분할 1회는 검증이 아니므로 천장 참고로만 두고 제안하지 않는다(1차 분석의 argmin 표 금지 판정 유지).

## 판정

1. **P1 폐기.** pre-#312의 개선(243.7→239.8bp)은 당시 floor_bound가 사실상 용역 모집단이었기 때문에 성립한 것으로, 라벨 재정의 후에는 base가 정답인 공사 561건에 conservative를 강제해 OOS 악화·median 악화·실격측 39.7→58.5% 팽창을 일으킨다.
2. **P1' 채택 후보(draft) — 배선 보류.** 방향성 근거가 사전에 명확하고(service base +282bp 편향) 전 스코프 개선·실격측 불변·red line 0이지만, 실질 게이트는 "conservative를 원하는 부분셀" 표본이며 이는 **원래-service floor_bound n=80(newer OOS n=29) < 150**이다. #312는 base를 원하는 공사만 셀에 더했을 뿐 이 부분셀을 깊게 하지 못했다.
3. 다중비교 억제: 평가 후보는 사전 방향성 근거가 있는 P0/P1/P1'로 한정했다(+ceiling 참고 1종). 신규 후보를 늘리는 방식의 재탐색은 하지 않는다.

## 승격(배선) 전 잔여 게이트

| 게이트 | 현황 |
|---|---|
| ① 원래-service floor_bound clean 표본 ≥150 | **미충족** (80, newer OOS 29) — 실 게이트 |
| ② rolling latest-N 창에서 재현 | 미충족 (현 창 ~3주, 해당 부분셀 표본 부족) |
| ③ 배선 시 as-of 재예측으로 replay 재현 확인 | 미착수 (replay ≠ as-of) |
| ④ 라벨 신뢰도 | 공사 near_100 오탐은 #312로 해소. **near_100×service는 여전히 bimodal → base 유지, 정책 대상 아님** |

## 원자료 (gitignored, 재현 경로)

- `reports/candidate-selector-analysis-post312-20260728.json` — 본 판정의 전체 수치
- `reports/candidate-selector-analysis-20260727.json` — 1차 분석(argmin 금지·Option A 후보)
- `reports/latest-award-holdout-agency-tpg5.json` / `-pre312.json` — post/pre-#312 홀드아웃
- 절차: `docs/operations/latest-award-holdout-backtest.md` (홀드아웃 실행), 라벨 재계산·flat화·replay 스크립트는 세션 스크래치패드 파이프라인(extract_regime → flatten → replay → build_report) 재사용
