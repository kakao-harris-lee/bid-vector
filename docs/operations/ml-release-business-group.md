# Phase B 업종 그룹 인식 ML 릴리스 운영 절차

> **관련 문서**
> - 설계 명세: `docs/superpowers/specs/2026-05-25-business-type-aware-prediction-design.md`
> - 구현 계획: `docs/superpowers/plans/2026-05-25-business-type-aware-prediction.md`
> - 기존 ML 릴리스 절차: `docs/ml-image-separation.md`

---

## 무엇이 바뀌었나

- **`Project.business_type_code` / `business_type_label` 컬럼 추가** — 나라장터 공고 상세 HTML 파싱 또는 제목 규칙(regex)으로 채워지며, predictor가 업종 그룹을 판별하는 키로 사용된다.
- **업종 그룹 캘리브레이션** — 히스토리컬 예측기가 카테고리별 가중치 대신 업종 그룹(construction / service / goods)별 낙찰가율 분포를 1차 분기점으로 사용한다(`BUSINESS_GROUP_CALIBRATION_ENABLED`).
- **그룹별 guardrail 키** — `PREDICTION_GROUP_MINIMUM_BID_RATES` / `PREDICTION_GROUP_MAXIMUM_BID_RATES`가 카테고리 guardrail을 오버라이드한다.
- **매니페스트 `group_calibration` 블록** — `create-manifest` 시 각 그룹의 `sample_count`가 기록되고, `preflight-rollout` gate가 `GROUP_CALIBRATION_MIN_SAMPLES` 미달 그룹을 거부한다.
- **백테스트 `by_group` 출력** — `backtest_synthetic_operators.py` 출력에 그룹별 MAE와 win-rate 프록시가 추가된다.

---

## 릴리스 체크리스트

### 1. business_type_code 백필

```bash
# ① dry-run으로 예상 범위 확인
python scripts/backfill_business_type.py --dry-run --limit 500

# 출력에서 candidates / updated_from_detail / updated_from_title_rule / failed 확인
# failed 비율이 10% 초과이면 수집 상태 점검 후 재시도

# ② 실제 적용 (기본값 --limit 200; 전체 DB는 --limit 를 크게 설정)
python scripts/backfill_business_type.py --limit 1000

# 감사 보고서: reports/business-type-backfill/run.json
```

커버리지 확인:

```sql
SELECT
    COUNT(*) FILTER (WHERE business_type_code IS NOT NULL)::float / COUNT(*) AS coverage
FROM projects;
-- BUSINESS_TYPE_COVERAGE_GATE(기본 0.95) 이상이어야 다음 단계 진행
```

### 2. ML 아티팩트 생성 및 매니페스트 작성

```bash
python scripts/promote_ml_release.py create-manifest \
    --release-tag <tag>                         \
    --embedding-model-path models/              \
    --lstm-artifact-path models/lstm.json       \
    --ensemble-artifact-path models/ensemble.json \
    --predictor-backtest-report reports/predictor-backtest.json \
    --git-sha $(git rev-parse HEAD)             \
    --notes "Phase B: 업종 그룹 캘리브레이션 적용"
```

매니페스트가 `models/manifests/<tag>.json`에 생성된다.
`summary.group_calibration` 블록에 `construction / service / goods` 각 그룹의 `sample_count`가 포함되어 있어야 한다.

### 3. Preflight gate (서명 검증 + 그룹 샘플 임계 확인)

```bash
python scripts/promote_ml_release.py preflight-rollout \
    --manifest <tag>          \
    --require-signature
```

- 서명 검증 실패 → `ML_RELEASE_MANIFEST_SIGNING_KEY` 환경 변수 점검
- `group_calibration sample_count < GROUP_CALIBRATION_MIN_SAMPLES(기본 100)` → 해당 그룹 데이터 부족; 백필 범위를 늘리거나 `GROUP_CALIBRATION_MIN_SAMPLES`를 일시적으로 낮춰서 재시도
- `passed: true`가 출력되면 다음 단계 진행

### 4. 백테스트 검증 (by_group 블록 확인)

```bash
python scripts/backtest_synthetic_operators.py \
    --start-date 2025-01-01 --end-date 2025-12-31 --limit 200
```

출력 JSON의 `by_group` 블록에서 확인할 항목:

| 지표 | 기준 |
|------|------|
| `mae` (각 그룹) | Phase A 대비 같거나 낮을 것 |
| `win_rate_proxy` | 그룹별 0.30 이상 |
| `settled_count` | 그룹별 20 이상 (샘플 신뢰도) |

MAE가 현저히 높아진 그룹이 있으면 해당 그룹의 백필 품질을 재검토한다.

### 5. 운영 프로모션

