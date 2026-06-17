"""Synthetic experiment persistence and run-lifecycle orchestration.

Owns the "가상 회사 낙찰 실험실" (Experiment Lab) domain: saving experiment
definitions, triggering asynchronous runs, and persisting per-operator results.
ML/predictor logic is untouched here -- the underlying backtest engine is invoked
only through :class:`SyntheticBacktestService`, whose ``win_rate_on_settled`` is a
price-only estimate (NOT actual award) and is passed through unchanged.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.models import (
    SyntheticExperiment,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
)
from app.services.synthetic_backtest import SyntheticBacktestService

logger = logging.getLogger(__name__)

RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
SYNTHETIC_OPERATOR_SAMPLE_TARGET = 30
SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET = 100
SAMPLE_STATUS_SUFFICIENT = "sufficient"
SAMPLE_STATUS_INSUFFICIENT = "insufficient_sample"

_G1_PRESET_WINDOW = {
    "start_at": "2025-01-01T00:00:00+00:00",
    "end_at": "2025-12-31T23:59:59+00:00",
    "limit": 200,
    "scenario": "base",
    "settle_actions": False,
}

SYNTHETIC_EXPERIMENT_PRESETS: dict[str, dict[str, Any]] = {
    "g1-construction-base-12m": {
        "description": "G-1 공사 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "construction"},
        "operator_slugs": [
            "cn-small-gangwon",
            "cn-mid-gyeonggi",
            "cn-electric-telecom-national",
        ],
    },
    "g1-service-base-12m": {
        "description": "G-1 일반/기술 용역 2025년 12개월 base preset",
        # Mixes general-service and technical-service operators. Leave category
        # unset so each operator's focus categories scope its award pool.
        "params": {**_G1_PRESET_WINDOW, "category": None},
        "operator_slugs": [
            "eng-supervision-busan",
            "eng-design-daejeon",
            "gs-cleaning-metro",
            "gs-security-national",
        ],
    },
    "g1-goods-base-12m": {
        "description": "G-1 물품 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "goods"},
        "operator_slugs": [
            "gd-office-sme",
            "gd-it-equipment-midcap",
        ],
    },
    "g1-software-base-12m": {
        "description": "G-1 소프트웨어 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "software"},
        "operator_slugs": [
            "sw-small-seoul",
            "sw-mid-metro",
            "sw-large-national",
        ],
    },
}

# Budget-band boundaries (KRW). A settlement is placed into the first band whose
# upper bound it is *strictly* below; the final band catches everything at or
# above the last boundary. Mirrors common 나라장터 budget tiers (1억/5억/10억/50억).
BUDGET_BAND_BOUNDARIES: tuple[tuple[str, float], ...] = (
    ("lt_1eok", 100_000_000.0),
    ("1eok_5eok", 500_000_000.0),
    ("5eok_10eok", 1_000_000_000.0),
    ("10eok_50eok", 5_000_000_000.0),
)
BUDGET_BAND_TOP_KEY = "gte_50eok"

# A settlement counts as a (price-only estimated) "win" when its price-only
# verdict is "plausible" -- IDENTICAL to ``would_have_won_price_only_count`` in
# the engine summary. This is a price-based ESTIMATE, not an actual award.
_WIN_VERDICTS = {"plausible"}

# Eligibility-gate (PR3) final verdict that counts as an estimated favorable win:
# the bid cleared the 낙찰하한가 AND landed at/under the realized winning amount.
# ``unknown`` (no 예가/하한 data) is EXCLUDED from the eligibility-rate denominator
# so the rate stays honest -- it is computed only over settlements we could judge.
_ELIGIBLE_FAVORABLE_VERDICT = "eligible_favorable"
_ELIGIBILITY_UNKNOWN_VERDICT = "unknown"

# CSV export columns (Phase 4). ``operator_slug`` is prepended to the CLI
# comparison columns (see ``scripts.backtest_synthetic_operators._write_comparison_csv``)
# so each row is self-identifying. ``win_rate_on_settled`` /
# ``win_rate_on_candidates`` remain PRICE-ONLY estimates (NOT actual awards); the
# column name is preserved unchanged so downstream readers keep that meaning.
EXPORT_CSV_COLUMNS: tuple[str, ...] = (
    "operator_slug",
    "business_type",
    "candidate_count",
    "paper_bid_count",
    "settled_count",
    "skipped_by_strategy_count",
    "would_have_won_price_only_count",
    "win_rate_on_settled",
    "win_rate_on_candidates",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
    "average_absolute_amount_error_rate",
)

# Per-operator metric keys surfaced in the A/B compare response. ``win_rate_on_settled``
# is a price-only estimate (NOT an actual award) and is carried through unchanged.
_COMPARE_METRIC_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "settled_count",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)

# Subset of compare metrics for which a signed delta (b - a) is computed. A delta
# is ``None`` when either side is missing/None (e.g. no settled rows -> win_rate None).
_COMPARE_DELTA_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)


def _budget_band_key(budget: float) -> str:
    for key, upper in BUDGET_BAND_BOUNDARIES:
        if budget < upper:
            return key
    return BUDGET_BAND_TOP_KEY


def _is_price_only_win(item: dict[str, Any]) -> bool:
    """Whether a settlement item is a price-only estimated win.

    Accepts both the rich engine settlement (``would_have_won_price_only`` string
    verdict) and the sliced dashboard item (``would_have_won`` bool) so the
    breakdown is correct regardless of which shape is fed in.
    """
    verdict = item.get("would_have_won_price_only")
    if verdict is not None:
        return str(verdict) in _WIN_VERDICTS
    return bool(item.get("would_have_won"))


def _empty_breakdown() -> dict[str, list[dict[str, Any]]]:
    return {"by_category": [], "by_budget_band": []}


def _latest_result_time(items: list[dict[str, Any]]) -> str | None:
    """Most recent award time (안내일/개찰일) in the group, ISO string or None.

    Surfaces per-group data freshness so a stale category is visible alongside
    its (possibly thin) sample. Items without a ``result_time`` are ignored.
    """
    times = [
        str(entry["result_time"])
        for entry in items
        if entry.get("result_time") not in (None, "")
    ]
    return max(times) if times else None


def _aggregate_group(items: list[dict[str, Any]]) -> dict[str, Any]:
    settled_count = len(items)
    would_have_won_count = sum(1 for entry in items if _is_price_only_win(entry))
    errors = [
        float(entry["absolute_bid_rate_error"])
        for entry in items
        if entry.get("absolute_bid_rate_error") is not None
    ]
    avg_error = round(sum(errors) / len(errors), 6) if errors else None
    # Price-only "close" rate. ``win_rate`` is kept (unchanged) for existing
    # consumers; ``est_price_close_rate`` is the SAME value under an honest name
    # ("가격 근접 추정율", NOT an actual award).
    est_price_close_rate = (
        round(would_have_won_count / settled_count, 6) if settled_count else None
    )
    # Eligibility-gate (PR3) estimate: favorable count over the JUDGEABLE
    # denominator (settled minus ``unknown``). ``unknown`` settlements (no 예가/
    # 낙찰하한 data) are excluded so the rate is not diluted by un-scoreable rows.
    eligible_favorable_count = sum(
        1
        for entry in items
        if str(entry.get("would_have_won_final")) == _ELIGIBLE_FAVORABLE_VERDICT
    )
    eligibility_unknown_count = sum(
        1
        for entry in items
        if str(entry.get("would_have_won_final")) == _ELIGIBILITY_UNKNOWN_VERDICT
    )
    eligibility_judged_count = settled_count - eligibility_unknown_count
    eligible_favorable_rate = (
        round(eligible_favorable_count / eligibility_judged_count, 6)
        if eligibility_judged_count > 0
        else None
    )
    return {
        "settled_count": settled_count,
        "would_have_won_count": would_have_won_count,
        # Price-only estimate (legacy key kept for frontend lockstep).
        "win_rate": est_price_close_rate,
        # Honest-named alias of the same price-only estimate.
        "est_price_close_rate": est_price_close_rate,
        # Eligibility-gate estimate (unknown excluded from denominator).
        "eligible_favorable_count": eligible_favorable_count,
        "eligibility_unknown_count": eligibility_unknown_count,
        "eligibility_judged_count": eligibility_judged_count,
        "eligible_favorable_rate": eligible_favorable_rate,
        "avg_abs_bid_rate_error": avg_error,
        # Per-group health: latest award time (freshness). ``settled_count`` above
        # already doubles as the sample-size health signal.
        "latest_result_time": _latest_result_time(items),
    }


def compute_breakdown(
    settlement_items: Optional[list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """Group per-operator settlements into category / budget-band breakdowns.

    Each group carries TWO honest, separately-named rates:

    * ``win_rate`` / ``est_price_close_rate`` -- the SAME value:
      ``would_have_won_count / settled_count`` where a "win" is the price-only
      estimate (``would_have_won_price_only == "plausible"``). NOT an actual
      award; ``None`` when the group has no settled items. ``win_rate`` is the
      legacy key kept for frontend lockstep; ``est_price_close_rate`` is its
      honest-named alias.
    * ``eligible_favorable_rate`` -- the PR3 eligibility-gate estimate:
      ``eligible_favorable_count / eligibility_judged_count`` where the
      denominator EXCLUDES ``unknown`` settlements (no 예가/낙찰하한 data), so the
      rate is computed only over judgeable rows. ``None`` when nothing is
      judgeable.

    Each group also carries health fields: ``settled_count`` (sample size) and
    ``latest_result_time`` (freshness of the newest award in the group).
    """
    if not settlement_items:
        return _empty_breakdown()

    by_category: dict[str, list[dict[str, Any]]] = {}
    by_band: dict[str, list[dict[str, Any]]] = {}
    for item in settlement_items:
        category = item.get("category")
        category_key = str(category) if category not in (None, "") else "unknown"
        by_category.setdefault(category_key, []).append(item)

        budget = float(item.get("budget_estimate") or 0.0)
        by_band.setdefault(_budget_band_key(budget), []).append(item)

    category_rows = [
        {"category": key, **_aggregate_group(items)}
        for key, items in sorted(by_category.items())
    ]
    # Preserve the canonical band ordering (ascending budget).
    band_order = [key for key, _ in BUDGET_BAND_BOUNDARIES] + [BUDGET_BAND_TOP_KEY]
    band_rows = [
        {"budget_band": key, **_aggregate_group(by_band[key])}
        for key in band_order
        if key in by_band
    ]
    return {"by_category": category_rows, "by_budget_band": band_rows}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


def _json_loads(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        logger.warning("Failed to decode stored synthetic-experiment JSON payload")
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def sample_status_for_settled_count(settled_count: int) -> dict[str, Any]:
    """Return the fixed G-1 per-operator sample health payload."""
    count = max(0, int(settled_count or 0))
    missing = max(0, SYNTHETIC_OPERATOR_SAMPLE_TARGET - count)
    return {
        "sample_status": (
            SAMPLE_STATUS_SUFFICIENT
            if missing == 0
            else SAMPLE_STATUS_INSUFFICIENT
        ),
        "sample_target": SYNTHETIC_OPERATOR_SAMPLE_TARGET,
        "settled_count": count,
        "missing_settled_count": missing,
    }


def aggregate_sample_status(operator_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize G-1 run-level sample health across operator result rows."""
    total_settled = sum(int(item.get("settled_count") or 0) for item in operator_results)
    insufficient = [
        {
            "operator_slug": str(item.get("slug") or item.get("operator_slug") or "unknown"),
            **sample_status_for_settled_count(int(item.get("settled_count") or 0)),
        }
        for item in operator_results
        if int(item.get("settled_count") or 0) < SYNTHETIC_OPERATOR_SAMPLE_TARGET
    ]
    missing_total = max(0, SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET - total_settled)
    return {
        "sample_status": (
            SAMPLE_STATUS_SUFFICIENT
            if missing_total == 0 and not insufficient
            else SAMPLE_STATUS_INSUFFICIENT
        ),
        "operator_sample_target": SYNTHETIC_OPERATOR_SAMPLE_TARGET,
        "run_total_sample_target": SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET,
        "total_settled_count": total_settled,
        "missing_total_settled_count": missing_total,
        "insufficient_operators": insufficient,
    }


