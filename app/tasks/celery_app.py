"""Celery app skeleton."""

from uuid import uuid4

try:
    from celery import Celery
    from celery.schedules import crontab
except ImportError:  # pragma: no cover - exercised in lightweight test environments
    class _FallbackAsyncResult:
        """In-memory async result object for environments without Celery."""

        def __init__(self, task_id: str, state: str = "PENDING", result=None):
            self.id = task_id
            self.state = state
            self.status = state
            self.result = result

        def ready(self) -> bool:
            return self.state in {"SUCCESS", "FAILURE", "REVOKED"}

        def successful(self) -> bool:
            return self.state == "SUCCESS"


    class _FallbackTask:
        """Minimal callable task wrapper used when Celery is unavailable."""

        def __init__(self, app, func, name: str | None = None):
            self.app = app
            self.run = func
            self.__name__ = getattr(func, "__name__", "task")
            self.name = name or self.__name__

        def __call__(self, *args, **kwargs):
            return self.run(*args, **kwargs)

        def delay(self, *args, **kwargs):
            return self.apply_async(args=args, kwargs=kwargs)

        def apply_async(self, args=None, kwargs=None, **options):
            del options
            task_id = str(uuid4())
            args = tuple(args or ())
            kwargs = dict(kwargs or {})
            try:
                result = self.run(*args, **kwargs)
                async_result = _FallbackAsyncResult(task_id, state="SUCCESS", result=result)
            except Exception as exc:  # pragma: no cover - exercised only on task failure
                async_result = _FallbackAsyncResult(task_id, state="FAILURE", result=exc)
            self.app._results[task_id] = async_result
            return async_result


    class Celery:  # type: ignore[override]
        """Very small Celery-compatible shim for tests without the dependency installed."""

        def __init__(self, *args, **kwargs):
            del args, kwargs
            self.conf = self
            self._results: dict[str, _FallbackAsyncResult] = {}

        def update(self, **kwargs):
            del kwargs

        def task(self, name: str | None = None, **options):
            del options

            def decorator(func):
                return _FallbackTask(self, func, name=name)

            return decorator

        def AsyncResult(self, task_id: str):
            return self._results.get(task_id, _FallbackAsyncResult(task_id))

    class crontab:  # type: ignore[no-redef]
        """Minimal crontab shim for environments without Celery installed.

        Stores ``day_of_week`` when supplied so weekly schedules remain
        introspectable in lightweight test environments. The real Celery
        ``crontab`` handles the actual scheduling semantics in production.
        """

        def __init__(self, hour: int = 0, minute: int = 0, day_of_week=None, **kwargs):
            del kwargs
            self.hour = {int(hour)}
            self.minute = {int(minute)}
            self.day_of_week = {int(day_of_week)} if day_of_week is not None else set()

from app.core.config import settings
from app.core.constants import PRICE_SCENARIOS
from app.tasks.pipeline_schedules import (
    INFERENCE_OUTBOX_PROCESS_TASK_NAME as INFERENCE_OUTBOX_PROCESS_TASK_NAME,
    NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME as NOTIFICATION_DELIVERY_OUTBOX_TASK_NAME,
    SIMILARITY_PROJECTION_BACKFILL_TASK_NAME as SIMILARITY_PROJECTION_BACKFILL_TASK_NAME,
    build_pipeline_beat_schedule,
    build_pipeline_task_routes,
)

