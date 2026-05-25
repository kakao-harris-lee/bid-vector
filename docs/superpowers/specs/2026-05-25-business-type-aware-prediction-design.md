# 업무구분(KONEPS 업종코드) 기반 예측 보정 — Design

- **작성일**: 2026-05-25
- **상태**: Draft (브레인스토밍 완료, 사용자 리뷰 대기)
- **관련 모듈**: `app/ai/price_prediction.py`, `app/ai/predictors/historical.py`, `app/services/koneps/collector.py`, `app/models/models.py`, `scripts/promote_ml_release.py`

## 1. 문제 진단 & 증거

### 1.1 관측

DB `tender_results.winning_rate` 분포 (2026-05-25 기준):

| Project.category | n | min | p25 | median | p75 | max | mean | std |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| construction | 9,880 | 0.271 | 0.902 | **0.903** | 0.905 | 1.000 | 0.902 | **0.030** |
| service | 8,047 | 0.001 | 0.880 | **0.883** | 0.931 | 1.000 | 0.901 | **0.059** |

- construction은 낙찰하한선 ~90% 근처의 **단봉** 분포(p25/p50/p75가 거의 동일).
- service는 ~88%/~93% **양봉** 분포로 std가 construction의 **2배**.
- 현재 predictor는 두 분포를 **단일 prior**로 학습하며, 카테고리별 floor/ceiling만 가드레일로 적용한다.

### 1.2 데이터 누수

KONEPS 크롤러(`app/services/koneps/collector.py:1741`)는 이미 HTML 셀에서 `business_type`을 파싱하지만, `Project` 모델에 영속화 컬럼이 없어 **19,824건의 KONEPS 세분류가 모두 버려지고 있다.**

### 1.3 목표 / 비-목표

**목표**

- KONEPS 4자리 업종코드를 영속화하고 predictor의 입력 feature로 사용.
- 공사·용역 2개 도메인 그룹에 대해 별도 calibration(사정율 중심값, rate band)을 적용.
- `predictor_backtest`로 그룹별 격차 축소를 측정 가능하게 만든다.

**비-목표 (YAGNI)**

- N개 모델 분리/manifest 분리 — 운영 복잡도 ↑, 데이터 부족 그룹(software 5건)은 학습 불가.
- 계층적 LSTM head — 데이터 부족 시 base/head 모두 흔들림.
- N그룹 calibration (goods 별도) — 데이터 누적 후 후속 PR.
- 업종코드 embedding feature — 4자리 카디널리티 충분, 후속 검토.

## 2. 전체 아키텍처

```
┌────────────────────────────────────────────────────────────────────┐
│  Phase A — 업종코드 데이터 레이어 (prerequisite)                    │
│                                                                    │
│  KONEPS HTML/OpenAPI ──▶ collector ──▶ Project.business_type_code  │
│                                       Project.business_type_label  │
│                                                                    │
│  Alembic migration: add 2 columns + index (business_type_code)     │
│  Backfill script: 기존 19,824건 재크롤 또는 title-rule fallback     │
│  완료 게이트: coverage ≥ 95%                                        │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  Phase B — 모델 레이어 (Phase A 완료 후 활성)                       │
│                                                                    │
│  PricePredictionContext + business_type_code, business_group       │
│                                                                    │
│  HistoricalStatisticalPredictor                                    │
│    ├─ select_competitive_base_rate(group=..., code=...)            │
│    └─ apply_group_calibration(group=...)                           │
│                                                                    │
│  Guardrails: PREDICTION_GROUP_*_BID_RATES (group-keyed)            │
│  기존 PREDICTION_CATEGORY_* 키는 한 릴리즈 deprecated 호환           │
└────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────────┐
│  Evaluation gate                                                   │
│                                                                    │
│  predictor_backtest를 그룹(공사/용역)로 분할 실행                   │
│  before/after: avg|err|·median|err|·p90|err| 그룹별 비교            │
│  manifest promotion gate에 group_calibration·group_error_delta 추가 │
└────────────────────────────────────────────────────────────────────┘
```

### 2.1 핵심 결정

- **2 도메인 그룹(공사·용역)만** 별도 보정. 나머지(`goods`/`other`)는 기존 경로로 흘림 — 추가 그룹은 데이터 누적된 뒤 후속 PR.
- 그룹 매핑은 **업종코드 prefix**로 결정 (`04 → 공사`, `06 → 용역`). `settings.BUSINESS_GROUP_CODE_PREFIXES`로 외부화해 도메인 변경 시 코드 수정 불필요.
- Phase B는 Phase A의 백필 coverage 임계값(`BUSINESS_TYPE_COVERAGE_GATE`, 기본 95%)을 만족할 때만 활성화.
- manifest promotion gate에 그룹별 오차 메트릭 추가 — 회귀 시 자동 차단.

