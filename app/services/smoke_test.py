"""KONEPS + Telegram end-to-end smoke test service.

Runs the first four phases of docs/operations/koneps-telegram-smoke-test-plan.md
in sequence and reports a one-line outcome to the operator's Telegram chat.
Designed to fire from Celery beat once a day.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.models.models import SmokeTestRun

logger = logging.getLogger(__name__)


@dataclass
class PhaseResult:
    name: str
    passed: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class SmokeTestReport:
    started_at: str = ""
    completed_at: str = ""
    overall_passed: bool = False
    phases: list[dict[str, Any]] = field(default_factory=list)
    telegram_message_id: int | None = None
    telegram_status: str = ""


class KonepsTelegramSmokeTestService:
    """Run the 4-phase smoke test and notify the operator via Telegram."""

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
            phases.append(PhaseResult(name="sbert_embedding", detail="skipped — Phase 1 failed"))

        # Phase 3: predict_price (only if we have a project)
        if latest_project is not None:
            p3 = self._phase_predict_price(db, latest_project)
            phases.append(p3)
        else:
            phases.append(PhaseResult(name="predict_price", detail="skipped — no eligible project"))

        # Phase 4: Telegram ping (always attempted — that's the point)
        p4 = self._phase_telegram_ping(report=report, prior_phases=phases)
        phases.append(p4)

        report.overall_passed = all(p.passed for p in phases)
        report.completed_at = datetime.now(timezone.utc).isoformat()
        report.phases = [asdict(p) for p in phases]
        return report

    def persist_report(self, db: Session, report: SmokeTestReport) -> "SmokeTestRun":
        """Persist one smoke cycle as a ``SmokeTestRun`` row and return it.

        Stores only the trimmed ``{name, passed, detail}`` slice of each phase
        (the bulky per-phase ``data`` dict is dropped to keep rows small). Empty
        timestamp strings tolerate to ``None`` and null Telegram fields (e.g.
        ENVIRONMENT=test skips Telegram) must not raise. Plain DB session, no
        subtask — safe under the ``memory://`` eager broker.
        """
        from app.models.models import SmokeTestRun

        trimmed_phases = [
            {
                "name": str(phase.get("name") or ""),
                "passed": bool(phase.get("passed")),
                "detail": str(phase.get("detail") or ""),
            }
            for phase in (report.phases or [])
        ]
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

    def _phase_koneps_collect(self, db: Session) -> PhaseResult:
        result = PhaseResult(name="koneps_collect")
        try:
            from app.schemas.schemas import CrawlRequest
            from app.services.koneps.collector import KonepsCollectorService

            req = CrawlRequest(source="koneps-openapi", execution_mode="live", max_items=10)
            collect_result = KonepsCollectorService().collect_notices(req)
            count = int(collect_result.get("collected_count") or 0)
            result.data["collected_count"] = count
            result.passed = count >= 1
            result.detail = f"collected {count}"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase koneps_collect failed")
        return result

    def _phase_sbert_embedding(self, db: Session) -> PhaseResult:
        result = PhaseResult(name="sbert_embedding")
        try:
            from sqlalchemy import text
            row = db.execute(text("""
                SELECT id, title, embedding_model, budget_estimate
                FROM projects WHERE created_at > now() - interval '30 minute'
                ORDER BY id DESC LIMIT 1
            """)).fetchone()
            if row is None:
                result.detail = "no recent project"
                return result
            model = row[2] or ""
            if "fallback-hash" in model:
                result.detail = f"fallback embedding: {model}"
                return result
            result.data["project"] = {"id": int(row[0]), "title": row[1], "budget_estimate": float(row[3] or 0)}
            result.passed = True
            result.detail = f"id={row[0]} model={model[-30:]}"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase sbert_embedding failed")
        return result

    def _phase_predict_price(self, db: Session, project_info: dict) -> PhaseResult:
        result = PhaseResult(name="predict_price")
        try:
            from app.ai.business_group import resolve_business_group
            from app.ai.price_prediction import predict_price
            from app.models.models import Project
            from app.services.backtest_cutoff import BacktestCutoffService

            project = db.query(Project).filter(Project.id == project_info["id"]).one()
            if not project.budget_estimate or project.budget_estimate <= 0:
                result.detail = f"id={project.id} no usable budget"
                return result
            desc = " ".join(p for p in [project.title, project.description or "", project.requirements or ""] if p)
            bg = resolve_business_group(project.business_type_code)
            cs = BacktestCutoffService()
            cutoff = cs.resolve_data_cutoff_at(project, tender_result=None, hours_before_deadline=0)
            history = cs.load_price_history_at_cutoff(
                db, category=project.category,
                agency_name=project.issuing_agency or project.demand_agency,
                cutoff_at=cutoff, exclude_project_id=int(project.id),
                limit=80, explicit_bid_rate_only=True,
            )
            pred = predict_price(
                budget=float(project.budget_estimate),
                category=project.category or "other",
                description=desc,
                historical_records=history,
                agency_name=project.issuing_agency or project.demand_agency,
                feedback_calibration=None,
                business_type_code=project.business_type_code,
                business_group=bg,
            )
            rate = float(pred.get("predicted_bid_rate") or 0)
            result.data["predicted_bid_rate"] = rate
            result.data["predictor_name"] = pred.get("predictor_name")
            # Upper bound matches the max guardrail ceiling (1.0). A rate above
            # 1.0 means the ceiling clamp did not apply — the smoke should fail,
            # not pass, so it can catch that guardrail regression.
            result.passed = 0.7 <= rate <= 1.0
            result.detail = f"rate={rate:.4f} predictor={pred.get('predictor_name')}"
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase predict_price failed")
        return result

    def _phase_telegram_ping(self, *, report: SmokeTestReport, prior_phases: list[PhaseResult]) -> PhaseResult:
        result = PhaseResult(name="telegram_ping")
        try:
            from app.services.notifications.telegram import TelegramNotificationService
            svc = TelegramNotificationService()
            if not svc.is_configured():
                result.detail = "telegram not configured"
                return result

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
        except Exception as exc:
            result.detail = f"exception: {type(exc).__name__}: {exc}"
            logger.exception("smoke phase telegram_ping failed")
        return result