OPERATOR_STRATEGY_MONITOR_TASK_NAME = "jobs.monitor_operator_strategy"
PAPER_BIDDING_FORWARD_TASK_NAME = "jobs.run_forward_paper_bidding"
FORWARD_SETTLEMENT_TASK_NAME = "jobs.settle_forward_paper_bids"
HISTORICAL_BACKTEST_TASK_NAME = "jobs.run_historical_backtest"
COLLECT_KONEPS_NOTICES_TASK_NAME = "jobs.collect_koneps_notices"
BACKFILL_SCSBID_RESERVE_DETAIL_TASK_NAME = "jobs.backfill_scsbid_reserve_detail"
PROJECT_EMBEDDING_REBUILD_TASK_NAME = "jobs.rebuild_project_embeddings"
PRICE_PREDICTOR_TRAINING_TASK_NAME = "ml.train_price_predictor"
DECISION_EXPERIMENT_REEVALUATION_TASK_NAME = "ml.reevaluate_decision_experiment"
SYNTHETIC_BACKTEST_RUN_TASK_NAME = "jobs.run_synthetic_operator_backtest"
ENRICH_BUSINESS_TYPE_TASK_NAME = "jobs.enrich_pending_business_types"
RECLASSIFY_CATEGORIES_TASK_NAME = "jobs.reclassify_pending_categories"
SMOKE_TEST_TASK_NAME = "jobs.run_koneps_telegram_smoke_test"
G2_CANDIDATE_RECHECK_TASK_NAME = "jobs.run_g2_candidate_recheck"
COLLECT_G2_EVIDENCE_TASK_NAME = "jobs.collect_g2_evidence"
TELEGRAM_POLLING_TASK_NAME = "jobs.poll_telegram_updates"
RECONCILE_STALE_TASK_RUNS_TASK_NAME = "jobs.reconcile_stale_task_runs"
NOTIFY_AWARD_RESULTS_TASK_NAME = "jobs.notify_award_results"
PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME = "jobs.recompute_preview_snapshot"
RUNTIME_PERFORMANCE_PROBE_TASK_NAME = "jobs.runtime_performance_probe"


def build_task_routes() -> dict[str, dict[str, str]]:
    """Route long-running domains onto dedicated queues."""
    return {
        COLLECT_KONEPS_NOTICES_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        BACKFILL_SCSBID_RESERVE_DETAIL_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        "jobs.send_telegram_notification": {"queue": settings.CELERY_OPS_QUEUE},
        TELEGRAM_POLLING_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        OPERATOR_STRATEGY_MONITOR_TASK_NAME: {"queue": settings.CELERY_ML_INFERENCE_QUEUE},
        PAPER_BIDDING_FORWARD_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        FORWARD_SETTLEMENT_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        HISTORICAL_BACKTEST_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        SYNTHETIC_BACKTEST_RUN_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        ENRICH_BUSINESS_TYPE_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        SMOKE_TEST_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        G2_CANDIDATE_RECHECK_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        COLLECT_G2_EVIDENCE_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        NOTIFY_AWARD_RESULTS_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        RECONCILE_STALE_TASK_RUNS_TASK_NAME: {"queue": settings.CELERY_OPS_QUEUE},
        PREVIEW_SNAPSHOT_RECOMPUTE_TASK_NAME: {"queue": settings.CELERY_ML_INFERENCE_QUEUE},
        **build_pipeline_task_routes(),
        PROJECT_EMBEDDING_REBUILD_TASK_NAME: {"queue": settings.CELERY_ML_BACKFILL_QUEUE},
        PRICE_PREDICTOR_TRAINING_TASK_NAME: {"queue": settings.CELERY_ML_TRAINING_QUEUE},
        DECISION_EXPERIMENT_REEVALUATION_TASK_NAME: {"queue": settings.CELERY_ML_REEVALUATION_QUEUE},
    }


def _normalize_task_time_limits(*, soft_seconds: int, hard_seconds: int) -> tuple[int | None, int | None]:
    """Convert configured task limits into Celery-friendly soft/hard values."""
    hard_limit = hard_seconds if hard_seconds > 0 else None
    soft_limit = soft_seconds if soft_seconds > 0 else None

    if hard_limit is not None and soft_limit is not None and soft_limit >= hard_limit:
        soft_limit = max(1, hard_limit - 1)

    return soft_limit, hard_limit