```bash
# 매니페스트 적용 (임베딩 재빌드 없이)
python scripts/promote_ml_release.py apply-manifest \
    --manifest <tag>

# 임베딩도 재빌드해야 하는 경우 (모델 교체 시)
python scripts/promote_ml_release.py apply-manifest \
    --manifest <tag>             \
    --rebuild-embeddings         \
    --limit 500

# Compose 서비스 재시작
python scripts/promote_ml_release.py apply-manifest \
    --manifest <tag>             \
    --restart-compose            \
    --wait-for-health-url http://localhost:8000/health
```

재시작 후 `/health` 응답과 `GET /api/v1/analytics/operations-dashboard`의 `predictor` 블록을 확인한다.

---

## 설정 키 레퍼런스

| 키 | 기본값 | 설명 |
|----|--------|------|
| `BUSINESS_GROUP_CODE_PREFIXES` | `{"construction":["04"], "service":["06"], "goods":["01","02"]}` | `business_type_code` 앞 두 자리로 그룹을 매핑. 새 업종 코드 대역이 추가되면 여기에 등록. |
| `BUSINESS_TYPE_COVERAGE_GATE` | `0.95` | 백필 후 coverage가 이 값 미만이면 릴리스 진행 차단. |
| `BUSINESS_GROUP_CALIBRATION_ENABLED` | `true` | `false`로 설정하면 그룹 캘리브레이션을 건너뛰고 카테고리 전용 경로로 폴백. 킬 스위치. |
| `PREDICTION_GROUP_MINIMUM_BID_RATES` | `{"construction":0.87,"service":0.70,"goods":0.84}` | 그룹별 낙찰가율 하한. 카테고리 guardrail보다 우선 적용. |
| `PREDICTION_GROUP_MAXIMUM_BID_RATES` | `{"construction":0.93,"service":1.00,"goods":1.00}` | 그룹별 낙찰가율 상한. |
| `GROUP_CALIBRATION_MIN_SAMPLES` | `100` | preflight gate에서 각 그룹의 `sample_count`가 이 값 이상이어야 통과. |
| `BUSINESS_TYPE_TITLE_RULES` | (5개 기본 패턴) | `backfill_business_type.py`의 title-rule fallback에서 사용하는 regex → code 매핑. |

설정은 `.env` 파일 또는 환경 변수로 주입한다. JSON 형식의 dict 값은 큰따옴표 이스케이프에 주의:

```bash
PREDICTION_GROUP_MINIMUM_BID_RATES='{"construction":0.87,"service":0.72,"goods":0.84}'
```

---

## 킬 스위치 및 롤백

릴리스 후 예측 품질이 저하되었다고 판단되면:

```bash
# 1. 그룹 캘리브레이션 즉시 비활성화 (재배포 불필요 — API 워커 재시작만)
BUSINESS_GROUP_CALIBRATION_ENABLED=false

# 2. API 워커 재시작
docker compose restart api

# 또는 Compose 전체
docker compose up -d api
```

이 설정으로 predictor는 카테고리 전용 경로로 폴백한다. DB 변경이나 마이그레이션은 불필요하다.

이전 매니페스트로 완전히 되돌리려면:

```bash
python scripts/promote_ml_release.py apply-manifest \
    --manifest <이전-tag>       \
    --restart-compose
```

`models/manifests/archive/`에 교체된 매니페스트가 보존된다(`ML_RELEASE_MANIFEST_RETENTION_LIMIT` 기본 20개).

---

## 모니터링

릴리스 후 운영 대시보드(`GET /api/v1/analytics/operations-dashboard`) 및 백테스트 리포트에서 확인할 지표:

**1. Predictor 품질 지표 (`predictor` 블록)**
- `by_group.{group}.mae` 추이 — 릴리스 전 대비 증가 여부
- `by_group.{group}.win_rate_proxy` — 그룹별 가격 기준 추정 낙찰률 (0.30 미만이면 조사 필요)
- `by_group.{group}.settled_count` — 유효 샘플 수; 샘플이 적으면 MAE 신뢰도가 낮음

**2. 업종 분류 커버리지**
- `business_type_code IS NULL` 비율이 릴리스 이전보다 높아졌다면 수집 파이프라인 점검
- 새 업종 코드가 `BUSINESS_GROUP_CODE_PREFIXES` 매핑 밖이면 `unclassified` 그룹으로 분류되어 카테고리 경로로 폴백됨 — 코드 등록 후 백필 재실행 필요

**3. 그룹별 적정성 추정 분포**
- 운영 대시보드의 `bid_rate_distribution` 히스토그램에서 그룹별 추천가율이 지나치게 한쪽으로 쏠리거나 guardrail 경계에 집중되면 캘리브레이션 데이터 품질 확인
- `PREDICTION_GROUP_MINIMUM_BID_RATES` / `MAXIMUM_BID_RATES`를 조정해 분포를 보정하고 API 워커를 재시작

> **주의**: `win_rate_proxy`는 "가격 기준 추정 낙찰"이지 "실제 낙찰"이 아니다. 실제 낙찰 여부는 나라장터 낙찰 결과 수집 후 `BidDecisionRecord.actual_outcome`에서 확인한다.
