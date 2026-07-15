# Per-notice 투찰가 메뉴 (bid target menu) — 설계 spec

- 날짜: 2026-07-15
- 상태: 승인됨 (사용자 "확정"), 구현 계획 대기
- 범위: **백엔드 추천 출력만** (스키마/서비스/테스트). UI는 후속 단계.

## 1. 배경 / 동기

한국수산자원공단 적격심사 용역 두 공고(R26BK01627948, R26BK01628093)의 투찰가가 **둘 다 밴드 끝(88.20% ceiling)에 붙어** 공고별로 구분되지 않았다. 원인:

- 발주처 밴드 `[floor 0.8806, ceiling 0.882]`는 발주처 단위 **정적 상수**(29건 집계)로 모든 공고에 동일 적용.
- 밴드 폭이 0.14%p로 좁고, ensemble 예측기는 시장평균(~0.91)을 예측 → 항상 밴드 끝으로 clamp → rate가 공고와 무관하게 동일.
- 최저적격(가장 낮은 적격가 낙찰) 목표에서는 경쟁 타겟이 floor 근처인데, 예측기 과대예측 탓에 ceiling으로 추천됨.

실제 낙찰하한은 공고별로 다르다(예정가격 = 복수예비가격 15개 중 4개 추첨 평균, ±, 개찰 전 비공개). 이 변동을 완벽히 예측하는 것은 불가능하다는 전제 하에, **(a) 밴드 안에서 공고별 신호로 추천 위치를 조정**하고 **(b) 운영자가 고를 수 있는 리스크별 투찰가 옵션을 제시**한다.

## 2. 목표 / 비목표

목표
- 공고별 **투찰가 메뉴 3종** 출력: 추천(공고별 신호로 밴드 내 위치), 공격(밴드 floor), 안전(밴드 ceiling).
- 각 옵션에 **정직한 리스크 프레이밍**(정성적 stance + 사실 basis + caveat). 승률/낙하확률 수치 제시 금지(§2).
- `recommended_amount`를 메뉴의 '추천'으로 정렬(핵심 semantic 변경, ★결정 1).

비목표(이번 범위 밖)
- 프론트 UI (후속 단계).
- 경쟁 프록시 신호(다음 증분, ★결정 2 — 이번은 산포 단독).
- 복수예비가격 기반 라이브 신호(개찰 전 비공개 → 원천 불가).
- guardrail 밴드 캘리브레이션 값 변경(별건, PR #149 소유).

## 3. 확정된 결정

- **★결정 1 (승인)**: `recommended_amount` = 메뉴의 '추천'(floor 앵커 + 신호 조정). 예측기의 시장평균 예측을 이 입찰유형(최저적격)에는 primary 추천으로 쓰지 않고 floor 앵커 타겟으로 대체. "왜 항상 같은 값" 문제 해소의 핵심.
- **★결정 2 (YAGNI, MVP)**: 이번 PR 신호 = **과거 낙찰률 산포 단독**. 경쟁 프록시는 데이터 확인 후 다음 증분. 신호 리졸버는 확장 가능하게 구조화.

## 4. 아키텍처 / 컴포넌트

### 4.1 신규 순수 모듈 `app/ai/bid_target.py`
단일 책임: `(밴드, budget, 신호) → 메뉴`. DB/IO 없음(순수, 단위 테스트 용이).

```
BidTargetSignals(dataclass):
    win_rate_dispersion: float | None   # 발주처/유형 실현 낙찰률 표준편차(0..), None=데이터부족
    data_sufficient: bool               # 신호가 신뢰 가능한가

build_bid_target_menu(*, floor_bid_rate, ceiling_bid_rate, budget, signals) -> BidTargetMenu
    - 공격(aggressive) = floor_bid_rate
    - 안전(safe)       = ceiling_bid_rate
    - 추천(recommended)= clamp(floor + adj*(ceiling-floor), floor, ceiling)
      where adj = _resolve_position_adjustment(signals)
    - 각 옵션: label, stance, bid_rate, bid_price(=round(rate*budget,2)), risk_note, basis

_resolve_position_adjustment(signals) -> float in [0,1]
    - base_adj = 0.15 (추천을 floor 근처=최저적격 경쟁타겟에 앵커)
    - 산포 큼 → adj 증가(예정가격 불확실 → 안전 쪽 소폭 상향), 캡
    - data_sufficient=False → base_adj 고정(중립)
    - 투명한 캡 공식(문서화). 확률 아님 — 위치 heuristic.
```

엣지 케이스
- `floor == ceiling`(§4.7 protected floor로 밴드 붕괴): 3종 동일 rate, `collapsed=True` 표기(단일 옵션).
- 밴드 없음(agency band 미적용, floor/ceiling None): 메뉴 생략(None 반환) — 기존 단일 추천 유지.
- `budget <= 0`: rate만 채우고 bid_price=None.
- floor > ceiling 방어(정상 흐름엔 없음): guardrail이 이미 보정.

### 4.2 신호 리졸버 (서비스 계층) `resolve_bid_target_signals(db, *, agency_name, category)`
- `win_rate_dispersion`: 발주처(정규화)+유형의 최근 `TenderResult.winning_rate` **집계(STDDEV) 쿼리**로 산출. **65535 교훈**: row를 로드하지 않고 SQL 집계(`func.stddev`)+`COUNT`로. 표본 부족(예: n<8) → `data_sufficient=False`.
- 위치: `app/services/` 내(예: `bid_target_signals.py` 또는 prediction 인접). DB 주입.

### 4.3 연결점
- `predict_price`는 그대로 floor/ceiling/predicted/base(사업금액) 반환(순수 유지).
- `opportunity_analysis._build_price_prediction`(db·project 보유)에서:
  1. predict_price 결과의 floor/ceiling/base 취득,
  2. `resolve_bid_target_signals(db, agency, category)`,
  3. `build_bid_target_menu(...)` → `prediction["bid_target_menu"]` 첨부,
  4. `recommended_amount` = 메뉴 추천 bid_price(★결정 1).
- `prediction_workflow.predict_project_price`(API)도 동일 헬퍼로 메뉴 첨부(일관성).

## 5. 스키마 (`app/schemas/schemas.py`)

```
BidTargetOption:
    label: Literal["recommended","aggressive","safe"]
    stance: str            # 예: "신호 종합 균형" / "경쟁력 높음·낙하 위험" / "낙하 위험 낮음·경쟁력 낮음"
    bid_rate: float
    bid_price: float | None
    risk_note: str
    basis: str             # 사실 근거(관측 낙찰하한/밴드)

BidTargetMenu:
    options: list[BidTargetOption]   # recommended, aggressive, safe 순
    band_floor_rate: float | None
    band_ceiling_rate: float | None
    signals_summary: str             # 무엇이 추천을 움직였나 + 데이터 충분성 플래그
    caveat: str
    collapsed: bool = False
```
- `PricePredictionResponse`에 `bid_target_menu: Optional[BidTargetMenu]` 추가.
- OpenAPI 타입 sync(sync-types) — 스키마 변경이므로 UI 전이라도 실행.

## 6. 정직 명세 (§2)

- 라벨/stance는 **정성적**이며 승률·낙하확률 수치가 아님.
- `basis`는 사실만: 예) "관측 낙찰 하한 88.0~88.05% / 발주처 밴드 88.06~88.20%".
- `caveat`: "예정가격은 복수예비가격 추첨(개찰 전 비공개)으로 결정되어 정확한 낙찰하한을 보장할 수 없습니다. 이 값들은 투찰서 초안·의사결정 지원이며 KONEPS 자동 제출·확정 낙찰이 아닙니다."
- `signals_summary`는 추천 이동 근거와 데이터 부족 여부를 투명 표기.