def build_operator_strategy_monitor_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for operator strategy monitoring."""
    if not settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_ENABLED:
        return {}

    return {
        "operator_strategy_monitor_periodic": {
            "task": OPERATOR_STRATEGY_MONITOR_TASK_NAME,
            "schedule": float(max(1, settings.OPERATOR_STRATEGY_MONITOR_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "limit": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_LIMIT,
                    "high_priority_only": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_HIGH_PRIORITY_ONLY,
                    "max_active_bids": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MAX_ACTIVE_BIDS,
                    "current_workload_score": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_CURRENT_WORKLOAD_SCORE,
                    "same_category_only": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SAME_CATEGORY_ONLY,
                    "similar_limit": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_SIMILAR_LIMIT,
                    "min_similarity": settings.OPERATOR_STRATEGY_MONITOR_SCHEDULE_MIN_SIMILARITY,
                },
                "trigger_source": "scheduled",
            },
        }
    }


def build_paper_bidding_forward_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for forward paper-bidding."""
    if not settings.PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED:
        return {}

    category = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY or "").strip() or None
    scenario = str(settings.PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO or "base").strip() or "base"
    scenario = scenario if scenario in PRICE_SCENARIOS else "base"

    return {
        "paper_bidding_forward_periodic": {
            "task": PAPER_BIDDING_FORWARD_TASK_NAME,
            "schedule": float(max(1, settings.PAPER_BIDDING_FORWARD_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "category": category,
                    "limit": max(1, settings.PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT),
                    "scenario": scenario,
                    "strategy_version": "scheduled-forward-paper",
                    "model_version": "current",
                    "history_limit": max(1, settings.PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT),
                    "persist": settings.PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST,
                },
            },
        }
    }


def build_forward_settlement_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for forward paper-bid settlement.

    Forward paper bids are generated without a settlement; this schedule sweeps
    unsettled forward paper bids whose deadline has passed and whose tender result
    is now available, then creates the matching ``PaperBidSettlement`` rows.
    Defaults to OFF; opt-in via ``FORWARD_SETTLEMENT_SCHEDULE_ENABLED``.
    """
    if not settings.FORWARD_SETTLEMENT_SCHEDULE_ENABLED:
        return {}

    return {
        "forward_settlement_periodic": {
            "task": FORWARD_SETTLEMENT_TASK_NAME,
            "schedule": float(
                max(1, settings.FORWARD_SETTLEMENT_INTERVAL_MINUTES) * 60
            ),
            "kwargs": {
                "limit": max(1, int(settings.FORWARD_SETTLEMENT_LIMIT)),
                "persist": bool(settings.FORWARD_SETTLEMENT_PERSIST),
            },
        }
    }


def build_award_result_notify_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the award-result Telegram alarm.

    Sweeps tracked real bids (``BidDecisionRecord.submitted_bid_amount`` set,
    ``award_notified_at IS NULL``) whose 개찰 result is now public and sends the
    operator one factual 적격/경쟁력 summary per bid (idempotent via
    ``award_notified_at``). Runs on an interval so freshly-published winners are
    picked up after the scsbid 6h collection. Defaults to OFF; opt-in via
    ``AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED``.
    """
    if not settings.AWARD_RESULT_NOTIFY_SCHEDULE_ENABLED:
        return {}

    return {
        "award_result_notify_periodic": {
            "task": NOTIFY_AWARD_RESULTS_TASK_NAME,
            "schedule": float(
                max(1, settings.AWARD_RESULT_NOTIFY_INTERVAL_MINUTES) * 60
            ),
            "kwargs": {
                "limit": max(1, int(settings.AWARD_RESULT_NOTIFY_LIMIT)),
            },
        }
    }