class SyntheticExperimentService:
    """CRUD + run lifecycle for synthetic experiments."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- experiment CRUD -------------------------------------------------------

    def create_experiment(
        self,
        *,
        name: str,
        description: Optional[str],
        params: dict[str, Any],
        operator_slugs: Optional[list[str]],
    ) -> SyntheticExperiment:
        experiment = SyntheticExperiment(
            name=name,
            description=description,
            params_json=_json_dumps(params),
            operator_slugs_json=_json_dumps(operator_slugs or []),
        )
        self.db.add(experiment)
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def list_experiments(self) -> list[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .order_by(
                SyntheticExperiment.created_at.desc(), SyntheticExperiment.id.desc()
            )
            .all()
        )

    def list_presets(self) -> list[dict[str, Any]]:
        """Return fixed G-1 presets with their saved experiment/run state."""
        return [
            self._serialize_preset(name, definition)
            for name, definition in SYNTHETIC_EXPERIMENT_PRESETS.items()
        ]

    def ensure_preset(self, name: str) -> Optional[SyntheticExperiment]:
        """Create or update the saved experiment for a fixed G-1 preset."""
        definition = SYNTHETIC_EXPERIMENT_PRESETS.get(name)
        if definition is None:
            return None
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        if experiment is None:
            experiment = SyntheticExperiment(name=name)
            self.db.add(experiment)
        experiment.description = str(definition["description"])
        experiment.params_json = _json_dumps(definition["params"])
        experiment.operator_slugs_json = _json_dumps(definition["operator_slugs"])
        self.db.commit()
        self.db.refresh(experiment)
        return experiment

    def _serialize_preset(
        self, name: str, definition: dict[str, Any]
    ) -> dict[str, Any]:
        experiment = (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.name == name)
            .first()
        )
        latest_run = experiment.runs[0] if experiment and experiment.runs else None
        return {
            "name": name,
            "description": str(definition["description"]),
            "params": definition["params"],
            "operator_slugs": list(definition["operator_slugs"]),
            "experiment_id": experiment.id if experiment else None,
            "latest_run_id": latest_run.id if latest_run else None,
            "latest_run_status": latest_run.status if latest_run else None,
        }

    def get_experiment(self, experiment_id: int) -> Optional[SyntheticExperiment]:
        return (
            self.db.query(SyntheticExperiment)
            .filter(SyntheticExperiment.id == experiment_id)
            .first()
        )

    # --- run lifecycle ---------------------------------------------------------

    def create_run(self, experiment: SyntheticExperiment) -> SyntheticExperimentRun:
        """Create a queued run and enqueue the Celery backtest task.

        The Celery payload carries ``experiment_id`` + ``run_id`` so the task can
        persist the run/result lifecycle, plus the saved params and slug subset.
        """
        from app.tasks.jobs import enqueue_synthetic_operator_backtest

        run = SyntheticExperimentRun(
            experiment_id=experiment.id,
            status=RUN_STATUS_QUEUED,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)

        params = _json_loads(experiment.params_json) or {}
        operator_slugs = _json_loads(experiment.operator_slugs_json) or []
        payload: dict[str, Any] = dict(params)
        payload["experiment_id"] = experiment.id
        payload["run_id"] = run.id
        payload["slugs"] = operator_slugs or None

        async_result = enqueue_synthetic_operator_backtest(payload=payload)
        run.task_id = getattr(async_result, "id", None)
        self.db.commit()
        self.db.refresh(run)
        return run

    def get_run(
        self, experiment_id: int, run_id: int
    ) -> Optional[SyntheticExperimentRun]:
        run = (
            self.db.query(SyntheticExperimentRun)
            .filter(
                SyntheticExperimentRun.id == run_id,
                SyntheticExperimentRun.experiment_id == experiment_id,
            )
            .first()
        )
        if run is None:
            return None
        if run.status in (RUN_STATUS_QUEUED, RUN_STATUS_RUNNING) and run.task_id:
            self._sync_run_with_task(run)
        return run

    def _sync_run_with_task(self, run: SyntheticExperimentRun) -> None:
        """Reconcile a still-pending run with the Celery task state.

        The task itself persists completion/failure to the DB; this only covers a
        task that failed without the lifecycle hook recording it (defensive).
        """
        from app.tasks.jobs import get_synthetic_backtest_task_status

        try:
            status_payload = get_synthetic_backtest_task_status(run.task_id)
        except Exception:  # pragma: no cover - defensive
            logger.warning("Could not read task status for run %s", run.id)
            return
        if (status_payload.get("status") == "failed") and run.status not in (
            RUN_STATUS_COMPLETED,
            RUN_STATUS_FAILED,
        ):
            self.mark_failed(run.id, status_payload.get("error") or "Task failed")
            self.db.refresh(run)

    # --- lifecycle hooks invoked from inside the Celery task -------------------

    def mark_running(self, run_id: int) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_RUNNING
        run.started_at = datetime.utcnow()
        self.db.commit()

    def mark_completed(self, run_id: int, result: dict[str, Any]) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        operator_results = result.get("results", []) or []
        summary = {
            "operator_count": result.get("operator_count", len(operator_results)),
            "scenario": result.get("scenario"),
            "category": result.get("category"),
            "start_at": result.get("start_at"),
            "end_at": result.get("end_at"),
            "limit": result.get("limit"),
            **aggregate_sample_status(operator_results),
        }
        run.status = RUN_STATUS_COMPLETED
        run.finished_at = datetime.utcnow()
        run.error = None
        run.summary_json = _json_dumps(summary)

        excluded_metric_keys = {"settlement_items", "breakdown"}
        for item in operator_results:
            metrics = {
                key: value
                for key, value in item.items()
                if key not in excluded_metric_keys
            }
            metrics.update(
                sample_status_for_settled_count(int(item.get("settled_count") or 0))
            )
            settlement_sample = item.get("settlement_items")
            # Prefer the engine-supplied breakdown (computed over the full,
            # non-truncated settlement set). Fall back to computing from the
            # sampled ``settlement_items`` for stubbed/legacy payloads.
            breakdown = item.get("breakdown")
            if breakdown is None:
                breakdown = compute_breakdown(settlement_sample)
            self.db.add(
                SyntheticExperimentResult(
                    run_id=run.id,
                    operator_slug=str(item.get("slug") or "unknown"),
                    metrics_json=_json_dumps(metrics),
                    settlement_sample_json=(
                        _json_dumps(settlement_sample)
                        if settlement_sample is not None
                        else None
                    ),
                    breakdown_json=_json_dumps(breakdown),
                )
            )
        self.db.commit()

    def mark_failed(self, run_id: int, error: str) -> None:
        run = self._fetch_run(run_id)
        if run is None:
            return
        run.status = RUN_STATUS_FAILED
        run.finished_at = datetime.utcnow()
        run.error = error
        self.db.commit()

    def _fetch_run(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    # --- serialization (ORM -> response dicts) --------------------------------

    def serialize_experiment(self, experiment: SyntheticExperiment) -> dict[str, Any]:
        return {
            "id": experiment.id,
            "name": experiment.name,
            "description": experiment.description,
            "params": _json_loads(experiment.params_json) or {},
            "operator_slugs": _json_loads(experiment.operator_slugs_json) or [],
            "created_at": experiment.created_at,
            "updated_at": experiment.updated_at,
            "runs": [self.serialize_run_summary(run) for run in experiment.runs],
        }

    def serialize_run_summary(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "status": run.status,
            "task_id": run.task_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "error": run.error,
            "summary": _json_loads(run.summary_json),
            "created_at": run.created_at,
        }

    def serialize_run_detail(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        payload = self.serialize_run_summary(run)
        payload["results"] = [
            {
                **sample_status_for_settled_count(
                    int((_json_loads(item.metrics_json) or {}).get("settled_count") or 0)
                ),
                "operator_slug": item.operator_slug,
                "metrics": _json_loads(item.metrics_json) or {},
                "settlement_sample": _json_loads(item.settlement_sample_json),
                "breakdown": _json_loads(item.breakdown_json) or _empty_breakdown(),
            }
            for item in run.results
        ]
        return payload

    # --- Phase 4: CSV export ---------------------------------------------------

    def export_run_csv(self, run: SyntheticExperimentRun) -> str:
        """Render a run's per-operator metrics as a CSV document (string).

        Columns mirror the CLI comparison CSV (``EXPORT_CSV_COLUMNS``) with
        ``operator_slug`` prepended. Missing metric keys serialize to an empty
        cell. A run with no results yields a header-only CSV (still HTTP 200), so
        not-yet-completed runs export cleanly rather than erroring. ``win_rate_*``
        columns stay price-only estimates -- the engine values pass through
        unchanged.
        """
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=list(EXPORT_CSV_COLUMNS), extrasaction="ignore"
        )
        writer.writeheader()
        for item in run.results:
            metrics = _json_loads(item.metrics_json) or {}
            row: dict[str, Any] = {}
            for column in EXPORT_CSV_COLUMNS:
                if column == "operator_slug":
                    value: Any = item.operator_slug
                else:
                    value = metrics.get(column)
                row[column] = "" if value is None else value
            writer.writerow(row)
        return buffer.getvalue()

    # --- Phase 4: A/B run comparison -------------------------------------------

    def _fetch_run_by_id(self, run_id: int) -> Optional[SyntheticExperimentRun]:
        """Fetch a run by id alone (cross-experiment; for A/B comparison)."""
        return (
            self.db.query(SyntheticExperimentRun)
            .filter(SyntheticExperimentRun.id == run_id)
            .first()
        )

    def _run_compact_header(self, run: SyntheticExperimentRun) -> dict[str, Any]:
        """Minimal run header (id + experiment_id + summary) for the compare payload."""
        return {
            "id": run.id,
            "experiment_id": run.experiment_id,
            "summary": _json_loads(run.summary_json),
        }

    @staticmethod
    def _compare_side_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
        """Extract the compare metric subset from a stored per-operator metrics dict."""
        return {key: metrics.get(key) for key in _COMPARE_METRIC_KEYS}

    @staticmethod
    def _compare_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        """Signed (b - a) deltas; ``None`` when either operand is missing/None."""
        delta: dict[str, Any] = {}
        for key in _COMPARE_DELTA_KEYS:
            a_value = a.get(key)
            b_value = b.get(key)
            if a_value is None or b_value is None:
                delta[key] = None
            else:
                delta[key] = round(float(b_value) - float(a_value), 6)
        return delta

    def compare_runs(self, run_a_id: int, run_b_id: int) -> Optional[dict[str, Any]]:
        """Join two runs' per-operator metrics by ``operator_slug`` and diff them.

        Returns ``None`` when either run is missing (router maps to 404). The two
        runs may belong to different experiments -- the join is purely on the
        operator-slug intersection. ``delta`` is ``b - a`` (positive => B higher);
        ``win_rate_*`` deltas are ``None`` when either side has no settled rows.
        """
        run_a = self._fetch_run_by_id(run_a_id)
        run_b = self._fetch_run_by_id(run_b_id)
        if run_a is None or run_b is None:
            return None

        metrics_a = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_a.results
        }
        metrics_b = {
            item.operator_slug: (_json_loads(item.metrics_json) or {})
            for item in run_b.results
        }

        shared = sorted(set(metrics_a) & set(metrics_b))
        operators = []
        for slug in shared:
            side_a = self._compare_side_metrics(metrics_a[slug])
            side_b = self._compare_side_metrics(metrics_b[slug])
            operators.append(
                {
                    "operator_slug": slug,
                    "a": side_a,
                    "b": side_b,
                    "delta": self._compare_delta(side_a, side_b),
                }
            )

        only_in_a = sorted(set(metrics_a) - set(metrics_b))
        only_in_b = sorted(set(metrics_b) - set(metrics_a))
        return {
            "run_a": self._run_compact_header(run_a),
            "run_b": self._run_compact_header(run_b),
            "operators": operators,
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
        }


def run_experiment_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute an experiment-scoped backtest inside a fresh DB session.

    Invoked by the Celery task. Opens its own ``SessionLocal`` so it is safe in
    both eager (memory://) and worker execution. Persists the run/result
    lifecycle when ``run_id`` is present in the payload.
    """
    from app.core.database import SessionLocal

    db = SessionLocal()
    run_id = payload.get("run_id")
    service = SyntheticExperimentService(db)
    try:
        if run_id is not None:
            service.mark_running(run_id)

        backtest = SyntheticBacktestService()
        result = backtest.run_for_all(
            db,
            start_at=_parse_dt(payload.get("start_at")),
            end_at=_parse_dt(payload.get("end_at")),
            category=payload.get("category"),
            limit=int(payload.get("limit") or 100),
            scenario=str(payload.get("scenario") or "base"),
            slugs=payload.get("slugs"),
        )
        if run_id is not None:
            service.mark_completed(run_id, result)
        return result
    except Exception as exc:
        if run_id is not None:
            service.mark_failed(run_id, str(exc))
        raise
    finally:
        db.close()
