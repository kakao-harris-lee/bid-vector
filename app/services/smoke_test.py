"""KONEPS + Telegram end-to-end smoke test service.

Runs the core smoke phases described in docs/production-smoke-test.md and
reports a one-line outcome to the operator's Telegram chat. Designed to fire
from Celery beat once a day.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from app.ai.factory import build_price_prediction_port
from app.ai.service_interfaces import PricePredictionPort
from app.services.smoke_failure_taxonomy import (
    FAILURE_GUIDANCE,
    classify_failure,
    guidance_for,
)

if TYPE_CHECKING:
    from app.models.models import SmokeTestRun
    from app.services.backtest_cutoff import BacktestCutoffService
    from app.services.koneps.collector import KonepsCollectorService
    from app.services.notifications.telegram import TelegramNotificationService
    from app.services.opportunity_monitoring import StrategyMonitoringService

logger = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    name: str
    passed: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    failure_category: str = ""
    action_required: str = ""
    retry_method: str = ""
    skip_reason: str = ""


@dataclass
class SmokeTestReport:
    started_at: str = ""
    completed_at: str = ""
    overall_passed: bool = False
    phases: list[dict[str, Any]] = field(default_factory=list)
    telegram_message_id: int | None = None
    telegram_status: str = ""


class KonepsTelegramSmokeTestService:
    """Run the smoke test phases and notify the operator via Telegram."""

    SCHEDULED_SMOKE_EVIDENCE_SCOPE = "g0_scheduled_smoke"
    SCHEDULED_SMOKE_CANONICAL_ONLY_REASON = (
        "G-0 scheduled smoke validates the canonical shared pipeline; "
        "G-2 per-operator evidence is recorded on operator-scoped monitor and experiment runs."
    )

    # Canonical guidance + classifier now live in
    # ``app.services.smoke_failure_taxonomy`` (shared with the operations
    # dashboard). The class alias preserves the historical
    # ``self.FAILURE_GUIDANCE`` access pattern.
    FAILURE_GUIDANCE: dict[str, dict[str, str]] = FAILURE_GUIDANCE

    def __init__(
        self,
        *,
        price_prediction_port: PricePredictionPort | None = None,
        koneps_collector: KonepsCollectorService | None = None,
        backtest_cutoff_service: BacktestCutoffService | None = None,
        strategy_monitoring_service: StrategyMonitoringService | None = None,
        telegram_service: TelegramNotificationService | None = None,
    ) -> None:
        self.price_prediction_port = price_prediction_port or build_price_prediction_port()
        # Injectable phase collaborators. Stored as the injected value (or
        # ``None``) and resolved inside each phase so an un-injected service
        # keeps the original lazy import + per-call instantiation, i.e. the same
        # creation point and lifetime as before.
        self._koneps_collector = koneps_collector
        self._backtest_cutoff_service = backtest_cutoff_service
        self._strategy_monitoring_service = strategy_monitoring_service
        self._telegram_service = telegram_service

    def run(self, db: Session) -> SmokeTestReport:
        report = SmokeTestReport(started_at=datetime.now(timezone.utc).isoformat())
        phases = []

        # Phase 1: live KONEPS fetch
        p1 = self._phase_koneps_collect(db)
        phases.append(p1)
        latest_project = None

        # Phase 2: sbert embedding (only if Phase 1 passed)
        if p1.passed:
            p2 = self._phase_sbert_embedding(db)
            phases.append(p2)
            latest_project = p2.data.get("project")
        else:
            phases.append(
                self._skipped_phase(
                    "sbert_embedding",
                    reason="Phase 1 failed",
                    upstream="koneps_collect",
                )
            )

        # Phase 3: predict_price (only if we have a project)
        if latest_project is not None:
            p3 = self._phase_predict_price(db, latest_project)
            phases.append(p3)
        else:
            phases.append(
                self._skipped_phase(
                    "predict_price",
                    reason="no eligible project",
                    upstream="sbert_embedding",
                )
            )

        # Phase 4: candidate generation / notification evidence. A zero-candidate
        # monitor run can still be an operationally useful green phase when it
        # records a clear skip reason.
        if p1.passed:
            p4 = self._phase_candidate_generation(db)
            phases.append(p4)
        else:
            phases.append(
                self._skipped_phase(
                    "candidate_generation",
                    reason="Phase 1 failed",
                    upstream="koneps_collect",
                )
            )

        # Phase 5: Telegram ping (always attempted — that's the point)
        p5 = self._phase_telegram_ping(report=report, prior_phases=phases)
        phases.append(p5)

        report.overall_passed = all(p.passed for p in phases)
        report.completed_at = datetime.now(timezone.utc).isoformat()
        report.phases = [asdict(p) for p in phases]
        return report

    def persist_report(self, db: Session, report: SmokeTestReport) -> "SmokeTestRun":
        """Persist one smoke cycle as a ``SmokeTestRun`` row and return it.

        Stores only the trimmed operational evidence for each phase (the bulky
        per-phase ``data`` dict is dropped). Empty timestamp strings tolerate to
        ``None`` and null Telegram fields (e.g. ENVIRONMENT=test skips Telegram)
        must not raise. Plain DB session, no subtask — safe under the
        ``memory://`` eager broker.
        """
        from app.models.models import SmokeTestRun

        trimmed_phases = []
        for phase in report.phases or []:
            trimmed = {
                "name": str(phase.get("name") or ""),
                "passed": bool(phase.get("passed")),
                "detail": str(phase.get("detail") or ""),
            }
            failure_category = str(phase.get("failure_category") or "")
            action_required = str(phase.get("action_required") or "")
            retry_method = str(phase.get("retry_method") or "")
            skip_reason = str(phase.get("skip_reason") or "")
            evidence = self._trim_phase_evidence(phase)
            if failure_category:
                trimmed["failure_category"] = failure_category
            if action_required:
                trimmed["action_required"] = action_required
            if retry_method:
                trimmed["retry_method"] = retry_method
            if skip_reason:
                trimmed["skip_reason"] = skip_reason
            if evidence:
                trimmed["evidence"] = evidence
            trimmed_phases.append(trimmed)
        run = SmokeTestRun(
            started_at=self._parse_iso(report.started_at),
            completed_at=self._parse_iso(report.completed_at),
            overall_passed=bool(report.overall_passed),
            phases=json.dumps(trimmed_phases, ensure_ascii=False),
            telegram_message_id=report.telegram_message_id,
            telegram_status=report.telegram_status or None,
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        """Parse an ISO timestamp, tolerating empty/None into ``None``."""
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _skipped_phase(self, name: str, *, reason: str, upstream: str) -> PhaseResult:
        failure_category = ""
        if reason == "Phase 1 failed":
            failure_category = "koneps_response"
        elif "no eligible" in reason or "no recent" in reason:
            failure_category = "no_candidate"
        result = PhaseResult(
            name=name,
            detail=f"skipped — {reason}",
            failure_category=failure_category,
            skip_reason=reason,
            data={"upstream_phase": upstream},
        )
        return self._finalize_phase(result)

    def _finalize_phase(self, result: PhaseResult) -> PhaseResult:
        result.data = self._with_phase_scope_evidence(result.name, result.data)
        return result if result.passed else self._annotate_failure(result)

    def _annotate_failure(self, result: PhaseResult) -> PhaseResult:
        if result.passed:
            result.failure_category = ""
            result.action_required = ""
            result.retry_method = ""
            return result
        category = result.failure_category or classify_failure(result.name, result.detail)
        guidance = guidance_for(category)
        result.failure_category = category
        result.action_required = result.action_required or guidance["action_required"]
        result.retry_method = result.retry_method or guidance["retry_method"]
        return result

    @staticmethod
    def _classify_failure(name: str, detail: str) -> str:
        """Delegate to the shared smoke failure taxonomy classifier."""
        return classify_failure(name, detail)

    @classmethod
    def _trim_phase_evidence(cls, phase: dict[str, Any]) -> dict[str, Any]:
        phase_name = str(phase.get("name") or "")
        evidence = phase.get("evidence")
        if isinstance(evidence, dict):
            return cls._with_phase_scope_evidence(phase_name, evidence)
        data = phase.get("data")
        if not isinstance(data, dict):
            return cls._with_phase_scope_evidence(phase_name, {})
        allowed_keys = {
            "evidence_scope",
            "operator_scope",
            "operator_id",
            "current_operator_id",
            "current_operator_username",
            "canonical_only_reason",
            "source_run_type",
            "source_run_id",
            "collected_count",
            "recent_collection_jobs",
            "recent_collection_count",
            "recent_collection_last_at",
            "project_id",
            "project_title",
            "predicted_bid_rate",
            "predictor_name",
            "monitor_run_id",
            "evaluated_project_count",
            "selected_candidate_count",
            "persisted_candidate_count",
            "notification_count",
            "new_candidate_count",
            "skip_reason",
            "telegram_status",
            "telegram_message_id",
        }
        compact = {key: value for key, value in data.items() if key in allowed_keys and value is not None}
        return cls._with_phase_scope_evidence(phase_name, compact)

    @classmethod
    def _with_phase_scope_evidence(cls, phase_name: str, evidence: dict[str, Any]) -> dict[str, Any]:
        scoped = dict(evidence or {})
        scoped.setdefault("evidence_scope", cls.SCHEDULED_SMOKE_EVIDENCE_SCOPE)

        source_run_id = cls._optional_int(scoped.get("source_run_id") or scoped.get("monitor_run_id"))
        if phase_name == "candidate_generation" and source_run_id is not None:
            scoped.setdefault("source_run_type", "operator_strategy_monitor")
            scoped.setdefault("source_run_id", source_run_id)

        operator_id = cls._optional_int(scoped.get("operator_id") or scoped.get("current_operator_id"))
        if operator_id is not None:
            scoped["operator_id"] = operator_id
            scoped.setdefault("operator_scope", "operator")
            return scoped

        scoped.setdefault("operator_scope", "canonical_only")
        scoped.setdefault("canonical_only_reason", cls.SCHEDULED_SMOKE_CANONICAL_ONLY_REASON)
        return scoped

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # Recency window for the collection-pipeline health cross-check used when
    # the live re-fetch legitimately returns zero notices. The KONEPS
    # BidPublicInfoService single-day query serves ~0 rows during the KST
    # overnight / pre-business-hours window (empirically the hourly beat itself
    # returns 0 across roughly KST 00:00-08:00 and ramps to saturation through
    # the business day). A daily smoke that fires inside that trough would flag
    # a false failure even though the pipeline is healthy, so a zero live fetch
    # falls back to "did the persisted hourly pipeline recently collect real
    # notices?" — mirroring the sbert phase's "recent real row" philosophy
    # instead of demanding this run produce a brand-new row. 26h comfortably
    # spans a full daily collection cycle (business-hours data + margin).
    KONEPS_COLLECT_RECENT_WINDOW_HOURS = 26

    def _phase_koneps_collect(self, db: Session) -> PhaseResult:
        result = PhaseResult(name="koneps_collect")
        try:
            from app.schemas.schemas import CrawlRequest
            from app.services.koneps.collector import KonepsCollectorService

            req = CrawlRequest(source="koneps-openapi", execution_mode="live", max_items=10)
            collector = self._koneps_collector or KonepsCollectorService()
            collect_result = collector.collect_notices(req)
            count = int(collect_result.get("collected_count") or 0)
            result.data["collected_count"] = count

            if count >= 1:
                # Live API reachable and returning fresh notices this run.
                result.passed = True
                result.detail = f"collected {count}"
                return self._finalize_phase(result)

            # Zero live notices. The koneps-openapi path raises on HTTP / bad
            # resultCode errors (caught below), so reaching here means the API
            # answered cleanly with an empty window — the expected KST overnight
            # trough. Validate pipeline health from the persisted hourly
            # collection instead of failing on a time-of-day-fragile re-fetch.
            health = self._recent_collection_health(db)
            result.data.update(health["evidence"])
            if health["healthy"]:
                result.passed = True
                result.detail = (
                    f"collected 0 live (off-peak); pipeline healthy: "
                    f"{health['recent_count']} notices via {health['recent_jobs']} "
                    f"job(s) in last {self.KONEPS_COLLECT_RECENT_WINDOW_HOURS}h"
                )
                return self._finalize_phase(result)

            # Zero live notices AND no recent successful persisted collection:
            # the hourly pipeline is genuinely stalled/empty — a real failure.
            # Pin the category to ``koneps_response`` (not the ``collected 0``
            # token's default ``no_candidate``): the live fetch just reached
            # KONEPS and got nothing while the hourly beat also persisted
            # nothing for a full cycle, so the operator guidance should point at
            # KONEPS availability / collection health, not "widen strategy
            # filters" (which misdirects for a genuine stall).
            result.detail = (
                f"collected 0 live and no notices persisted in last "
                f"{self.KONEPS_COLLECT_RECENT_WINDOW_HOURS}h"
            )
            result.failure_category = "koneps_response"
            result.skip_reason = "KONEPS returned zero notices and pipeline is stale"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase koneps_collect failed")
        return self._finalize_phase(result)

    def _recent_collection_health(self, db: Session) -> dict[str, Any]:
        """Summarise whether the hourly KONEPS collection recently succeeded.

        Looks for completed ``koneps-openapi`` crawl jobs that persisted at
        least one notice within ``KONEPS_COLLECT_RECENT_WINDOW_HOURS``. Returns
        a ``healthy`` flag plus evidence counts so ``_phase_koneps_collect`` can
        stay green when the live off-peak re-fetch returns zero while the beat
        pipeline is demonstrably alive, and fail loudly when it is not.
        """
        from datetime import timedelta

        from sqlalchemy import func

        from app.core.time import utc_now
        from app.models.models import CrawlJob

        cutoff = utc_now() - timedelta(hours=self.KONEPS_COLLECT_RECENT_WINDOW_HOURS)
        # Match the same ``koneps-openapi`` source the phase's live fetch uses
        # (both intentionally target the default KONEPS_COLLECTION_SOURCE). The
        # hourly beat persists jobs under ``request.source``; if an operator
        # ever points collection at a non-default source, revisit this filter.
        jobs, notices, last_at = (
            db.query(
                func.count(CrawlJob.id),
                func.coalesce(func.sum(CrawlJob.result_count), 0),
                func.max(CrawlJob.created_at),
            )
            .filter(
                CrawlJob.source == "koneps-openapi",
                CrawlJob.status == "completed",
                CrawlJob.result_count > 0,
                CrawlJob.created_at >= cutoff,
            )
            .one()
        )
        recent_jobs = int(jobs or 0)
        recent_count = int(notices or 0)
        # ``func.max`` may return a datetime (PostgreSQL) or an ISO string
        # (SQLite test backend, which does not re-apply the column type).
        if last_at is None:
            last_at_iso = None
        elif hasattr(last_at, "isoformat"):
            last_at_iso = last_at.isoformat()
        else:
            last_at_iso = str(last_at)
        return {
            "healthy": recent_jobs >= 1 and recent_count >= 1,
            "recent_jobs": recent_jobs,
            "recent_count": recent_count,
            "evidence": {
                "recent_collection_jobs": recent_jobs,
                "recent_collection_count": recent_count,
                "recent_collection_last_at": last_at_iso,
            },
        }

    # Recency window for selecting a real embedded project to validate the
    # embedding -> prediction pipeline health. Decoupled from KONEPS collection
    # novelty: the smoke verifies that *some* recent, real (non-fallback)
    # embedding can drive prediction, not that this run produced a brand-new row.
    # 7 days comfortably covers the daily collection + (re)embedding cadence
    # while still failing loudly if embeddings have gone stale/absent.
    SBERT_RECENT_WINDOW_DAYS = 7

    def _phase_sbert_embedding(self, db: Session) -> PhaseResult:
        result = PhaseResult(name="sbert_embedding")
        try:
            from datetime import timedelta

            from sqlalchemy import case

            from app.core.time import utc_now
            from app.models.models import Project

            window_days = self.SBERT_RECENT_WINDOW_DAYS
            cutoff = utc_now() - timedelta(days=window_days)

            # Most-recent-first by embedding refresh time, falling back to
            # created_at when embedding_updated_at is NULL (NULLS LAST via a
            # dialect-portable case() ordering key). Prefer projects with a
            # usable budget so the downstream predict_price phase exercises the
            # real guardrail band instead of immediately skipping on "no usable
            # budget" — this is a legitimate selection to maximise end-to-end
            # health coverage, not a result fabrication (predict_price still
            # validates the guardrail band and fails on regression).
            recency_key = case(
                (Project.embedding_updated_at.isnot(None), Project.embedding_updated_at),
                else_=Project.created_at,
            )
            has_budget = case((Project.budget_estimate > 0, 1), else_=0)

            base = db.query(Project).filter(
                Project.embedding.isnot(None),
                Project.embedding_model.isnot(None),
                Project.embedding_model != "",
                # Exclude fallback-hash embeddings (keep in lockstep with the
                # explicit fallback guard below and FALLBACK_EMBEDDING_MODEL).
                ~Project.embedding_model.contains("fallback-hash"),
                recency_key >= cutoff,
            )

            project = (
                base.order_by(
                    has_budget.desc(),
                    recency_key.desc(),
                    Project.id.desc(),
                )
                .first()
            )

            if project is None:
                # Distinguish an empty DB / no embeddings at all from "embedded
                # rows exist but are all older than the window". Operators need
                # to know whether to seed/collect or to investigate a stalled
                # embedding pipeline.
                any_real_embedded = (
                    db.query(Project.id)
                    .filter(
                        Project.embedding.isnot(None),
                        Project.embedding_model.isnot(None),
                        Project.embedding_model != "",
                        ~Project.embedding_model.contains("fallback-hash"),
                    )
                    .first()
                )
                if any_real_embedded is None:
                    skip_reason = "no non-fallback embedded project"
                else:
                    skip_reason = f"no embedded project in last {window_days}d"
                result.detail = skip_reason
                result.skip_reason = skip_reason
                return self._finalize_phase(result)

            model = project.embedding_model or ""
            # Defensive: the query already excludes fallback-hash, but keep the
            # explicit guard so the guardrail intent is local and obvious.
            if "fallback-hash" in model:
                result.detail = f"fallback embedding: {model}"
                result.skip_reason = "fallback embedding model"
                return self._finalize_phase(result)

            budget = float(project.budget_estimate or 0)
            result.data["project"] = {
                "id": int(project.id),
                "title": project.title,
                "budget_estimate": budget,
            }
            result.data["project_id"] = int(project.id)
            result.data["project_title"] = project.title
            result.passed = True
            result.detail = f"id={project.id} model={model[-30:]} budget={budget:.0f}"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase sbert_embedding failed")
        return self._finalize_phase(result)

    def _phase_predict_price(self, db: Session, project_info: dict) -> PhaseResult:
        result = PhaseResult(name="predict_price")
        try:
            from app.ai.business_group import resolve_business_group
            from app.models.models import Project
            from app.services.backtest_cutoff import BacktestCutoffService
            from app.services.bid_base import resolve_notice_legal_floor_inputs

            project = db.query(Project).filter(Project.id == project_info["id"]).one()
            if not project.budget_estimate or project.budget_estimate <= 0:
                result.detail = f"id={project.id} no usable budget"
                result.data["project_id"] = int(project.id)
                result.skip_reason = "no usable budget"
                return self._finalize_phase(result)
            desc = " ".join(p for p in [project.title, project.description or "", project.requirements or ""] if p)
            bg = resolve_business_group(project.business_type_code)
            cs = self._backtest_cutoff_service or BacktestCutoffService()
            cutoff = cs.resolve_data_cutoff_at(project, tender_result=None, hours_before_deadline=0)
            history = cs.load_price_history_at_cutoff(
                db, category=project.category,
                agency_name=project.issuing_agency or project.demand_agency,
                cutoff_at=cutoff, exclude_project_id=int(project.id),
                limit=80, explicit_bid_rate_only=True,
            )
            # 공사 법정 낙찰하한 tier 입력(구간=추정가격, 기준일=공고 시점). 스모크가
            # 실 공고에 대해 production live 경로와 동일한 era-correct guardrail 을
            # 태우도록 공고 자신의 날짜를 넘긴다(소급 없음). tier floor 는 [0.7, 1.0]
            # 안이라 아래 rate 범위 검증을 깨지 않는다.
            estimation_amount, reference_date = resolve_notice_legal_floor_inputs(
                project
            )
            pred = self.price_prediction_port.predict_price(
                budget=float(project.budget_estimate),
                category=project.category or "other",
                description=desc,
                historical_records=history,
                agency_name=project.issuing_agency or project.demand_agency,
                feedback_calibration=None,
                business_type_code=project.business_type_code,
                business_group=bg,
                estimation_amount=estimation_amount,
                reference_date=reference_date,
            )
            rate = float(pred.get("predicted_bid_rate") or 0)
            result.data["predicted_bid_rate"] = rate
            result.data["predictor_name"] = pred.get("predictor_name")
            result.data["project_id"] = int(project.id)
            # Upper bound matches the max guardrail ceiling (1.0). A rate above
            # 1.0 means the ceiling clamp did not apply — the smoke should fail,
            # not pass, so it can catch that guardrail regression.
            result.passed = 0.7 <= rate <= 1.0
            result.detail = f"rate={rate:.4f} predictor={pred.get('predictor_name')}"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase predict_price failed")
        return self._finalize_phase(result)

    def _phase_candidate_generation(self, db: Session) -> PhaseResult:
        result = PhaseResult(name="candidate_generation")
        try:
            from app.schemas.schemas import OperatorStrategyMonitorRequest
            from app.services.opportunity_monitoring import StrategyMonitoringService

            request = OperatorStrategyMonitorRequest(limit=3, high_priority_only=True)
            monitor = self._strategy_monitoring_service or StrategyMonitoringService()
            monitor_result = monitor.execute_monitoring(
                db,
                request=request,
                trigger_source=StrategyMonitoringService.SCHEDULED_TRIGGER_SOURCE,
            )
            evidence = {
                "monitor_run_id": monitor_result.get("monitor_run_id"),
                "source_run_type": "operator_strategy_monitor",
                "source_run_id": monitor_result.get("monitor_run_id"),
                "operator_id": monitor_result.get("operator_id"),
                "current_operator_id": monitor_result.get("current_operator_id"),
                "current_operator_username": monitor_result.get("current_operator_username"),
                "evaluated_project_count": int(monitor_result.get("evaluated_project_count") or 0),
                "selected_candidate_count": int(monitor_result.get("selected_candidate_count") or 0),
                "persisted_candidate_count": int(monitor_result.get("persisted_candidate_count") or 0),
                "notification_count": int(monitor_result.get("notification_count") or 0),
                "new_candidate_count": int(monitor_result.get("new_candidate_count") or 0),
            }
            result.data.update(evidence)
            result.passed = True
            if evidence["notification_count"] > 0:
                result.detail = (
                    f"run_id={evidence['monitor_run_id']} selected={evidence['selected_candidate_count']} "
                    f"persisted={evidence['persisted_candidate_count']} notifications={evidence['notification_count']}"
                )
            else:
                if evidence["selected_candidate_count"] == 0:
                    result.skip_reason = "no strategy candidates selected"
                elif evidence["persisted_candidate_count"] == 0:
                    result.skip_reason = "selected candidates already persisted or skipped"
                else:
                    result.skip_reason = "no new notification created"
                result.data["skip_reason"] = result.skip_reason
                result.detail = (
                    f"run_id={evidence['monitor_run_id']} selected={evidence['selected_candidate_count']} "
                    f"persisted={evidence['persisted_candidate_count']} notifications=0 "
                    f"skip_reason={result.skip_reason}"
                )
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase candidate_generation failed")
        return self._finalize_phase(result)

    def _phase_telegram_ping(self, *, report: SmokeTestReport, prior_phases: list[PhaseResult]) -> PhaseResult:
        result = PhaseResult(name="telegram_ping")
        try:
            from app.services.notifications.telegram import TelegramNotificationService
            svc = self._telegram_service or TelegramNotificationService()
            if not svc.is_configured():
                result.detail = "telegram not configured"
                return self._finalize_phase(result)

            lines = [
                f"[smoke] bid-vector e2e {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            ]
            for p in prior_phases:
                mark = "✓" if p.passed else "✗"
                lines.append(f"  {mark} {p.name}: {p.detail or '(no detail)'}")
            delivery = svc.send_message("\n".join(lines))
            report.telegram_status = str(delivery.get("status"))
            report.telegram_message_id = delivery.get("telegram_message_id")
            result.passed = bool(delivery.get("sent"))
            result.detail = f"status={delivery.get('status')} msg_id={delivery.get('telegram_message_id')}"
            result.data["delivery"] = delivery
            result.data["telegram_status"] = delivery.get("status")
            result.data["telegram_message_id"] = delivery.get("telegram_message_id")
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase telegram_ping failed")
        return self._finalize_phase(result)