def build_historical_backtest_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for historical (settled) paper-bidding."""
    if not settings.HISTORICAL_BACKTEST_SCHEDULE_ENABLED:
        return {}

    category = str(settings.HISTORICAL_BACKTEST_SCHEDULE_CATEGORY or "").strip() or None
    scenario = str(settings.HISTORICAL_BACKTEST_SCHEDULE_SCENARIO or "base").strip() or "base"
    scenario = scenario if scenario in PRICE_SCENARIOS else "base"

    return {
        "historical_backtest_periodic": {
            "task": HISTORICAL_BACKTEST_TASK_NAME,
            "schedule": float(max(1, settings.HISTORICAL_BACKTEST_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "request_payload": {
                    "category": category,
                    "limit": max(1, int(settings.HISTORICAL_BACKTEST_SCHEDULE_LIMIT)),
                    "scenario": scenario,
                    "lookback_days": max(1, int(settings.HISTORICAL_BACKTEST_LOOKBACK_DAYS)),
                    "history_limit": max(1, int(settings.HISTORICAL_BACKTEST_SCHEDULE_HISTORY_LIMIT)),
                    "cutoff_hours_before_deadline": max(0, int(settings.HISTORICAL_BACKTEST_SCHEDULE_CUTOFF_HOURS)),
                    "settle_actions": str(settings.HISTORICAL_BACKTEST_SCHEDULE_SETTLE_ACTIONS or "bid_now,review"),
                    "strategy_version": "scheduled-historical-backtest",
                    "model_version": "current",
                    "persist": bool(settings.HISTORICAL_BACKTEST_SCHEDULE_PERSIST),
                },
            },
        }
    }


def build_koneps_collection_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entries for KONEPS notice collection.

    If KONEPS_COLLECTION_CATEGORIES is set, creates one entry per category.
    Falls back to the single KONEPS_COLLECTION_CATEGORY for backward compatibility.
    """
    if not settings.KONEPS_COLLECTION_SCHEDULE_ENABLED:
        return {}

    execution_mode = str(settings.KONEPS_COLLECTION_EXECUTION_MODE or "auto").strip() or "auto"
    if execution_mode not in {"mock", "live", "auto"}:
        execution_mode = "auto"

    max_items = min(500, max(1, int(settings.KONEPS_COLLECTION_MAX_ITEMS)))
    interval = float(max(1, settings.KONEPS_COLLECTION_INTERVAL_MINUTES) * 60)
    source = str(settings.KONEPS_COLLECTION_SOURCE or "koneps-openapi").strip() or "koneps-openapi"

    # multi-category support
    raw_categories = str(settings.KONEPS_COLLECTION_CATEGORIES or "").strip()
    if raw_categories:
        categories = [t.strip().lower() for t in raw_categories.split(",") if t.strip()]
    else:
        single = str(settings.KONEPS_COLLECTION_CATEGORY or "").strip() or None
        categories = [single] if single else [None]

    result: dict[str, dict[str, object]] = {}
    for cat in categories:
        slug = cat or "general"
        key = f"koneps_collection_{slug}_periodic"
        result[key] = {
            "task": COLLECT_KONEPS_NOTICES_TASK_NAME,
            "schedule": interval,
            "kwargs": {
                "request_payload": {
                    "source": source,
                    "category": cat,
                    "execution_mode": execution_mode,
                    "max_items": max_items,
                },
            },
        }
    return result


def build_scsbid_collection_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for KONEPS scsbid (open-bid result) collection.

    This is an *independent* beat schedule that re-uses ``collect_koneps_notices`` but
    drives the collector to the scsbid OpenAPI branch via ``request_payload.source``.
    Defaults to OFF; opt-in via ``KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED`` in .env.
    """
    if not settings.KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED:
        return {}

    category = str(settings.KONEPS_SCSBID_COLLECTION_CATEGORY or "").strip() or None
    execution_mode = (
        str(settings.KONEPS_SCSBID_COLLECTION_EXECUTION_MODE or "auto").strip() or "auto"
    )
    if execution_mode not in {"mock", "live", "auto"}:
        execution_mode = "auto"

    categories = [
        token.strip().lower()
        for token in str(settings.KONEPS_SCSBID_COLLECTION_CATEGORIES or "").split(",")
        if token.strip()
    ] or None

    return {
        "koneps_scsbid_collection_periodic": {
            "task": COLLECT_KONEPS_NOTICES_TASK_NAME,
            "schedule": float(
                max(1, settings.KONEPS_SCSBID_COLLECTION_INTERVAL_MINUTES) * 60
            ),
            "kwargs": {
                "request_payload": {
                    "source": str(
                        settings.KONEPS_SCSBID_COLLECTION_SOURCE or "scsbid-openapi"
                    ).strip()
                    or "scsbid-openapi",
                    "category": category,
                    "categories": categories,
                    "execution_mode": execution_mode,
                    # max_items 는 싣지 않는다: sweep 상한은
                    # ``KONEPS_SCSBID_COLLECTION_MAX_ITEMS`` 를 sweep 이 직접 읽는다.
                    # 요청으로 실으면 스키마 상한에 눌린 값이 sweep 을 자른다(회귀).
                    "lookback_days": max(
                        1, int(settings.KONEPS_SCSBID_COLLECTION_LOOKBACK_DAYS)
                    ),
                    "page_size": max(
                        1,
                        min(999, int(settings.KONEPS_SCSBID_COLLECTION_PAGE_SIZE)),
                    ),
                    "max_pages": max(
                        1, int(settings.KONEPS_SCSBID_COLLECTION_MAX_PAGES)
                    ),
                    "collect_reserve_detail": bool(
                        settings.KONEPS_SCSBID_COLLECTION_RESERVE_DETAIL
                    ),
                },
            },
        }
    }


def build_business_type_enrichment_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for business_type enrichment."""
    if not settings.BUSINESS_TYPE_ENRICHMENT_SCHEDULE_ENABLED:
        return {}

    return {
        "business_type_enrichment_periodic": {
            "task": ENRICH_BUSINESS_TYPE_TASK_NAME,
            "schedule": float(max(1, settings.BUSINESS_TYPE_ENRICHMENT_INTERVAL_MINUTES) * 60),
            "kwargs": {
                "limit": max(1, int(settings.BUSINESS_TYPE_ENRICHMENT_BATCH_LIMIT)),
            },
        }
    }