### 2.2 의도적으로 제외

- 별도 manifest/artifact 분리 — 단일 release_tag 유지.
- Soft-label/sub-category embedding — 후속.
- 추론 시점 KONEPS API 추가 호출 — 백필은 source_url detail 재파싱 우선, 실패 시 title-rule fallback.

## 3. Phase A — 데이터 레이어

### 3.1 스키마 변경

`Project` 테이블 추가 컬럼:

| 컬럼 | 타입 | nullable | 인덱스 | 비고 |
|---|---|---|---|---|
| `business_type_code` | `String(8)` | yes | yes (b-tree) | KONEPS 4자리 업종코드 (예: `0411`). null = 미상 |
| `business_type_label` | `String(64)` | yes | no | 사람이 읽는 라벨 (예: `건축공사`) |

`business_group`은 **컬럼 아님** — config의 prefix-rule로 런타임에 계산. 룰이 바뀌어도 backfill 불필요.

**Alembic 마이그레이션**: `add_business_type_to_project.py`. Up은 ADD COLUMN 2개 + 인덱스, Down은 DROP. nullable이라 락 영향 미미.

### 3.2 수집 파이프라인

`app/services/koneps/collector.py`의 기존 `business_type`(text) 캡처 경로:

- `_parse_koneps_result_table` (line 1741) — HTML 리스트 셀
- `_parse_detail_html` — 상세 페이지

작업:

- HTML 셀에서 코드(`0411` 등)와 라벨을 분리해 `CrawlNoticeItem`에 `business_type_code` 필드 추가.
- 상세 페이지 우선, 리스트 셀 fallback. 두 경로 모두 실패 시 `None` 저장.
- KONEPS OpenAPI 응답의 업종코드 필드 매핑 (OpenAPI 우선 정책과 일치).

### 3.3 영속화

`KonepsCollectorService.upsert_project` (및 `PaperBiddingBacktestService`의 project 동기화 지점)에서 매 upsert마다:

```python
project.business_type_code = item.business_type_code
project.business_type_label = item.business_type_label
```

기존 행 재크롤 시 갱신, 신규 행은 처음부터 포함.

### 3.4 백필

`scripts/backfill_business_type.py` 신규:

1. `source_url`이 있는 row만 1차 후보 (대부분 케이스).
2. 상세 페이지 비동기 fetch + 파싱 → `business_type_code`/`label` 추출.
3. 실패한 row는 2차 fallback: `title + agency` 기반 regex 룰 — config의 `BUSINESS_TYPE_TITLE_RULES`에서 가져옴.
4. 모든 fallback 실패는 코드 `null` 유지 + audit JSON에 기록 (`reports/business-type-backfill/<run>.json`).
5. `--dry-run` 모드, `--limit N`, idempotent.

**완료 게이트**: `SELECT count(*) WHERE business_type_code IS NULL` / total ≤ `BUSINESS_TYPE_COVERAGE_GATE` (기본 5%). Phase B는 이 임계값을 만족할 때만 활성.

### 3.5 테스트 (`tests/test_business_type_backfill.py`)

- 컬럼 마이그레이션 후 nullable 동작
- collector가 HTML/OpenAPI 응답에서 코드+라벨 모두 추출
- 백필 스크립트의 source_url → 코드 매핑
- title-rule fallback이 의도한 매핑 산출
- 미매핑 row가 NULL로 안전하게 보존

## 4. Phase B — 모델 레이어

### 4.1 Context 확장

`app/ai/predictors/base.py`의 `PricePredictionContext`:

```python
@dataclass
class PricePredictionContext:
    budget: float
    description: str
    historical_bids: list[Any]
    category: str                          # 기존 coarse 5값 유지
    business_type_code: str | None = None  # 신규 — KONEPS 4자리
    business_group: str | None = None      # 신규 — derived
```

`predict_price(...)` 시그니처에 두 신규 인자 추가, **모두 optional**. 호출처(`OpportunityAnalysisService`, `BidDecisionService`, paper backtest)는 Project row에서 코드를 읽어 채운다. 코드 없으면 group은 `None`.

