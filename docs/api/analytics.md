# Analytics API

> 베이스 경로: `/api/v1/analytics` · 베이스 URL 예시: `http://localhost:3000`
> 인증: **불필요**. 단일 운영자 모델이라 서버가 canonical 운영자를 암묵 해석한다(응답의 `operator_id`). Analytics 라우터의 어떤 엔드포인트도 Bearer 토큰을 요구하지 않는다.
> 공통 에러: 모든 엔드포인트는 쿼리/경로/바디 검증 실패 시 `422`(`{"detail": [...]}`)를 반환한다.
> 도메인: 결정 분석(decision analytics) · 예측 리포팅(prediction reporting) · 실험(decision experiments).

## 목차
- [POST /api/v1/analytics/event](#post-apiv1analyticsevent) — 분석 이벤트 적재
- [GET /api/v1/analytics/summary](#get-apiv1analyticssummary) — 저장소 전역 기간 요약
- [GET /api/v1/analytics/operator-stats](#get-apiv1analyticsoperator-stats) — 단일 운영자 통계
- [GET /api/v1/analytics/prediction-feedback](#get-apiv1analyticsprediction-feedback) — 예측·추천 vs 실 낙찰 정확도
- [GET /api/v1/analytics/prediction-observability](#get-apiv1analyticsprediction-observability) — predictor 선택/fallback/guardrail/정확도 관측
- [GET /api/v1/analytics/operations-dashboard](#get-apiv1analyticsoperations-dashboard) — 운영 건강 대시보드(수집/전략/태스크/알림/ML릴리스)
- [GET /api/v1/analytics/decision-insights](#get-apiv1analyticsdecision-insights) — 결정 신호 요약
- [GET /api/v1/analytics/decision-funnel](#get-apiv1analyticsdecision-funnel) — 결정 퍼널 + 비교/추세/세그먼트
- [GET /api/v1/analytics/decision-recommendations](#get-apiv1analyticsdecision-recommendations) — 튜닝 권고 + 실험 계획
- [POST /api/v1/analytics/decision-experiments](#post-apiv1analyticsdecision-experiments) — 실험 계획 등록
- [GET /api/v1/analytics/decision-experiments](#get-apiv1analyticsdecision-experiments) — 실험 런 목록
- [GET /api/v1/analytics/decision-experiments/{experiment_run_id}](#get-apiv1analyticsdecision-experimentsexperiment_run_id) — 실험 런 상세
- [PATCH /api/v1/analytics/decision-experiments/{experiment_run_id}](#patch-apiv1analyticsdecision-experimentsexperiment_run_id) — 실험 런 수동 갱신
- [POST /api/v1/analytics/decision-experiments/{experiment_run_id}/evaluate](#post-apiv1analyticsdecision-experimentsexperiment_run_idevaluate) — 재평가 비동기 큐잉
- [POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-thresholds](#post-apiv1analyticsdecision-experimentsexperiment_run_idapply-thresholds) — 임계값 전략 반영
- [POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-strategy](#post-apiv1analyticsdecision-experimentsexperiment_run_idapply-strategy) — 워크로드/카테고리 전략 반영
- [GET /api/v1/analytics/user-stats/{user_id}](#get-apiv1analyticsuser-statsuser_id) — (deprecated) 레거시 통계 alias

---

## POST /api/v1/analytics/event
운영자 워크플로에서 발생한 분석 이벤트 한 건을 적재한다. 프론트엔드/내부 작업이 사용자 행동이나 내부 텔레메트리를 기록할 때 호출한다. 이벤트는 항상 canonical 운영자에 귀속된다. `event_data`(object)는 서버에서 문자열로 직렬화되어 저장된다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | event_type | string | 예 | 이벤트 종류 키 |
| body | event_data | object | 예 | 이벤트 부가 데이터(문자열로 저장됨) |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/analytics/event" \
  -H "Content-Type: application/json" \
  -d '{"event_type": "project.viewed", "event_data": {"project_id": 4821, "from": "watchlist"}}'
```
```json
{ "event_type": "project.viewed", "event_data": { "project_id": 4821, "from": "watchlist" } }
```

**응답 200**
```json
{ "status": "logged" }
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | event_type/event_data 누락 또는 타입 오류 |

---

## GET /api/v1/analytics/summary
지정 기간 동안의 저장소 전역 요약(입찰 수, 신규 공고 수, 운영자 이벤트 수)을 반환한다. 대시보드 상단 요약에 쓴다. `total_events`는 내부 텔레메트리(`telegram.delivery`, `telegram.strategy.pending_edit`)를 제외한다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~90) | 아니오 | 집계 기간(일), 기본 7 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/summary?days=7"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 7,
  "total_bids": 12,
  "total_projects": 134,
  "total_events": 87,
  "mode": "single_operator"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days 범위(1~90) 위반 |

---

## GET /api/v1/analytics/operator-stats
단일 운영자의 입찰/이벤트 통계를 반환한다. `total_bids`/`total_events`는 기간 내, `bids_count`는 전체 누적 입찰 수다. `requested_user_id`는 이 엔드포인트에서는 채워지지 않는다(레거시 alias 전용).

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/operator-stats?days=30"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 30,
  "total_bids": 41,
  "total_events": 512,
  "bids_count": 318,
  "requested_user_id": null,
  "mode": "single_operator"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days 범위(1~365) 위반 |

---

## GET /api/v1/analytics/prediction-feedback
저장된 예측가/추천 투찰가를 실제 개찰 결과와 대조해 정확도를 집계한다. predictor 품질과 추천 엔진 개선 효과 추적에 쓴다. 오차율은 절대 오차율(낮을수록 정확)이며 `recommendation_improved_vs_prediction`은 추천이 순수 예측보다 실 낙찰가에 더 가까웠는지를 나타낸다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 90 |
| query | limit | integer (1~100) | 아니오 | items 최대 개수, 기본 20 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/prediction-feedback?days=90&limit=20"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 90,
  "result_count": 56,
  "prediction_sample_count": 48,
  "recommendation_sample_count": 42,
  "average_prediction_error_rate": 0.041,
  "average_recommendation_error_rate": 0.029,
  "prediction_within_1_percent_count": 11,
  "prediction_within_3_percent_count": 30,
  "recommendation_within_1_percent_count": 18,
  "recommendation_within_3_percent_count": 35,
  "recommendation_better_than_prediction_count": 27,
  "items": [
    {
      "project_id": 4821,
      "project_title": "OO청사 통합관제시스템 유지보수",
      "tender_result_id": 991,
      "result_status": "awarded",
      "announced_at": "2026-04-18T05:00:00Z",
      "winning_amount": 487300000,
      "winning_rate": 0.8712,
      "latest_prediction_id": 7720,
      "predicted_price": 491000000,
      "prediction_delta_amount": 3700000,
      "prediction_error_rate": 0.0076,
      "latest_decision_record_id": 3310,
      "recommended_amount": 488500000,
      "recommendation_delta_amount": 1200000,
      "recommendation_error_rate": 0.0025,
      "recommendation_improved_vs_prediction": true
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/limit 범위 위반 |

---

## GET /api/v1/analytics/prediction-observability
predictor 선택 분포, fallback·guardrail 발생, 결과 정확도를 종합 관측 지표로 요약한다. ML 예측 파이프라인 건강성 모니터링에 쓴다. `guardrail_rate`는 카테고리 낙찰하한 등 guardrail이 예측을 보정/차단한 비율이다(predictor guardrail은 우회 금지).

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 90 |
| query | trend_bucket_days | integer (1~90) | 아니오 | 추세 버킷 크기(일), 기본 14 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/prediction-observability?days=90&trend_bucket_days=14"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 90,
  "prediction_count": 132,
  "fallback_count": 14,
  "fallback_rate": 0.106,
  "guardrail_count": 9,
  "guardrail_rate": 0.068,
  "accuracy_sample_count": 56,
  "average_absolute_error_rate": 0.038,
  "within_1_percent_count": 13,
  "within_3_percent_count": 34,
  "predictor_breakdown": [
    {
      "predictor_name": "lgbm-v3",
      "predictor_family": "gradient_boosting",
      "prediction_count": 90,
      "selection_rate": 0.682,
      "fallback_count": 4,
      "fallback_rate": 0.044,
      "guardrail_count": 5,
      "guardrail_rate": 0.056,
      "accuracy_sample_count": 40,
      "average_absolute_error_rate": 0.031,
      "within_1_percent_count": 10,
      "within_3_percent_count": 26,
      "average_confidence_score": 0.74,
      "average_training_window_size": 1800,
      "average_predicted_bid_rate": 0.873
    }
  ],
  "pricing_mode_breakdown": [
    {
      "pricing_mode": "rate_based",
      "prediction_count": 110,
      "selection_rate": 0.833,
      "fallback_count": 10,
      "fallback_rate": 0.091,
      "guardrail_count": 7,
      "guardrail_rate": 0.064
    }
  ],
  "performance_trend": [
    {
      "bucket_start": "2026-03-01T00:00:00Z",
      "bucket_end": "2026-03-15T00:00:00Z",
      "prediction_count": 22,
      "fallback_rate": 0.091,
      "guardrail_rate": 0.045,
      "accuracy_sample_count": 9,
      "average_absolute_error_rate": 0.042,
      "backtest_sample_count": 120,
      "average_backtest_error_rate": 0.036
    }
  ],
  "fallback_reason_breakdown": { "insufficient_history": 9, "model_load_error": 5 },
  "guardrail_reason_breakdown": { "below_category_floor": 9 }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/trend_bucket_days 범위 위반 |

---

## GET /api/v1/analytics/operations-dashboard
수집(crawl), 전략 모니터링, Celery 태스크, 알림(Telegram), ML 릴리스 다섯 영역의 운영 건강 지표를 한 번에 묶어 대시보드 카드로 반환한다. 운영 상태 페이지에서 쓴다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |
| query | recent_limit | integer (1~20) | 아니오 | 각 영역 최근 목록 길이, 기본 5 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/operations-dashboard?days=30&recent_limit=5"
```

**응답 200** (지면상 일부 중첩 필드 축약)
```json
{
  "operator_id": 1,
  "period_days": 30,
  "crawl": {
    "job_count": 60,
    "completed_count": 57,
    "fallback_count": 2,
    "failed_count": 3,
    "success_rate": 0.95,
    "failure_rate": 0.05,
    "average_result_count": 142.3,
    "total_result_count": 8112,
    "last_success_at": "2026-05-29T03:00:00Z",
    "last_failure_at": "2026-05-22T03:00:00Z",
    "failure_reason_breakdown": { "timeout": 2, "parse_error": 1 },
    "recent_failures": [
      {
        "crawl_job_id": 4410,
        "source": "koneps_openapi",
        "target_date": "2026-05-22",
        "status": "failed",
        "error_message": "upstream timeout",
        "created_at": "2026-05-22T03:00:00Z",
        "completed_at": "2026-05-22T03:01:10Z"
      }
    ]
  },
  "strategy": {
    "run_count": 30,
    "completed_count": 29,
    "failed_count": 1,
    "running_count": 0,
    "completion_rate": 0.967,
    "failure_rate": 0.033,
    "evaluated_project_count": 1820,
    "selected_candidate_count": 96,
    "persisted_candidate_count": 88,
    "notification_count": 80,
    "selection_rate": 0.053,
    "persistence_rate": 0.917,
    "notification_rate": 0.909,
    "average_selected_candidates": 3.2,
    "last_completed_at": "2026-05-29T04:00:00Z",
    "last_failure_at": "2026-05-10T04:00:00Z",
    "failure_reason_breakdown": { "embedding_unavailable": 1 },
    "recent_failures": []
  },
  "tasks": {
    "broker": { "url": "memory://", "transport": "memory", "health_status": "healthy", "detail": "in-memory broker" },
    "result_backend": { "url": "cache+memory://", "transport": "memory", "health_status": "healthy", "detail": "ok" },
    "runtime": {
      "eager_mode": false,
      "inline_ml_tasks_allowed": false,
      "worker_concurrency": 4,
      "worker_prefetch_multiplier": 1,
      "worker_max_tasks_per_child": 100,
      "task_time_limit_seconds": 1800,
      "task_soft_time_limit_seconds": 1500,
      "result_expires_seconds": 86400,
      "task_track_started": true,
      "worker_send_task_events": true,
      "task_send_sent_event": true,
      "broker_connection_retry_on_startup": true,
      "broker_connection_max_retries": 10,
      "broker_publish_max_retries": 3,
      "health_status": "healthy",
      "detail": "ok"
    },
    "queues": [ { "queue": "ml", "task_count": 2, "task_names": ["reevaluate_decision_experiment"] } ],
    "tracked_task_count": 48,
    "queued_count": 1,
    "running_count": 0,
    "active_count": 1,
    "completed_count": 45,
    "failed_count": 2,
    "retry_count": 1,
    "failure_rate": 0.042,
    "stale_task_threshold_seconds": 3600,
    "stale_task_count": 0,
    "average_queue_wait_seconds": 2.4,
    "average_runtime_seconds": 38.1,
    "backlog_status": "healthy",
    "failure_status": "healthy",
    "risk_flags": [],
    "recent_delayed_tasks": [],
    "recent_failures": [],
    "recent_retries": []
  },
  "notifications": {
    "notification_count": 80,
    "unread_count": 3,
    "decision_notification_count": 52,
    "bid_submission_notification_count": 28,
    "telegram_configured": true,
    "telegram_delivery_attempt_count": 80,
    "telegram_sent_count": 78,
    "telegram_failed_count": 1,
    "telegram_pending_configuration_count": 0,
    "telegram_skipped_count": 1,
    "telegram_success_rate": 0.975,
    "telegram_status": "healthy",
    "telegram_detail": "ok",
    "telegram_status_counts": { "sent": 78, "failed": 1, "skipped": 1 },
    "telegram_failure_reason_breakdown": { "network_error": 1 },
    "recent_telegram_failures": [
      {
        "event_id": 9001,
        "notification_id": 5521,
        "source": "decision",
        "status": "failed",
        "detail": "network_error",
        "timestamp": "2026-05-27T06:12:00Z"
      }
    ]
  },
  "ml_release": {
    "manifest_dir": "models/releases",
    "manifest_count": 6,
    "remote_storage_configured": false,
    "remote_auto_publish": false,
    "retention_limit": 10,
    "status": "healthy",
    "detail": "latest release verified",
    "latest_release_tag": "ml-2026-05-20",
    "latest_manifest_path": "models/releases/ml-2026-05-20.json",
    "latest_validated_on": "2026-05-20T09:00:00Z",
    "latest_signature_status": "verified",
    "latest_gate_status": "passed",
    "latest_gate_passed": true,
    "latest_gate_policy": "require_signature",
    "latest_best_predictor_key": "lgbm-v3",
    "latest_dataset_quality_status": "ok",
    "latest_backtest_sample_count": 240,
    "latest_backtest_average_absolute_error_rate": 0.035,
    "backtest_status": "healthy",
    "backtest_detail": "within target",
    "recent_manifests": [
      {
        "manifest_path": "models/releases/ml-2026-05-20.json",
        "release_tag": "ml-2026-05-20",
        "validated_on": "2026-05-20T09:00:00Z",
        "signature_status": "verified",
        "gate_status": "passed",
        "gate_passed": true,
        "gate_policy": "require_signature",
        "backtest_sample_count": 240,
        "backtest_average_absolute_error_rate": 0.035,
        "dataset_quality_status": "ok",
        "best_predictor_key": "lgbm-v3",
        "best_predictor_name": "LightGBM v3",
        "recommended_docker_target": "ml-runtime:2026-05",
        "remote_storage_enabled": false,
        "detail": ""
      }
    ]
  },
  "cards": [
    { "key": "crawl_success_rate", "label": "수집 성공률", "value": 0.95, "unit": "ratio", "status": "healthy", "detail": "최근 30일" },
    { "key": "task_failure_rate", "label": "태스크 실패율", "value": 0.042, "unit": "ratio", "status": "healthy", "detail": "안정" }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/recent_limit 범위 위반 |

---

## GET /api/v1/analytics/decision-insights
영속된 입찰 결정(`BidDecisionRecord`) 신호를 요약한다 — 액션 분포, 제출 수, 워크로드 출처, 우선순위·기대마진·실행복잡도·경쟁도·예산포착 평균 점수(0~1 정규화). 결정 엔진 튜닝과 운영자 리뷰에 쓴다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |
| query | limit | integer (1~50) | 아니오 | recent_decisions 개수, 기본 10 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/decision-insights?days=30&limit=10"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 30,
  "result_count": 64,
  "high_priority_count": 18,
  "bid_now_count": 22,
  "review_count": 19,
  "skip_count": 23,
  "submitted_count": 27,
  "auto_workload_count": 40,
  "provided_workload_count": 24,
  "average_priority_score": 0.58,
  "average_expected_margin_score": 0.41,
  "average_execution_complexity_score": 0.37,
  "average_competitiveness_score": 0.62,
  "average_budget_capture_score": 0.71,
  "status_breakdown": { "planned": 14, "reviewing": 9, "submitted": 27, "skipped": 14 },
  "action_breakdown": { "bid_now": 22, "review": 19, "skip": 23 },
  "recent_decisions": [
    {
      "decision_record_id": 3310,
      "project_id": 4821,
      "action": "bid_now",
      "decision_status": "submitted",
      "priority_score": 0.82,
      "expected_margin_score": 0.55,
      "execution_complexity_score": 0.3,
      "competitiveness_score": 0.7,
      "budget_capture_score": 0.86,
      "updated_at": "2026-05-28T08:21:00Z"
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/limit 범위 위반 |

---

## GET /api/v1/analytics/decision-funnel
영속된 결정 레코드가 운영자 워크플로(진입 액션 → 제출/보류/스킵)를 따라 어떻게 진행되는지 퍼널로 요약하고, 직전 동일 기간과 비교하며 시계열·세그먼트 분해를 제공한다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |
| query | limit | integer (1~50) | 아니오 | recent_submissions 개수, 기본 10 |
| query | breakdown_limit | integer (1~20) | 아니오 | 세그먼트 분해 항목 수, 기본 5 |
| query | trend_bucket_days | integer (1~30) | 아니오 | 추세 버킷 크기(일), 기본 7 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/decision-funnel?days=30&breakdown_limit=5&trend_bucket_days=7"
```

**응답 200** (trend/breakdown 배열 일부 축약)
```json
{
  "operator_id": 1,
  "period_days": 30,
  "decision_count": 64,
  "project_count": 60,
  "active_pending_count": 23,
  "submitted_count": 27,
  "skipped_count": 14,
  "entry_bid_now_count": 22,
  "entry_review_count": 19,
  "entry_skip_count": 23,
  "direct_submitted_count": 15,
  "submitted_after_bid_now_count": 18,
  "submitted_after_review_count": 9,
  "submitted_after_skip_count": 0,
  "overall_submission_rate": 0.422,
  "workflow_submission_rate": 0.659,
  "bid_now_submission_rate": 0.818,
  "review_submission_rate": 0.474,
  "average_hours_to_submit": 18.6,
  "current_period_start": "2026-04-29T00:00:00Z",
  "current_period_end": "2026-05-29T00:00:00Z",
  "previous_period": {
    "period_start": "2026-03-30T00:00:00Z",
    "period_end": "2026-04-29T00:00:00Z",
    "decision_count": 58,
    "project_count": 55,
    "active_pending_count": 20,
    "submitted_count": 22,
    "skipped_count": 16,
    "entry_bid_now_count": 18,
    "entry_review_count": 17,
    "entry_skip_count": 23,
    "direct_submitted_count": 12,
    "submitted_after_bid_now_count": 14,
    "submitted_after_review_count": 8,
    "submitted_after_skip_count": 0,
    "overall_submission_rate": 0.379,
    "workflow_submission_rate": 0.629,
    "bid_now_submission_rate": 0.778,
    "review_submission_rate": 0.471,
    "average_hours_to_submit": 21.2
  },
  "comparison": {
    "current_period_start": "2026-04-29T00:00:00Z",
    "current_period_end": "2026-05-29T00:00:00Z",
    "previous_period_start": "2026-03-30T00:00:00Z",
    "previous_period_end": "2026-04-29T00:00:00Z",
    "decision_count_delta": 6,
    "project_count_delta": 5,
    "submitted_count_delta": 5,
    "active_pending_count_delta": 3,
    "skipped_count_delta": -2,
    "overall_submission_rate_delta": 0.043,
    "workflow_submission_rate_delta": 0.03,
    "bid_now_submission_rate_delta": 0.04,
    "review_submission_rate_delta": 0.003,
    "average_hours_to_submit_delta": -2.6
  },
  "trend_bucket_days": 7,
  "breakdown_limit_applied": 5,
  "trend": [
    {
      "bucket_start": "2026-05-01",
      "bucket_end": "2026-05-08",
      "decision_count": 16,
      "submitted_count": 7,
      "active_pending_count": 5,
      "skipped_count": 4,
      "entry_bid_now_count": 6,
      "entry_review_count": 5,
      "entry_skip_count": 5,
      "submitted_after_bid_now_count": 5,
      "submitted_after_review_count": 2,
      "submitted_after_skip_count": 0,
      "submission_rate": 0.438,
      "bid_now_submission_rate": 0.833,
      "review_submission_rate": 0.4,
      "average_priority_score": 0.59,
      "average_expected_margin_score": 0.43,
      "average_hours_to_submit": 17.9
    }
  ],
  "category_breakdown": [
    {
      "segment": "용역",
      "decision_count": 30,
      "project_count": 28,
      "submitted_count": 14,
      "active_pending_count": 10,
      "skipped_count": 6,
      "entry_bid_now_count": 12,
      "entry_review_count": 9,
      "entry_skip_count": 9,
      "submitted_after_bid_now_count": 10,
      "submitted_after_review_count": 4,
      "submitted_after_skip_count": 0,
      "submission_rate": 0.467,
      "bid_now_submission_rate": 0.833,
      "review_submission_rate": 0.444,
      "average_priority_score": 0.61,
      "average_expected_margin_score": 0.45,
      "average_hours_to_submit": 16.2
    }
  ],
  "workload_source_breakdown": [],
  "agency_breakdown": [],
  "recent_submissions": [
    {
      "decision_record_id": 3310,
      "project_id": 4821,
      "project_title": "OO청사 통합관제시스템 유지보수",
      "initial_action": "bid_now",
      "initial_decision_status": "planned",
      "current_action": "bid_now",
      "current_decision_status": "submitted",
      "priority_score": 0.82,
      "recommended_amount": 488500000,
      "first_decided_at": "2026-05-27T09:00:00Z",
      "submitted_at": "2026-05-28T08:21:00Z",
      "hours_to_submit": 23.35
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/limit/breakdown_limit/trend_bucket_days 범위 위반 |

---

## GET /api/v1/analytics/decision-recommendations
결정 퍼널 분석에서 도출한 실행 가능한 튜닝 권고와 실험 계획 후보를 반환한다. `experiments`/`recommended_next_experiment`는 곧바로 `POST /decision-experiments`로 등록 가능하다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |
| query | breakdown_limit | integer (1~20) | 아니오 | 세그먼트 분해 항목 수, 기본 5 |
| query | trend_bucket_days | integer (1~30) | 아니오 | 추세 버킷 크기(일), 기본 7 |
| query | recommendation_limit | integer (1~20) | 아니오 | 권고 항목 수, 기본 5 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/decision-recommendations?days=30&recommendation_limit=5"
```

**응답 200** (comparison 축약, 위 funnel과 동일 구조)
```json
{
  "operator_id": 1,
  "period_days": 30,
  "decision_count": 64,
  "submitted_count": 27,
  "active_pending_count": 23,
  "overall_submission_rate": 0.422,
  "workflow_submission_rate": 0.659,
  "bid_now_submission_rate": 0.818,
  "review_submission_rate": 0.474,
  "recommendation_count": 2,
  "recommendation_limit_applied": 5,
  "experiment_count": 1,
  "headline": "review 단계 전환율이 낮아 임계값 조정 실험을 권장합니다",
  "comparison": {
    "current_period_start": "2026-04-29T00:00:00Z",
    "current_period_end": "2026-05-29T00:00:00Z",
    "previous_period_start": "2026-03-30T00:00:00Z",
    "previous_period_end": "2026-04-29T00:00:00Z",
    "decision_count_delta": 6,
    "project_count_delta": 5,
    "submitted_count_delta": 5,
    "active_pending_count_delta": 3,
    "skipped_count_delta": -2,
    "overall_submission_rate_delta": 0.043,
    "workflow_submission_rate_delta": 0.03,
    "bid_now_submission_rate_delta": 0.04,
    "review_submission_rate_delta": 0.003,
    "average_hours_to_submit_delta": -2.6
  },
  "experiment_history": { "review_threshold_tuning": { "recent_run_count": 2, "success_count": 1 } },
  "recommended_next_experiment": {
    "experiment_key": "review_threshold_tuning",
    "recommendation_key": "low_review_submission_rate",
    "priority_rank": 1,
    "title": "review 임계값 하향 실험",
    "hypothesis": "review_threshold를 낮추면 review 진입 결정의 제출 전환율이 오른다",
    "suggested_change": "review_threshold 0.55 → 0.50",
    "target_metric": "review_submission_rate",
    "expected_direction": "increase",
    "success_criteria": "review_submission_rate +5%p 이상, 가드 지표 악화 없음",
    "guardrail_metric": "overall_submission_rate",
    "minimum_decision_sample": 30,
    "duration_days": 14,
    "rollback_trigger": "overall_submission_rate 3%p 이상 하락",
    "parameter_recommendation": { "review_threshold": 0.5 }
  },
  "experiments": [],
  "recommendations": [
    {
      "key": "low_review_submission_rate",
      "severity": "action",
      "title": "review 전환율 저조",
      "summary": "review 진입 결정의 제출 전환율이 47%로 기준 대비 낮습니다",
      "suggested_adjustment": "review_threshold 하향 검토",
      "supporting_metrics": { "review_submission_rate": 0.474 },
      "priority_score": 0.78,
      "history_adjustment": {
        "status": "promoted",
        "priority_delta": 0.1,
        "reason": "직전 유사 실험 1건 성공",
        "recent_run_count": 2,
        "success_count": 1,
        "rollback_count": 0,
        "failed_count": 0,
        "pending_count": 1,
        "applied_count": 1
      },
      "parameter_recommendation": { "review_threshold": 0.5 },
      "experiment_plan": {
        "experiment_key": "review_threshold_tuning",
        "recommendation_key": "low_review_submission_rate",
        "priority_rank": 1,
        "title": "review 임계값 하향 실험",
        "hypothesis": "review_threshold를 낮추면 제출 전환율이 오른다",
        "suggested_change": "review_threshold 0.55 → 0.50",
        "target_metric": "review_submission_rate",
        "expected_direction": "increase",
        "success_criteria": "review_submission_rate +5%p 이상",
        "guardrail_metric": "overall_submission_rate",
        "minimum_decision_sample": 30,
        "duration_days": 14,
        "rollback_trigger": "overall_submission_rate 3%p 이상 하락",
        "parameter_recommendation": { "review_threshold": 0.5 }
      }
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | days/breakdown_limit/trend_bucket_days/recommendation_limit 범위 위반 |

---

## POST /api/v1/analytics/decision-experiments
실험 계획 한 건을 영속화한다. 보통 `decision-recommendations`가 제안한 실험 계획을 그대로 등록해 실행 추적과 사후 평가의 기준선을 확보한다. 등록 시 현재 윈도우의 기준선 지표가 `baseline_summary`로 함께 스냅샷된다.

**파라미터** (request body `DecisionExperimentRunCreateRequest`)
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| body | experiment_key | string | 예 | 실험 식별 키 |
| body | recommendation_key | string | 예 | 출처 권고 키 |
| body | priority_rank | integer (1~20) | 예 | 우선순위 |
| body | title | string | 예 | 실험 제목 |
| body | hypothesis | string | 예 | 가설 |
| body | suggested_change | string | 예 | 제안 변경 |
| body | target_metric | string | 예 | 성공 지표 |
| body | expected_direction | enum(increase\|decrease\|stabilize) | 예 | 기대 방향 |
| body | success_criteria | string | 예 | 성공 기준 |
| body | guardrail_metric | string | 예 | 가드 지표 |
| body | minimum_decision_sample | integer (≥1) | 예 | 최소 결정 표본 |
| body | duration_days | integer (1~30) | 예 | 실험 기간 |
| body | rollback_trigger | string | 예 | 롤백 트리거 |
| body | parameter_recommendation | object | 아니오 | 파라미터 권고 |
| body | baseline_days | integer (1~90) | 아니오 | 기준선 기간, 기본 14 |
| body | started_at | datetime\|null | 아니오 | 시작 시각 |
| body | notes | string\|null | 아니오 | 메모 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/analytics/decision-experiments" \
  -H "Content-Type: application/json" \
  -d '{
    "experiment_key": "review_threshold_tuning",
    "recommendation_key": "low_review_submission_rate",
    "priority_rank": 1,
    "title": "review 임계값 하향 실험",
    "hypothesis": "review_threshold를 낮추면 제출 전환율이 오른다",
    "suggested_change": "review_threshold 0.55 → 0.50",
    "target_metric": "review_submission_rate",
    "expected_direction": "increase",
    "success_criteria": "review_submission_rate +5%p 이상",
    "guardrail_metric": "overall_submission_rate",
    "minimum_decision_sample": 30,
    "duration_days": 14,
    "rollback_trigger": "overall_submission_rate 3%p 이상 하락",
    "parameter_recommendation": { "review_threshold": 0.5 },
    "baseline_days": 14
  }'
```

**응답 200**
```json
{
  "run": {
    "id": 12,
    "operator_id": 1,
    "experiment_key": "review_threshold_tuning",
    "recommendation_key": "low_review_submission_rate",
    "status": "planned",
    "outcome": null,
    "priority_rank": 1,
    "title": "review 임계값 하향 실험",
    "hypothesis": "review_threshold를 낮추면 제출 전환율이 오른다",
    "suggested_change": "review_threshold 0.55 → 0.50",
    "target_metric": "review_submission_rate",
    "expected_direction": "increase",
    "success_criteria": "review_submission_rate +5%p 이상",
    "guardrail_metric": "overall_submission_rate",
    "minimum_decision_sample": 30,
    "duration_days": 14,
    "baseline_days": 14,
    "rollback_trigger": "overall_submission_rate 3%p 이상 하락",
    "notes": null,
    "started_at": "2026-05-29T00:00:00Z",
    "ended_at": null,
    "last_evaluated_at": null,
    "created_at": "2026-05-29T00:00:00Z",
    "updated_at": "2026-05-29T00:00:00Z",
    "latest_evaluation": null,
    "supported_apply_types": ["thresholds"],
    "applied_apply_types": [],
    "application_status": "not_ready",
    "application_detail": "성공 평가 후 적용 가능",
    "application_history": [],
    "review_bucket": "needs_evaluation",
    "review_priority": 80,
    "review_reason": "최소 표본 수집 후 평가 필요",
    "next_actions": [
      {
        "action": "evaluate",
        "label": "재평가 실행",
        "method": "POST",
        "path": "/api/v1/analytics/decision-experiments/12/evaluate",
        "enabled": true,
        "reason": "표본 수집 진행 중",
        "payload": {},
        "dry_run_supported": false,
        "force_supported": false
      }
    ]
  },
  "baseline_summary": {
    "window_start": "2026-05-15T00:00:00Z",
    "window_end": "2026-05-29T00:00:00Z",
    "decision_count": 30,
    "submitted_count": 13,
    "active_pending_count": 9,
    "overall_submission_rate": 0.433,
    "workflow_submission_rate": 0.65,
    "bid_now_submission_rate": 0.8,
    "review_submission_rate": 0.471,
    "auto_submission_rate": 0.4,
    "provided_submission_rate": 0.5,
    "best_category": "용역",
    "best_category_submission_rate": 0.52,
    "worst_category": "물품",
    "worst_category_submission_rate": 0.31
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | 필수 필드 누락, 범위(priority_rank 1~20, duration_days 1~30 등) 위반 |

---

## GET /api/v1/analytics/decision-experiments
최근 실험 런 목록을 상태별 집계 카운트와 함께 반환한다. 실험 현황 대시보드(주의 필요 항목 우선 정렬)에서 쓴다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| query | limit | integer (1~100) | 아니오 | 반환 런 수, 기본 20 |
| query | status | string\|null | 아니오 | 런 라이프사이클 필터(함수 인자 run_status의 alias) |
| query | outcome | string\|null | 아니오 | 평가 결과 필터 |
| query | application_status | string\|null | 아니오 | 적용 상태 필터 |
| query | sort | string | 아니오 | 정렬: needs_attention(기본)\|created_desc\|created_asc\|priority\|last_evaluated_desc\|application |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/decision-experiments?limit=20&status=running&sort=needs_attention"
```

**응답 200** (runs는 위 상세의 run 구조와 동일, 축약)
```json
{
  "operator_id": 1,
  "result_count": 1,
  "total_match_count": 1,
  "sort": "needs_attention",
  "active_count": 1,
  "completed_count": 0,
  "rolled_back_count": 0,
  "failed_count": 0,
  "success_count": 0,
  "pending_count": 1,
  "inconclusive_count": 0,
  "rollback_count": 0,
  "applicable_count": 0,
  "ready_to_apply_count": 0,
  "applied_count": 0,
  "partially_applied_count": 0,
  "blocked_count": 0,
  "not_ready_count": 1,
  "not_supported_count": 0,
  "application_status_counts": { "not_ready": 1 },
  "outcome_counts": {},
  "review_bucket_counts": { "needs_evaluation": 1 },
  "runs": [
    {
      "id": 12,
      "operator_id": 1,
      "experiment_key": "review_threshold_tuning",
      "recommendation_key": "low_review_submission_rate",
      "status": "running",
      "outcome": null,
      "priority_rank": 1,
      "title": "review 임계값 하향 실험",
      "hypothesis": "review_threshold를 낮추면 제출 전환율이 오른다",
      "suggested_change": "review_threshold 0.55 → 0.50",
      "target_metric": "review_submission_rate",
      "expected_direction": "increase",
      "success_criteria": "review_submission_rate +5%p 이상",
      "guardrail_metric": "overall_submission_rate",
      "minimum_decision_sample": 30,
      "duration_days": 14,
      "baseline_days": 14,
      "rollback_trigger": "overall_submission_rate 3%p 이상 하락",
      "notes": null,
      "started_at": "2026-05-29T00:00:00Z",
      "ended_at": null,
      "last_evaluated_at": null,
      "created_at": "2026-05-29T00:00:00Z",
      "updated_at": "2026-05-29T00:00:00Z",
      "latest_evaluation": null,
      "supported_apply_types": ["thresholds"],
      "applied_apply_types": [],
      "application_status": "not_ready",
      "application_detail": "성공 평가 후 적용 가능",
      "application_history": [],
      "review_bucket": "needs_evaluation",
      "review_priority": 80,
      "review_reason": "최소 표본 수집 후 평가 필요",
      "next_actions": []
    }
  ]
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | limit 범위(1~100) 위반 |

---

## GET /api/v1/analytics/decision-experiments/{experiment_run_id}
실험 런 한 건의 상세를 기준선 스냅샷·최신 평가와 함께 반환한다. 실험 상세 화면에서 쓴다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 실험 런 ID |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/decision-experiments/12"
```

**응답 200**: `POST /decision-experiments`의 200 응답과 동일한 `DecisionExperimentRunDetailResponse` 구조(`run` + `baseline_summary`). 평가가 한 번이라도 실행됐다면 `run.latest_evaluation`이 채워진다.
```json
{
  "run": {
    "id": 12,
    "status": "running",
    "outcome": "watch",
    "last_evaluated_at": "2026-05-29T01:00:00Z",
    "latest_evaluation": {
      "evaluated_at": "2026-05-29T01:00:00Z",
      "sample_size": 22,
      "minimum_sample_reached": false,
      "target_metric": "review_submission_rate",
      "baseline_target_value": 0.471,
      "current_target_value": 0.5,
      "target_delta": 0.029,
      "guardrail_metric": "overall_submission_rate",
      "baseline_guardrail_value": 0.433,
      "current_guardrail_value": 0.44,
      "guardrail_delta": 0.007,
      "outcome": "watch",
      "recommended_action": "collect_more_data",
      "summary": "표본 부족 — 추세는 긍정적",
      "current_summary": {
        "window_start": "2026-05-22T00:00:00Z",
        "window_end": "2026-05-29T00:00:00Z",
        "decision_count": 22,
        "submitted_count": 10,
        "active_pending_count": 7,
        "overall_submission_rate": 0.44,
        "workflow_submission_rate": 0.66,
        "bid_now_submission_rate": 0.81,
        "review_submission_rate": 0.5,
        "auto_submission_rate": 0.41,
        "provided_submission_rate": 0.52,
        "best_category": "용역",
        "best_category_submission_rate": 0.55,
        "worst_category": "물품",
        "worst_category_submission_rate": 0.33
      }
    }
  },
  "baseline_summary": {
    "window_start": "2026-05-15T00:00:00Z",
    "window_end": "2026-05-29T00:00:00Z",
    "decision_count": 30,
    "submitted_count": 13,
    "active_pending_count": 9,
    "overall_submission_rate": 0.433,
    "workflow_submission_rate": 0.65,
    "bid_now_submission_rate": 0.8,
    "review_submission_rate": 0.471,
    "auto_submission_rate": 0.4,
    "provided_submission_rate": 0.5,
    "best_category": "용역",
    "best_category_submission_rate": 0.52,
    "worst_category": "물품",
    "worst_category_submission_rate": 0.31
  }
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 실험 런 없음 |
| 400 | 기타 도메인 검증 오류 |
| 422 | experiment_run_id 타입 오류 |

> 참고: 404/400은 런타임 분기로 OpenAPI 스펙에는 명시되지 않음(FastAPI HTTPException 자동 미문서화).

---

## PATCH /api/v1/analytics/decision-experiments/{experiment_run_id}
실험 런의 메모나 라이프사이클 상태를 수동으로 갱신한다. 운영자가 실험을 수동으로 완료/롤백 처리하거나 노트를 남길 때 쓴다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 실험 런 ID |
| body | status | enum(planned\|running\|completed\|rolled_back)\|null | 아니오 | 라이프사이클 상태(수동 전환은 failed 제외) |
| body | outcome | enum(insufficient_data\|watch\|success\|rollback\|inconclusive)\|null | 아니오 | 평가 결과 |
| body | replace_notes | string\|null | 아니오 | 노트 교체 |
| body | append_note | string\|null | 아니오 | 노트 추가 |
| body | ended_at | datetime\|null | 아니오 | 종료 시각 |

**요청 예시**
```bash
curl -X PATCH "http://localhost:3000/api/v1/analytics/decision-experiments/12" \
  -H "Content-Type: application/json" \
  -d '{"status": "completed", "outcome": "success", "append_note": "표본 충분, 목표 달성", "ended_at": "2026-06-12T00:00:00Z"}'
```

**응답 200**: `DecisionExperimentRunDetailResponse` (갱신된 `run` + `baseline_summary`). 구조는 위 상세와 동일.

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 실험 런 없음 |
| 400 | 무효한 상태 전환 등 도메인 오류 |
| 422 | 바디 검증 오류 |

---

## POST /api/v1/analytics/decision-experiments/{experiment_run_id}/evaluate
실험 재평가를 API 요청 안에서 동기 실행하지 않고 비동기 작업(Celery)으로 큐에 넣는다. 먼저 런 존재를 확인한 뒤 큐잉하고 태스크 상태를 반환한다. `poll_url`로 진행 상태를 폴링한다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 실험 런 ID |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/analytics/decision-experiments/12/evaluate"
```

**응답 202**
```json
{
  "task_id": "f1e2d3c4-0000-4a5b-8c9d-abcdef012345",
  "task_name": "reevaluate_decision_experiment",
  "queue": "ml",
  "status": "queued",
  "detail": "재평가 작업이 큐에 등록되었습니다",
  "poll_url": "/api/v1/ml/reevaluations/decision-experiments/tasks/f1e2d3c4-0000-4a5b-8c9d-abcdef012345"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 실험 런 없음 |
| 400 | 기타 도메인 오류 |
| 422 | experiment_run_id 타입 오류 |

---

## POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-thresholds
성공한 실험이 제안한 임계값(bid_now/review threshold) 조정을 운영자 전략에 실제로 반영한다. 기본은 `outcome=success` 실험에만 적용 가능하다. `dry_run=true`면 변경 없이 시뮬레이션, `force=true`면 성공 미달이어도 강제 적용한다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 실험 런 ID |
| body | dry_run | boolean | 아니오 | true면 시뮬레이션만, 기본 false |
| body | force | boolean | 아니오 | true면 성공 미달이어도 강제 적용, 기본 false |
| body | append_note | string\|null | 아니오 | 적용 메모 추가 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/analytics/decision-experiments/12/apply-thresholds" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": false, "force": false, "append_note": "성공 실험 임계값 반영"}'
```

**응답 200**
```json
{
  "operator_id": 1,
  "run_id": 12,
  "experiment_key": "review_threshold_tuning",
  "recommendation_key": "low_review_submission_rate",
  "applied": true,
  "dry_run": false,
  "latest_outcome": "success",
  "threshold_updates": [
    {
      "parameter": "review_threshold",
      "label": "review 임계값",
      "direction": "decrease",
      "previous_value": 0.55,
      "suggested_value": 0.5,
      "delta": -0.05,
      "rationale": "review 진입 전환율 개선이 검증됨"
    }
  ],
  "strategy_thresholds": {
    "bid_now_threshold": 0.75,
    "review_threshold": 0.5
  },
  "detail": "임계값 조정이 운영자 전략에 반영되었습니다"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 실험 런 없음 |
| 400 | 임계값 매핑 미지원 / 이미 적용됨 / 성공 결과 아님(force=false) |
| 422 | 바디 검증 오류 |

---

## POST /api/v1/analytics/decision-experiments/{experiment_run_id}/apply-strategy
성공한 실험이 제안한 워크로드/카테고리 튜닝(auto_workload_penalty_multiplier, category_priority_overrides)을 운영자 전략에 반영한다. apply-thresholds와 동일한 게이트(성공 실험 한정, `dry_run`/`force` 동작 동일)를 따른다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | experiment_run_id | integer | 예 | 실험 런 ID |
| body | dry_run | boolean | 아니오 | true면 시뮬레이션만, 기본 false |
| body | force | boolean | 아니오 | true면 성공 미달이어도 강제 적용, 기본 false |
| body | append_note | string\|null | 아니오 | 적용 메모 추가 |

**요청 예시**
```bash
curl -X POST "http://localhost:3000/api/v1/analytics/decision-experiments/13/apply-strategy" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true}'
```

**응답 200** (dry_run 예시 — applied=false)
```json
{
  "operator_id": 1,
  "run_id": 13,
  "experiment_key": "workload_penalty_tuning",
  "recommendation_key": "high_auto_workload_skip_rate",
  "applied": false,
  "dry_run": true,
  "latest_outcome": "success",
  "strategy_updates": [
    {
      "parameter": "auto_workload_penalty_multiplier",
      "label": "자동 워크로드 패널티 배수",
      "direction": "decrease",
      "previous_value": 1.2,
      "suggested_value": 1.0,
      "delta": -0.2,
      "rationale": "자동 워크로드 결정의 스킵률이 과도하게 높음"
    },
    {
      "parameter": "category_priority_overrides",
      "label": "카테고리 우선순위 오버라이드",
      "direction": "replace",
      "previous_value": { "물품": 0.8 },
      "suggested_value": { "물품": 0.9, "용역": 1.1 },
      "delta": null,
      "rationale": "용역 카테고리 전환율이 높아 우선순위 상향"
    }
  ],
  "strategy_tuning": {
    "auto_workload_penalty_multiplier": 1.0,
    "category_priority_overrides": { "물품": 0.9, "용역": 1.1 }
  },
  "detail": "dry-run: 적용 시 위 조정이 반영됩니다"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 404 | 해당 ID의 실험 런 없음 |
| 400 | 전략 매핑 미지원 / 이미 적용됨 / 성공 결과 아님(force=false) |
| 422 | 바디 검증 오류 |

---

## GET /api/v1/analytics/user-stats/{user_id}
> **Deprecated**. 신규 코드는 [`GET /api/v1/analytics/operator-stats`](#get-apiv1analyticsoperator-stats)를 사용하세요.

구버전(다중 사용자 시절) 호환용 alias. 단일 운영자 통계와 동일한 데이터를 반환하되 입력 `user_id`를 `requested_user_id`에 echo한다. `user_id`가 무엇이든 통계는 항상 canonical 운영자 기준으로 계산된다.

**파라미터**
| 위치 | 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|---|
| path | user_id | integer | 예 | (레거시) 요청 사용자 ID, 결과의 requested_user_id에만 반영 |
| query | days | integer (1~365) | 아니오 | 집계 기간(일), 기본 30 |

**요청 예시**
```bash
curl "http://localhost:3000/api/v1/analytics/user-stats/1?days=30"
```

**응답 200**
```json
{
  "operator_id": 1,
  "period_days": 30,
  "total_bids": 41,
  "total_events": 512,
  "bids_count": 318,
  "requested_user_id": 1,
  "mode": "single_operator"
}
```

**에러**
| 코드 | 의미 |
|---|---|
| 422 | user_id/days 타입·범위 오류 |
