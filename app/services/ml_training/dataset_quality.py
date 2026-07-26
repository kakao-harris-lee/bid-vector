"""Dataset-quality gating for the price-predictor training service.

Scores whether a training dataset is deep/diverse enough for release-gated
training and emits the auditable quality report. Threshold constants live in
:mod:`.constants`; the check bodies are moved verbatim from the original module.
"""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any

from app.core.config import settings
from app.core.time import utc_now

from .constants import (
    _QUALITY_AGENCY_DIVERSITY_TARGET,
    _QUALITY_BID_RATE_VARIANCE_MAX,
    _QUALITY_LINKED_RESULT_COVERAGE_MIN,
    _QUALITY_PROJECT_DIVERSITY_TARGET,
    _QUALITY_RESERVE_PATTERN_COVERAGE_MIN,
    _QUALITY_SCORE_BASE,
    _QUALITY_SCORE_BLOCKING_PENALTY,
    _QUALITY_SCORE_WARNING_PENALTY,
)


class DatasetQualityMixin:
    """Dataset-quality report + normalized check appender."""

    def _build_dataset_quality_report(
        self,
        *,
        release_tag: str,
        category: str | None,
        agency_name: str | None,
        limit: int,
        bid_rates: list[float],
        dataset: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate whether the dataset is strong enough for release-gated training."""
        summary = dataset.get("summary") if isinstance(dataset.get("summary"), dict) else {}
        sample_count = int(summary.get("sample_count") or 0)
        project_count = int(summary.get("project_count") or 0)
        agency_count = int(summary.get("agency_count") or 0)
        linked_result_count = int(summary.get("linked_result_count") or 0)
        reserve_pattern_sample_count = int(summary.get("reserve_pattern_sample_count") or 0)
        linked_result_coverage = self._safe_ratio(linked_result_count, sample_count)
        reserve_pattern_coverage = self._safe_ratio(reserve_pattern_sample_count, sample_count)
        std_bid_rate = pstdev(bid_rates) if len(bid_rates) > 1 else 0.0
        min_training_samples = max(1, int(settings.PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES or 1))
        min_gate_samples = max(1, int(settings.ML_RELEASE_PREDICTOR_GATE_MIN_SAMPLE_COUNT or 1))
        configured_holdout_size = max(1, int(settings.PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE or 1), min_gate_samples)
        required_samples = min_training_samples + configured_holdout_size

        checks: list[dict[str, Any]] = []
        self._append_quality_check(
            checks,
            name="sample_depth",
            passed=sample_count >= required_samples,
            severity="blocking",
            value=sample_count,
            threshold=required_samples,
            detail="Dataset needs enough rows for both training prefix and release-gate holdout samples.",
        )
        self._append_quality_check(
            checks,
            name="project_diversity",
            passed=project_count >= min(_QUALITY_PROJECT_DIVERSITY_TARGET, max(1, sample_count)),
            severity="warning",
            value=project_count,
            threshold=min(_QUALITY_PROJECT_DIVERSITY_TARGET, max(1, sample_count)),
            detail="Dataset should include more than one project before release comparison is trusted.",
        )
        self._append_quality_check(
            checks,
            name="agency_diversity",
            passed=bool(agency_name) or agency_count >= min(_QUALITY_AGENCY_DIVERSITY_TARGET, max(1, sample_count)),
            severity="warning",
            value=agency_count,
            threshold=min(_QUALITY_AGENCY_DIVERSITY_TARGET, max(1, sample_count)),
            detail="Global training should include multiple agencies; agency-scoped runs are exempt.",
        )
        self._append_quality_check(
            checks,
            name="linked_result_coverage",
            passed=linked_result_coverage >= _QUALITY_LINKED_RESULT_COVERAGE_MIN,
            severity="warning",
            value=round(linked_result_coverage, 4),
            threshold=_QUALITY_LINKED_RESULT_COVERAGE_MIN,
            detail="Linked tender results improve post-training auditability.",
        )
        self._append_quality_check(
            checks,
            name="reserve_pattern_coverage",
            passed=reserve_pattern_coverage >= _QUALITY_RESERVE_PATTERN_COVERAGE_MIN,
            severity="warning",
            value=round(reserve_pattern_coverage, 4),
            threshold=_QUALITY_RESERVE_PATTERN_COVERAGE_MIN,
            detail="Reserve-price samples improve scenario spread validation.",
        )
        self._append_quality_check(
            checks,
            name="bid_rate_variance",
            passed=sample_count < 2 or std_bid_rate <= _QUALITY_BID_RATE_VARIANCE_MAX,
            severity="warning",
            value=round(float(std_bid_rate), 6),
            threshold=_QUALITY_BID_RATE_VARIANCE_MAX,
            detail="Very high bid-rate variance should be reviewed before promotion.",
        )

        blocking_issue_count = sum(1 for check in checks if not check["passed"] and check["severity"] == "blocking")
        warning_count = sum(1 for check in checks if not check["passed"] and check["severity"] == "warning")
        score = max(
            0,
            _QUALITY_SCORE_BASE
            - (blocking_issue_count * _QUALITY_SCORE_BLOCKING_PENALTY)
            - (warning_count * _QUALITY_SCORE_WARNING_PENALTY),
        )
        status = "failed" if blocking_issue_count else ("warning" if warning_count else "passed")

        return {
            "report_version": "1",
            "release_tag": release_tag,
            "created_at": utc_now().isoformat(),
            "category": category,
            "agency_name": agency_name,
            "limit": limit,
            "status": status,
            "score": score,
            "blocking_issue_count": blocking_issue_count,
            "warning_count": warning_count,
            "metrics": {
                "sample_count": sample_count,
                "required_sample_count": required_samples,
                "required_holdout_sample_count": configured_holdout_size,
                "project_count": project_count,
                "agency_count": agency_count,
                "linked_result_count": linked_result_count,
                "linked_result_coverage": round(linked_result_coverage, 4),
                "reserve_pattern_sample_count": reserve_pattern_sample_count,
                "reserve_pattern_coverage": round(reserve_pattern_coverage, 4),
                "average_bid_rate": round(float(mean(bid_rates)), 6) if bid_rates else None,
                "std_bid_rate": round(float(std_bid_rate), 6),
                "started_at": summary.get("started_at"),
                "ended_at": summary.get("ended_at"),
            },
            "checks": checks,
        }

    def _append_quality_check(
        self,
        checks: list[dict[str, Any]],
        *,
        name: str,
        passed: bool,
        severity: str,
        value: Any,
        threshold: Any,
        detail: str,
    ) -> None:
        """Append one normalized dataset quality check."""
        checks.append({
            "name": name,
            "passed": bool(passed),
            "severity": severity,
            "value": value,
            "threshold": threshold,
            "detail": detail,
        })