def build_category_reclassify_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for SBERT prototype category reclassification."""
    if not settings.CATEGORY_RECLASSIFY_SCHEDULE_ENABLED:
        return {}

    return {
        "category_reclassify_periodic": {
            "task": RECLASSIFY_CATEGORIES_TASK_NAME,
            "schedule": float(max(1, settings.CATEGORY_RECLASSIFY_INTERVAL_MINUTES) * 60),
            "kwargs": {"limit": max(1, int(settings.CATEGORY_RECLASSIFY_BATCH_LIMIT))},
        }
    }


def build_smoke_test_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the daily KONEPS+Telegram smoke test."""
    if not settings.SMOKE_TEST_SCHEDULE_ENABLED:
        return {}
    hour = max(0, min(23, int(settings.SMOKE_TEST_HOUR_KST)))
    minute = max(0, min(59, int(settings.SMOKE_TEST_MINUTE)))
    return {
        "smoke_test_daily": {
            "task": SMOKE_TEST_TASK_NAME,
            "schedule": crontab(hour=hour, minute=minute),
        }
    }


def build_g2_candidate_recheck_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the daily G-2 candidate re-check.

    Read-only observation tool: the task re-runs ``preview_candidates`` for every
    synthetic operator to measure when niche biddable inventory recovers and
    candidates reappear. It never executes the heavy strategy monitor and writes
    nothing to operator data (only a single analytics evidence event). Defaults
    to OFF; opt-in via ``G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED``.
    """
    if not settings.G2_CANDIDATE_RECHECK_SCHEDULE_ENABLED:
        return {}
    hour = max(0, min(23, int(settings.G2_CANDIDATE_RECHECK_HOUR_KST)))
    minute = max(0, min(59, int(settings.G2_CANDIDATE_RECHECK_MINUTE)))
    return {
        "g2_candidate_recheck_daily": {
            "task": G2_CANDIDATE_RECHECK_TASK_NAME,
            "schedule": crontab(hour=hour, minute=minute),
        }
    }


def build_collect_g2_evidence_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the daily G-2 evidence snapshot.

    Read-only observation tool: the task snapshots the per-operator G-2 evidence
    ledger (via ``AnalyticsReportingService.build_g2_evidence_summary``) into a
    single ``collect_g2_evidence`` analytics event so ``counted_days`` can
    accumulate toward the G-2 exit review. It never runs monitors, writes nothing
    to operator data, and sends no notifications. Defaults to OFF; opt-in via
    ``COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED``.
    """
    if not settings.COLLECT_G2_EVIDENCE_SCHEDULE_ENABLED:
        return {}
    hour = max(0, min(23, int(settings.COLLECT_G2_EVIDENCE_HOUR_KST)))
    minute = max(0, min(59, int(settings.COLLECT_G2_EVIDENCE_MINUTE)))
    return {
        "collect_g2_evidence_daily": {
            "task": COLLECT_G2_EVIDENCE_TASK_NAME,
            "schedule": crontab(hour=hour, minute=minute),
            "kwargs": {
                "window_days": max(1, int(settings.COLLECT_G2_EVIDENCE_WINDOW_DAYS)),
                "recent_limit": max(1, int(settings.COLLECT_G2_EVIDENCE_RECENT_LIMIT)),
            },
        }
    }