### 4.2 business_group 도출

`app/ai/business_group.py` 신규:

```python
def resolve_business_group(code: str | None) -> str | None:
    """Map KONEPS 업종코드 prefix → 도메인 그룹. config-driven."""
```

기본 룰:

- `04`* → `"construction"`
- `06`* → `"service"`
- `01`* / `02`* → `"goods"`
- 기타/None → `None` (fallback path)

룰은 `settings.BUSINESS_GROUP_CODE_PREFIXES: dict[str, list[str]]`로 외부화.

### 4.3 Predictor 분기

`app/ai/predictors/historical.py::select_competitive_base_rate(...)`에 `business_group` 인자 추가. 그룹 분기:

```python
if business_group == "construction":
    # 단봉 분포(p25=p75=0.903). recent_target 비중 ↑, std-clip narrow
    base_rate = recent_target * 0.6 + median_rate * 0.3 + heuristic * 0.1
elif business_group == "service":
    # 양봉 분포(0.88 vs 0.93). competitive_quantile 비중 ↑로 가격경쟁 mode 우선
    base_rate = competitive_quantile_rate * 0.5 + median_rate * 0.35 + heuristic * 0.15
else:
    # 기존 5-카테고리 로직 유지 (fallback)
    ...
```

기존 `category`-keyed 분기는 코드를 모르는 row에서만 fallback으로 동작 (deprecated 경로지만 호환).

### 4.4 Guardrail 재구성

`app/core/config.py`:

```python
PREDICTION_GROUP_MINIMUM_BID_RATES: dict[str, float] = {
    "construction": 0.87,   # 낙찰하한선
    "service": 0.70,        # 연구용역 가격경쟁 모드 반영
    "goods": 0.84,
}
PREDICTION_GROUP_MAXIMUM_BID_RATES: dict[str, float] = {
    "construction": 0.93,   # 기존 유지
    "service": 1.00,        # 협상 모드 보존
    "goods": 1.00,
}
```

`_apply_prediction_guardrails` resolve 순서: `business_group` > 기존 `category` > 글로벌 default. 기존 `PREDICTION_CATEGORY_*` 키는 한 릴리즈 동안 호환.

### 4.5 Calibration 데이터 소스

학습 시점 manifest에 저장:

```json
"group_calibration": {
  "construction": {"median_rate": 0.903, "std": 0.030, "p25": 0.902, "p75": 0.905, "sample_count": 9880},
  "service":      {"median_rate": 0.883, "std": 0.059, "p25": 0.880, "p75": 0.931, "sample_count": 8047}
}
```

런타임이 manifest를 읽어 `select_competitive_base_rate`에 prior로 주입 — 학습 데이터 변동에 따라 자동 갱신. 모델 코드는 변동 없음.

### 4.6 ML release manifest 변경

`scripts/promote_ml_release.py preflight-rollout` gate에 추가:

- `group_calibration` 블록 존재 여부
- 그룹별 `sample_count` ≥ 임계값 (`construction: 500`, `service: 500`)
- 직전 release 대비 `group_error_delta` 회귀 차단 (e.g., +5%p 이상 악화 시 reject)

### 4.7 테스트

- `tests/test_business_group_resolver.py` — prefix 룰 정확성
- `tests/test_predictor_business_group.py` — 그룹별 base_rate 산출 분기, fallback 동작
- `tests/test_prediction_guardrails.py` — group 키 우선 적용, category fallback, 둘 다 없을 때 default
- `tests/test_ml_release.py` — manifest gate가 `group_calibration`/`group_error_delta` 검증

## 5. 평가 & 롤아웃

### 5.1 Backtest 그룹 분할

`app/ai/predictor_backtest.py::build_predictor_backtest_report`에 `business_group` 차원 추가:

```python
report = {
    "overall": {...},
    "by_group": {
        "construction": {"avg_abs_error_rate": ..., "median": ..., "p90": ..., "sample_count": ...},
        "service":      {"avg_abs_error_rate": ..., "median": ..., "p90": ..., "sample_count": ...},
        "ungrouped":    {...}
    },
    "by_predictor_x_group": {...}
}
```

판정 메트릭: `avg_abs_error_rate` (winning_rate − predicted_rate 절댓값 평균). 그룹별 계산.

### 5.2 합격 기준 (조정 가능 기본값)

