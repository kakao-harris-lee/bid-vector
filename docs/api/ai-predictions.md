# AI Predictions API

> 베이스 경로: `/api/v1/predictions` · 베이스 URL 예시: `http://localhost:3000`
> 인증: 세 엔드포인트 모두 명시적 Bearer 토큰 의존성이 없습니다. 단, `/price`는 내부에서 canonical operator 계정을 강제 확보해 예측 결과를 그 운영자에게 귀속시킵니다(단일 운영자 모델).

가격 예측·투찰 추천·문서 분석 3종 AI 엔드포인트입니다. 모두 요청의 `project_id`로 대상 공고(`Project`)를 먼저 조회하고, 없으면 `404`를 반환합니다.

## 목차
- [POST /api/v1/predictions/price](#post-apiv1predictionsprice) — 공고 적정 투찰가·투찰률 예측 (결과 영속화)
- [POST /api/v1/predictions/bid-recommendation](#post-apiv1predictionsbid-recommendation) — 운영자 이력 기반 투찰 추천
- [POST /api/v1/predictions/analyze-document](#post-apiv1predictionsanalyze-document) — 공고 문서 요구사항·리스크 분석

---

## POST /api/v1/predictions/price

대상 공고에 대해 적정 투찰가와 투찰률(bid rate)을 예측합니다. 동일 카테고리의 최근 낙찰 이력 시계열(explicit bid-rate 기반, 최대 80건)을 끌어와 통계/히스토리컬 블렌드 방식으로 가격을 산출하고, 운영자 피드백 보정을 반영합니다. 결과는 보수/기준/공격(`conservative`/`base`/`aggressive`) 시나리오 후보와 함께 반환됩니다.

- 언제 쓰나: 운영자가 특정 공고의 투찰가를 결정하기 직전, 가격 예측 화면에서 호출. 결과는 단일 operator에 귀속되어 `PricePrediction` 레코드로 저장됩니다(이후 추적·피드백 보정에 사용).
- 도메인: predictor guardrail이 적용됩니다. 카테고리/공고별 낙찰하한(`floor_bid_rate`/`floor_price`) 미만 투찰을 차단하도록 예측가를 클램프하며, 공고별 법정 하한은 `legal_floor_bid_rate`로 전달할 수 있습니다. 최종 후보에는 하한 안전마진(`safe_floor_bid_rate`)이 적용되고, 적용 여부·사유가 `guardrail_applied`/`guardrail_reason`으로 노출됩니다. 최종 투찰 후보 금액은 기본 10원 단위(`bid_price_granularity`)로 내림 처리하되, 하한 안전가격을 침범하면 해당 단위로 올림 보정합니다. `procurement_rate_band`에는 `service_price_competitive`, `service_direct_negotiated`, `service_high_negotiated`, `goods_price_competitive`, `goods_deep_discount` 같은 세부 조달 밴드가 노출될 수 있습니다. `pricing_mode`는 이력이 충분하면 `historical_blend`, 부족하면 `heuristic`입니다.
- 부수효과: `PricePrediction` 레코드 1건이 DB에 영속화됩니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 대상 공고 ID |
| body | budget_estimate | float | 예 | 추정 예산(KRW) |
| body | category | str | 예 | 공고 카테고리 |
| body | description | str | 예 | 공고 설명 텍스트 |
| body | agency_name | str\|null | 아니오 | 발주 기관명(기관 매칭 보정용) |
| body | legal_floor_bid_rate | float\|null | 아니오 | 공고별 법정 낙찰하한율. `0.87995` 또는 `87.995` 모두 허용 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/predictions/price \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 1024,
    "budget_estimate": 480000000,
    "category": "정보통신공사",
    "description": "관내 행정망 네트워크 장비 교체 및 구축",
    "agency_name": "한국조달연구원",
    "legal_floor_bid_rate": 87.995
  }'
```
```json
{
  "project_id": 1024,
  "budget_estimate": 480000000,
  "category": "정보통신공사",
  "description": "관내 행정망 네트워크 장비 교체 및 구축",
  "agency_name": "한국조달연구원",
  "legal_floor_bid_rate": 87.995
}
```

**응답 200**
```json
{
  "predicted_price": 432480000,
  "price_range_min": 425280000,
  "price_range_max": 439200000,
  "confidence_score": 0.78,
  "model_version": "2026.04.1",
  "predictor_name": "historical_statistical",
  "predictor_family": "statistical",
  "fallback_reason": null,
  "selector_name": "configured_preference",
  "selection_reason": "historical_sample_sufficient",
  "backtest_sample_count": 64,
  "backtest_average_absolute_error_rate": 0.018,
  "backtest_report": null,
  "training_window_size": 80,
  "pricing_mode": "historical_blend",
  "historical_sample_size": 72,
  "agency_match_sample_size": 9,
  "predicted_bid_rate": 0.901,
  "competitive_target_bid_rate": 0.8975,
  "procurement_rate_band": "service_price_competitive",
  "bid_rate_candidates": [
    {
      "label": "conservative",
      "bid_rate": 0.908,
      "predicted_price": 435840000,
      "confidence_weight": 0.3,
      "guardrail_applied": false,
      "pre_guardrail_bid_rate": null,
      "pre_guardrail_price": null,
      "price_granularity_applied": false,
      "pre_granularity_price": null
    },
    {
      "label": "base",
      "bid_rate": 0.901,
      "predicted_price": 432480000,
      "confidence_weight": 0.5,
      "guardrail_applied": false,
      "pre_guardrail_bid_rate": null,
      "pre_guardrail_price": null,
      "price_granularity_applied": false,
      "pre_granularity_price": null
    },
    {
      "label": "aggressive",
      "bid_rate": 0.894,
      "predicted_price": 429120000,
      "confidence_weight": 0.2,
      "guardrail_applied": false,
      "pre_guardrail_bid_rate": null,
      "pre_guardrail_price": null,
      "price_granularity_applied": false,
      "pre_granularity_price": null
    }
  ],
  "reserve_price_context": {
    "sample_count": 41,
    "average_reserve_span_rate": 0.03,
    "estimated_price_sample_count": 41,
    "average_estimated_price_rate": 0.982,
    "median_estimated_price_rate": 0.981,
    "median_bid_to_estimated_price_rate": 0.918,
    "average_selected_number": 3.2,
    "frequent_selected_numbers": [1, 3, 4, 13]
  },
  "feedback_calibration": {
    "sample_count": 18,
    "agency_match_sample_count": 5,
    "average_signed_error_rate": -0.004,
    "average_absolute_error_rate": 0.012,
    "applied_adjustment_rate": -0.002
  },
  "guardrail_applied": false,
  "guardrail_reason": null,
  "legal_floor_bid_rate": 0.87995,
  "floor_guardrail_source": "legal",
  "floor_bid_rate": 0.87995,
  "floor_price": 422376000,
  "floor_safety_margin_rate": 0.001,
  "safe_floor_bid_rate": 0.88095,
  "safe_floor_price": 422856000,
  "ceiling_bid_rate": 1.0,
  "ceiling_price": 480000000,
  "bid_price_granularity": 10,
  "bid_price_rounding_mode": "floor",
  "price_granularity_applied": false,
  "explanation": "최근 동일 카테고리 낙찰 이력 72건과 기관 매칭 9건을 블렌드해 산출. 낙찰하한 0.88 이상 유지."
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `project_id`에 해당하는 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 요청 본문 필수 필드 누락·타입 불일치 |

```json
{ "detail": "Project not found" }
```

---

## POST /api/v1/predictions/bid-recommendation

대상 공고의 예산·카테고리·설명과 운영자 과거 데이터를 입력으로 AI 기반 투찰 추천가(`recommended_bid`)와 근거(`reasoning`)를 산출합니다.

- 언제 쓰나: 정밀 가격 예측(`/price`)과 별개로, 운영자 이력을 곁들인 간단한 추천이 필요할 때.
- 도메인: DB write나 operator 귀속이 없는 순수 추론. 응답 `reasoning`에 추천 근거가, `market_analysis`(선택)에 시장 맥락이 담깁니다. `/price`의 guardrail 필드 세트는 포함되지 않습니다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 대상 공고 ID |
| body | user_historical_data | dict\|null | 아니오 | 운영자 과거 투찰 데이터 |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/predictions/bid-recommendation \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 1024,
    "user_historical_data": { "avg_bid_rate": 0.903, "win_count": 12 }
  }'
```
```json
{
  "project_id": 1024,
  "user_historical_data": { "avg_bid_rate": 0.903, "win_count": 12 }
}
```

**응답 200**
```json
{
  "recommended_bid": 433440000,
  "confidence_score": 0.71,
  "reasoning": "운영자 평균 투찰률 0.903과 카테고리 시장가를 반영해 0.903 수준 권장.",
  "market_analysis": {
    "category_median_bid_rate": 0.899,
    "competitor_estimate": 7
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `project_id`에 해당하는 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 요청 본문 필수 필드 누락·타입 불일치 |

```json
{ "detail": "Project not found" }
```

---

## POST /api/v1/predictions/analyze-document

제출된 공고 문서 본문을 분석해 핵심 요구사항(`key_requirements`), 난이도(`complexity_score`), 예상 공수(`estimated_effort`), 리스크(`risks`)를 추출합니다.

- 언제 쓰나: 운영자가 공고 규격서·제안요청서 등을 검토할 때, 문서 텍스트를 붙여 넣어 요구사항·리스크를 빠르게 파악.
- 도메인: `project_id`로 공고 존재만 확인하고, 실제 분석은 전달된 `document_content`/`document_type`에만 기반합니다(문서 본문은 호출자가 제공). DB write 없음.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | project_id | int | 예 | 대상 공고 ID |
| body | document_content | str | 예 | 분석할 문서 본문 텍스트 |
| body | document_type | str | 예 | 문서 종류(예: 규격서, 제안요청서) |

**요청 예시**
```bash
curl -X POST http://localhost:3000/api/v1/predictions/analyze-document \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": 1024,
    "document_content": "본 사업은 관내 행정망 L3 스위치 24대 교체와 24개월 유지보수를 포함한다...",
    "document_type": "규격서"
  }'
```
```json
{
  "project_id": 1024,
  "document_content": "본 사업은 관내 행정망 L3 스위치 24대 교체와 24개월 유지보수를 포함한다...",
  "document_type": "규격서"
}
```

**응답 200**
```json
{
  "key_requirements": [
    "L3 스위치 24대 교체",
    "24개월 유지보수 포함",
    "기존 행정망 무중단 전환"
  ],
  "complexity_score": 0.64,
  "estimated_effort": 120.0,
  "risks": [
    "무중단 전환 실패 시 행정 서비스 중단",
    "납기 내 장비 수급 지연 가능성"
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | `project_id`에 해당하는 공고 없음 (`{"detail": "Project not found"}`) |
| 422 | 요청 본문 필수 필드 누락·타입 불일치 |

```json
{ "detail": "Project not found" }
```