def build_price_predictor_training_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the weekly schedule entry for price-predictor ML training.

    The ``ml.train_price_predictor`` task is routed onto the dedicated ML
    training queue (see :func:`build_task_routes`), so the ``training-worker``
    compose service executes it while the ``beat`` service only schedules it.
    Defaults to OFF; opt-in via ``PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED``.

    The scheduled runs are **candidate-only**: they pass ``create_manifest=False``
    and ``publish_remote=False`` so the weekly jobs only retrain predictor
    artifacts + dataset/quality/comparison reports (most recent 500 settled
    rows, the task default ``limit``). The global run is kept, and
    ``PRICE_PREDICTOR_TRAINING_SCHEDULE_CATEGORIES`` can add category-scoped
    runs (default: construction/service/goods) so each business group gets a
    fresh candidate artifact. Scheduled jobs never create a release manifest,
    upload to remote storage, or activate a model — release/promotion stays a
    deliberate, gated, manual action (``scripts/promote_ml_release.py``).
    """
    if not settings.PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED:
        return {}

    day_of_week = max(
        0, min(6, int(settings.PRICE_PREDICTOR_TRAINING_SCHEDULE_DAY_OF_WEEK))
    )
    hour = max(0, min(23, int(settings.PRICE_PREDICTOR_TRAINING_SCHEDULE_HOUR_KST)))
    minute = max(0, min(59, int(settings.PRICE_PREDICTOR_TRAINING_SCHEDULE_MINUTE)))

    schedule = {
        "price_predictor_training_weekly": {
            "task": PRICE_PREDICTOR_TRAINING_TASK_NAME,
            "schedule": crontab(day_of_week=day_of_week, hour=hour, minute=minute),
            # Candidate-only: never auto-create/publish/activate a release.
            "kwargs": {
                "request_payload": {
                    "create_manifest": False,
                    "publish_remote": False,
                }
            },
        }
    }
    categories = [
        token.strip().lower()
        for token in str(
            settings.PRICE_PREDICTOR_TRAINING_SCHEDULE_CATEGORIES or ""
        ).split(",")
        if token.strip()
    ]
    for category in dict.fromkeys(categories):
        safe_key = "".join(ch if ch.isalnum() else "_" for ch in category).strip("_")
        if not safe_key:
            continue
        schedule[f"price_predictor_training_weekly_{safe_key}"] = {
            "task": PRICE_PREDICTOR_TRAINING_TASK_NAME,
            "schedule": crontab(day_of_week=day_of_week, hour=hour, minute=minute),
            # Candidate-only: never auto-create/publish/activate a release.
            "kwargs": {
                "request_payload": {
                    "category": category,
                    "create_manifest": False,
                    "publish_remote": False,
                }
            },
        }
    return schedule


def build_telegram_polling_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for Telegram getUpdates polling."""
    if not settings.TELEGRAM_POLLING_SCHEDULE_ENABLED:
        return {}

    return {
        "telegram_polling_periodic": {
            "task": TELEGRAM_POLLING_TASK_NAME,
            "schedule": float(
                max(5, int(settings.TELEGRAM_POLLING_INTERVAL_SECONDS))
            ),
            "kwargs": {
                "limit": max(1, int(settings.TELEGRAM_POLLING_LIMIT)),
                "timeout_seconds": max(
                    0,
                    int(settings.TELEGRAM_POLLING_TIMEOUT_SECONDS),
                ),
            },
        }
    }


def build_stale_task_reconciler_beat_schedule() -> dict[str, dict[str, object]]:
    """Build the periodic schedule entry for the stale task-run reconciler.

    Durable backstop for orphaned non-terminal ``operator_strategy_runs`` /
    ``crawl_jobs`` rows left behind by a hard-kill / worker-restart / DB-down
    event (the cases the in-task finalize-on-failure path cannot cover because no
    Python ``finally`` runs on SIGKILL). Without this, one orphaned ``running``
    row trips the operations dashboard ``task_stale_queue: critical`` KPI forever.

    Safe idempotent janitor: only flips already-stale non-terminal rows to
    ``failed`` (never deletes rows or partial results), so it is recommended ON.
    Defaults to OFF to honor the codebase opt-in-via-.env schedule convention.
    """
    if not settings.STALE_TASK_RECONCILER_SCHEDULE_ENABLED:
        return {}

    return {
        "stale_task_reconciler_periodic": {
            "task": RECONCILE_STALE_TASK_RUNS_TASK_NAME,
            "schedule": float(
                max(1, settings.STALE_TASK_RECONCILER_INTERVAL_MINUTES) * 60
            ),
        }
    }


