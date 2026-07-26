"""Strategy filtering, scoring/estimation, and stateless leaf helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Iterable

from app.ai.predictors.historical import apply_probability_calibration
from app.core.single_user import split_multi_value_text
from app.core.time import ensure_utc, utc_now
from app.domain.aggregates import average
from app.domain.rate_normalization import to_bid_rate_fraction
from app.models.models import CompanyProfile, Project, TenderResult
from app.services.paper_bidding_backtest.base import _PaperBiddingBase

logger = logging.getLogger(__name__)


class _ScoringMixin(_PaperBiddingBase):
    """Strategy gate, matched/probability/competitiveness scoring, leaf helpers."""

    def _passes_strategy(self, project: Project, strategy) -> bool:
        category = str(project.category or "").strip().lower()
        focus_categories = [
            value.lower() for value in split_multi_value_text(strategy.focus_categories)
        ]
        if focus_categories and category not in focus_categories:
            return False

        budget = self._resolve_project_budget(project)
        min_budget = float(strategy.min_budget_estimate or 0.0)
        max_budget = float(strategy.max_budget_estimate or 0.0)
        if min_budget > 0 and budget < min_budget:
            return False
        if max_budget > 0 and budget > max_budget:
            return False

        searchable_text = " ".join(
            part
            for part in [
                project.title,
                project.description,
                project.requirements,
                project.issuing_agency,
                project.demand_agency,
            ]
            if part
        ).lower()
        required_keywords = [
            value.lower()
            for value in split_multi_value_text(strategy.required_keywords)
        ]
        if required_keywords and not all(
            keyword in searchable_text for keyword in required_keywords
        ):
            return False

        exclude_keywords = [
            value.lower() for value in split_multi_value_text(strategy.exclude_keywords)
        ]
        if any(keyword in searchable_text for keyword in exclude_keywords):
            return False
        return True

    def _select_scenario(
        self, prediction: dict[str, Any], *, scenario: str
    ) -> dict[str, Any]:
        candidates = prediction.get("bid_rate_candidates") or []
        for candidate in candidates:
            if str(candidate.get("label") or "") == scenario:
                return candidate
        for candidate in candidates:
            if str(candidate.get("label") or "") == self.DEFAULT_SCENARIO:
                return candidate
        return {
            "label": self.DEFAULT_SCENARIO,
            "bid_rate": prediction.get("predicted_bid_rate", 0.0),
            "predicted_price": prediction.get("predicted_price", 0.0),
        }

    def _resolve_project_budget(self, project: Project) -> float:
        for value in [project.budget_estimate, project.budget_max, project.budget_min]:
            budget = float(value or 0.0)
            if budget > 0:
                return budget
        return 0.0

    def _resolve_matched_score(
        self, *, project: Project, profile: CompanyProfile | None
    ) -> tuple[float, list[str], str]:
        """Resolve the matched score for *project* against the operator *profile*.

        When a profile is available the real classifier (license/region/budget/
        capability/semantic axes) drives the score so per-operator profile
        differences are reflected. When no profile exists or the classifier raises,
        fall back to the legacy field-presence heuristic so a single bad project can
        never abort an entire run.

        Returns ``(matched_score, reasons, source)`` where ``source`` is one of
        ``"classifier"`` or ``"heuristic"`` for audit purposes.
        """
        if profile is None:
            return self._estimate_matched_score(project=project), [], "heuristic"

        try:
            classification = self.classifier.classify(project=project, profile=profile)
            score = float(classification.get("score") or 0.0)
            reasons = [str(reason) for reason in (classification.get("reasons") or [])]
            return round(max(0.0, min(1.0, score)), 2), reasons, "classifier"
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning(
                "paper_bidding_backtest: classifier failed for project %s, "
                "falling back to heuristic matched score (%s)",
                getattr(project, "id", "?"),
                exc,
            )
            return self._estimate_matched_score(project=project), [], "heuristic"

    def _compose_reasoning(
        self, *, decision_reasoning: str, match_reasons: list[str], match_source: str
    ) -> str:
        """Append profile-fit reasons to the decision reasoning for audit trails.

        Keeps the existing decision reasoning as the leading text so downstream
        consumers that only parse the first sentence stay compatible.
        """
        base = str(decision_reasoning or "").strip()
        if match_source != "classifier" or not match_reasons:
            return base
        joined = " ".join(
            reason.strip() for reason in match_reasons if reason and reason.strip()
        )
        if not joined:
            return base
        fit_note = f"[프로필 매칭] {joined}"
        return f"{base} {fit_note}".strip() if base else fit_note

    def _estimate_matched_score(self, *, project: Project) -> float:
        score = 0.72
        if project.category:
            score += 0.08
        if project.issuing_agency or project.demand_agency:
            score += 0.05
        if project.requirements or project.description:
            score += 0.05
        return round(min(1.0, score), 2)

    def _estimate_probability_score(
        self,
        *,
        matched_score: float,
        prediction: dict[str, Any],
        history_count: int,
        business_group: str | None = None,
    ) -> float:
        """Estimate the낙찰 가능성 (P(win)) signal consumed by the decision engine.

        Prefers the settlement-calibrated curve (summary.probability_calibration in
        the active ensemble artifact) when present; otherwise falls back to the
        legacy heuristic so offline / fresh environments keep working unchanged.
        """
        confidence = max(
            0.0, min(1.0, float(prediction.get("confidence_score", 0.0) or 0.0))
        )
        calibrated = apply_probability_calibration(
            {
                "confidence_score": confidence,
                "matched_score": matched_score,
                "business_group": business_group,
            }
        )
        if calibrated is not None:
            return calibrated
        history_signal = min(1.0, max(0.0, history_count / 30))
        probability = (
            matched_score * self.WIN_PROBABILITY_MATCHED_WEIGHT
            + confidence * self.WIN_PROBABILITY_CONFIDENCE_WEIGHT
            + history_signal * self.WIN_PROBABILITY_HISTORY_WEIGHT
        )
        return round(max(0.0, min(1.0, probability)), 2)

    def _estimate_competitiveness_score(self, paper_bid_rate: float) -> float:
        target_rate = self.COMPETITIVENESS_TARGET_RATE
        return round(
            max(
                0.0,
                min(
                    1.0,
                    1.0
                    - (
                        abs(paper_bid_rate - target_rate)
                        / self.COMPETITIVENESS_RATE_TOLERANCE
                    ),
                ),
            ),
            2,
        )

    def _estimate_expected_margin_score(self, paper_bid_rate: float) -> float:
        return round(max(0.0, min(1.0, paper_bid_rate)), 2)

    def _estimate_execution_complexity_score(self, project: Project) -> float:
        budget = self._resolve_project_budget(project)
        if budget >= self.EXECUTION_COMPLEXITY_TIER1_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER1_SCORE
        if budget >= self.EXECUTION_COMPLEXITY_TIER2_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER2_SCORE
        if budget >= self.EXECUTION_COMPLEXITY_TIER3_BUDGET:
            return self.EXECUTION_COMPLEXITY_TIER3_SCORE
        return self.EXECUTION_COMPLEXITY_DEFAULT_SCORE

    def _deadline_hours_remaining(
        self, *, project: Project, data_cutoff_at: datetime
    ) -> int | None:
        if project.deadline is None:
            return None
        seconds = (
            ensure_utc(project.deadline) - ensure_utc(data_cutoff_at)
        ).total_seconds()
        return max(0, int(seconds // 3600))

    def _decision_status_for_action(self, action: str) -> str:
        if action == "bid_now":
            return "planned"
        if action == "review":
            return "reviewing"
        return "skipped"

    def _build_input_hash(
        self,
        *,
        project: Project,
        data_cutoff_at: datetime,
        scenario: str,
        history: list[dict[str, Any]],
        paper_bid_amount: float,
        strategy_version: str,
    ) -> str:
        payload = {
            "project_id": int(project.id),
            "data_cutoff_at": data_cutoff_at.isoformat(),
            "scenario": scenario,
            "history_ids": [record.get("historical_data_id") for record in history],
            "paper_bid_amount": paper_bid_amount,
            "strategy_version": strategy_version,
        }
        serialized = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _result_time(self, result: TenderResult) -> datetime:
        value = result.announced_at or result.created_at
        if value is None:
            return utc_now()
        return ensure_utc(value)

    def _normalize_actions(self, actions: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for action in actions:
            value = str(action or "").strip()
            if value and value not in normalized:
                normalized.append(value)
        return tuple(normalized or self.DEFAULT_SETTLE_ACTIONS)

    def _normalize_rate(self, value: float) -> float:
        # 스케일 판별을 단일 출처(app.domain.rate_normalization)로 통일한다. 종전 이곳만
        # 임계치가 2.0이라 (1.5, 2.0] 밴드의 율을 다른 6개 구현과 다르게 해석했다.
        return to_bid_rate_fraction(float(value or 0.0))

    def _average(self, values: list[float]) -> float | None:
        return average(values, digits=6)