| 메트릭 | 기준 |
|---|---|
| 전체 평균 오차율 회귀 | ≤ +0.5%p |
| construction 그룹 오차율 | 기존 대비 동등 (±0.5%p) |
| service 그룹 오차율 | **기존 대비 −2.0%p 이상 개선** |
| 그룹 sample_count 미달 | ≥ 500건 미달 시 group_calibration 적용 보류 |

### 5.3 롤아웃 단계

1. **Phase A PR** — 스키마 + collector + 백필 스크립트 + Phase A 테스트. 백필은 `--dry-run`으로 먼저, audit JSON 검토 후 본 실행. **coverage ≥ 95% 확인 후에만 Phase B 머지.**
2. **Phase B (no-op) PR** — Context/predictor 인자 추가. business_group 미설정 시 기존 path. 단위 테스트만. 운영 영향 0.
3. **Phase B (활성) PR** — guardrail group 키, manifest `group_calibration` 소비, predictor 분기 활성. `predictor_backtest` 그룹별 결과 PR 설명에 첨부.
4. **Manifest gate PR** — `promote_ml_release.py preflight-rollout` group 검사 추가.
5. **운영 release** — 새 manifest로 `ml-release-preflight` 통과 후 promote. Telegram 알림 + operations dashboard ML release 카드로 추적.

각 PR은 워크플로(`feature/<slug>` 또는 `chore/<slug>` → PR → `/code-review` → 머지) 적용.

### 5.4 롤백 컨트랙트

- 운영 중 service 그룹 오차율이 +2%p 이상 악화 → manifest의 `group_calibration` 블록을 비우고 재promote → 자동 fallback path (현 동작과 동일).
- Alembic 마이그레이션 Down 가능. 단 백필된 코드 데이터는 함께 손실되므로 운영에서는 컬럼을 비우는 정도로 한정 권장.
- 코드 분기 disable 스위치: `BUSINESS_GROUP_CALIBRATION_ENABLED: bool = True`. 즉시 끄고 기존 path로 회귀.

### 5.5 후속 (이번 spec 범위 외)

- N 그룹 분리 (`goods` 별도 calibration) — 데이터 누적 후
- 업종코드 4자리 세분류 단위 embedding feature
- LSTM/Ensemble 모델에 group head 추가 — 우선 historical 단일 분기로 효과 측정 후 결정
- `business_type_label` UI 노출 — 전략 편집기/Projects에 필터로 추가

## 6. 단일 출처(SoT) 매핑

| 항목 | 위치 |
|---|---|
| 업종코드 → 그룹 매핑 룰 | `settings.BUSINESS_GROUP_CODE_PREFIXES` |
| 그룹별 guardrail | `settings.PREDICTION_GROUP_{MIN,MAX}_BID_RATES` |
| 그룹별 calibration prior | manifest `group_calibration` 블록 |
| 그룹별 backtest 결과 | `predictor_backtest.report.by_group` |
| 그룹별 promotion gate | `promote_ml_release.py preflight-rollout` |
| disable 스위치 | `settings.BUSINESS_GROUP_CALIBRATION_ENABLED` |
| coverage 게이트 | `settings.BUSINESS_TYPE_COVERAGE_GATE` (기본 0.95) |

## 7. 오픈 질문 (구현 단계에서 확정)

- KONEPS HTML 셀의 업종코드 형식이 정확히 4자리 + 라벨인지, 라벨만인지 — collector 확장 전 샘플 5건으로 확인.
- 백필 fallback 룰 정확도 — title regex가 어디까지 매칭 가능한지 — 1차 dry-run 결과로 결정.
- service 그룹 양봉이 단순 협상/가격경쟁 2-mode인지 더 세분(연구/감리/일반)되는지 — Phase B에서 sub-group 도입 여부 결정.

---

**Self-review 결과**

- 모든 섹션에 placeholder/TBD 없음. 합격 기준 임계값은 명시값(±0.5%p, −2.0%p, 95%).
- 내부 일관성: Phase A coverage 임계값(95%)이 §2.1, §3.4, §6에 동일하게 표기됨. group prefix 룰이 §4.2와 §6에서 같은 키(`BUSINESS_GROUP_CODE_PREFIXES`)로 일치.
- 스코프: 단일 spec — Phase A·B를 묶어 설계하되 PR 단계로 분리(§5.3). N그룹/embedding/LSTM head는 §1.3 비-목표 + §5.5 후속으로 명시.
- 모호성: "service 그룹 양봉 분포"의 두 mode가 어떻게 자동 분리되는지는 §7 오픈 질문으로 명시.