def build_celery_runtime_config() -> dict[str, object]:
    """Build the shared Celery runtime configuration for eager and worker-backed modes."""
    soft_time_limit, hard_time_limit = _normalize_task_time_limits(
        soft_seconds=settings.CELERY_TASK_SOFT_TIME_LIMIT_SECONDS,
        hard_seconds=settings.CELERY_TASK_TIME_LIMIT_SECONDS,
    )
    eager_mode = settings.uses_in_memory_celery

    config: dict[str, object] = {
        "imports": ("app.tasks.jobs", "app.tasks.inference_jobs", "app.tasks.performance_probe", "app.tasks.pipeline_delivery_jobs"),
        "task_serializer": "json",
        "accept_content": ["json"],
        "result_serializer": "json",
        "timezone": "Asia/Seoul",
        "enable_utc": False,
        "task_default_queue": settings.CELERY_TASK_DEFAULT_QUEUE,
        "task_routes": build_task_routes(),
        "task_always_eager": eager_mode,
        "task_store_eager_result": True,
        "task_ignore_result": False,
        "task_track_started": settings.CELERY_TASK_TRACK_STARTED,
        "result_expires": settings.CELERY_RESULT_EXPIRES_SECONDS,
        "worker_send_task_events": settings.CELERY_WORKER_SEND_TASK_EVENTS,
        "task_send_sent_event": settings.CELERY_TASK_SEND_SENT_EVENT,
        "worker_concurrency": settings.CELERY_WORKER_CONCURRENCY,
        "worker_prefetch_multiplier": settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
        "worker_max_tasks_per_child": settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,
        "broker_connection_retry_on_startup": settings.CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP,
        "broker_connection_max_retries": settings.CELERY_BROKER_CONNECTION_MAX_RETRIES,
        "broker_transport_options": {
            "max_retries": settings.CELERY_BROKER_PUBLISH_MAX_RETRIES,
        },
        "beat_schedule": {
            **build_operator_strategy_monitor_beat_schedule(),
            **build_paper_bidding_forward_beat_schedule(),
            **build_forward_settlement_beat_schedule(),
            **build_award_result_notify_beat_schedule(),
            **build_historical_backtest_beat_schedule(),
            **build_koneps_collection_beat_schedule(),
            **build_scsbid_collection_beat_schedule(),
            **build_business_type_enrichment_beat_schedule(),
            **build_category_reclassify_beat_schedule(),
            **build_smoke_test_beat_schedule(),
            **build_g2_candidate_recheck_beat_schedule(),
            **build_collect_g2_evidence_beat_schedule(),
            **build_price_predictor_training_beat_schedule(),
            **build_telegram_polling_beat_schedule(),
            **build_stale_task_reconciler_beat_schedule(),
            **build_pipeline_beat_schedule(),
        },
        # Resolved lazily by celery from the string path to avoid a circular
        # import; auto-recovers from a corrupt shelve/dbm schedule db instead
        # of crash-looping beat. See app/tasks/beat_scheduler.py.
        "beat_scheduler": "app.tasks.beat_scheduler:SelfHealingScheduler",
    }

    if soft_time_limit is not None:
        config["task_soft_time_limit"] = soft_time_limit
    if hard_time_limit is not None:
        config["task_time_limit"] = hard_time_limit

    if settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB > 0:
        config["worker_max_memory_per_child"] = int(
            settings.CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB
        )

    if eager_mode:
        config.update(
            task_acks_late=False,
            task_acks_on_failure_or_timeout=True,
        )
    else:
        config.update(
            task_acks_late=True,
            task_acks_on_failure_or_timeout=True,
            worker_cancel_long_running_tasks_on_connection_loss=True,
        )

    return config


def apply_task_result_repr_maxsize(app: Celery) -> None:
    """Apply the configured success-result log cap; non-positive keeps default."""
    maxsize = int(settings.CELERY_TASK_RESULT_REPR_MAXSIZE)
    task_base = getattr(app, "Task", None)
    if maxsize > 0 and task_base is not None:
        task_base.resultrepr_maxsize = maxsize


celery_app = Celery(
    "bid_vector",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(**build_celery_runtime_config())
apply_task_result_repr_maxsize(celery_app)