## 7. 데이터 흐름

```
predict_price → {floor_bid_rate, ceiling_bid_rate, predicted_bid_rate, budget=사업금액 base}
  ↓ (service: db 보유)
resolve_bid_target_signals(db, agency, category) → BidTargetSignals(win_rate_dispersion, data_sufficient)
  ↓
build_bid_target_menu(floor, ceiling, budget, signals)
  → {aggressive=floor, recommended=floor+adj*width, safe=ceiling} + stance/basis/caveat
  ↓
prediction["bid_target_menu"] + recommended_amount ← 추천 bid_price   → 스키마 → (UI 후속)
```

## 8. 테스트

- `bid_target` 유닛: adj=0→floor / adj=1→ceiling / 보간; 엣지(floor==ceiling collapsed, budget<=0, 밴드 없음); `bid_price == round(rate*budget,2)`이고 budget=사업금액 base(=#162와 정합).
- 신호 리졸버: 산포 큼→adj 상향(추천이 안전 쪽); 표본 부족→data_sufficient=False→중립 adj; 집계 쿼리가 row 미로드(65535 회귀 방지).
- 정직: 응답에 확률 필드 없음, caveat/basis 존재·사실.
- 통합: 한국수산자원공단 공고에서 옵션 3종, base 정합, recommended_amount==추천 bid_price(★결정 1).
- 회귀: predicted_bid_rate/floor/ceiling 등 기존 필드 불변; 메뉴는 additive(단 recommended_amount는 ★결정 1로 변경) — 기존 스냅샷 테스트 영향 확인·조정.

## 9. 롤아웃 / 배포

- 스키마 변경 → sync-types.
- 머지 후 predict_price 경로(api·worker) 재기동으로 반영(#162 패턴).
- guardrail 캘리브레이션·밴드 값 무변경(회귀 표면 최소).

## 10. 남은 리스크 / 후속

- 신호가 약함/좁은 밴드(0.14%p) → 추천 이동폭이 작다(정직 표기). 주 가치는 3옵션 프레이밍 + 정직 리스크.
- 다음 증분: 경쟁 프록시 신호, 복수예비가격 수집 시 캘리브레이션 반영, UI(투찰서/추천 화면).
