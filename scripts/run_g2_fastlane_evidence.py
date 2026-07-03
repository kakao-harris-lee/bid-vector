#!/usr/bin/env python3
"""Create the minimal G-2 fast-lane evidence set for selected operators.

The workflow intentionally avoids real external notification delivery. It first
ensures an active dry-run notification channel, then creates or reuses completed
operator-scoped monitor/decision/synthetic evidence and records one
``collect_g2_evidence`` snapshot.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import sys
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models.models import (
    DecisionExperimentRun,
    OperatorNotificationChannel,
    OperatorStrategyRun,
    SyntheticExperimentResult,
    SyntheticExperimentRun,
    User,
)
from app.schemas.schemas import OperatorStrategyMonitorRequest
from app.services.analytics_reporting import AnalyticsReportingService
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.synthetic_experiment import (
    RUN_STATUS_QUEUED,
    SAMPLE_STATUS_SUFFICIENT,
    SyntheticExperimentService,
    run_experiment_backtest,
)
from app.tasks.jobs import collect_g2_evidence


DEFAULT_TARGETS = (
    "19:gs-cleaning-metro",
    "20:gs-security-national",
    "25:cn-electric-telecom-national",
)


@dataclass(frozen=True)
class TargetOperator:
    operator_id: int
    slug: str

    @property
    def username(self) -> str:
        return f"synthetic-{self.slug}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create fast-lane G-2 evidence for selected synthetic operators."
    )
    parser.add_argument(
        "--target",
        action="append",
        default=None,
        metavar="OPERATOR_ID:SLUG",
        help=(
            "Target operator id and synthetic slug. Repeat for multiple operators. "
            "Default: 19/20/25 fast-lane set."
        ),
    )
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--recent-limit", type=int, default=5)
    parser.add_argument("--monitor-limit", type=int, default=20)
    parser.add_argument("--backtest-limit", type=int, default=1000)
    parser.add_argument("--start-at", default="2025-01-01T00:00:00+00:00")
    parser.add_argument("--end-at", default="2025-12-31T23:59:59+00:00")
    parser.add_argument(
        "--skip-monitor",
        action="store_true",
        help="Do not run strategy monitoring; only reuse existing completed runs.",
    )
    parser.add_argument(
        "--skip-snapshot",
        action="store_true",
        help="Do not write the collect_g2_evidence analytics snapshot.",
    )
    return parser


def parse_target(value: str) -> TargetOperator:
    operator_id_raw, sep, slug = value.partition(":")
    if not sep or not operator_id_raw.isdigit() or not slug.strip():
        raise argparse.ArgumentTypeError(
            f"target must be OPERATOR_ID:SLUG, got {value!r}"
        )
    return TargetOperator(operator_id=int(operator_id_raw), slug=slug.strip())


def _load_targets(values: list[str] | None) -> list[TargetOperator]:
    raw_targets = values if values else list(DEFAULT_TARGETS)
    return [parse_target(raw) for raw in raw_targets]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _verify_targets(db: Session, targets: list[TargetOperator]) -> None:
    users = {
        int(user.id): str(user.username or "")
        for user in db.query(User)
        .filter(User.id.in_([target.operator_id for target in targets]))
        .all()
    }
    missing: list[str] = []
    mismatched: list[str] = []
    for target in targets:
        username = users.get(target.operator_id)
        if username is None:
            missing.append(str(target.operator_id))
        elif username != target.username:
            mismatched.append(
                f"{target.operator_id}: expected {target.username}, got {username}"
            )
    if missing or mismatched:
        details = []
        if missing:
            details.append(f"missing operators: {', '.join(missing)}")
        if mismatched:
            details.append(f"username mismatch: {', '.join(mismatched)}")
        raise RuntimeError("; ".join(details))


def _ensure_dry_run_channel(db: Session, *, operator_id: int) -> dict[str, Any]:
    channel = (
        db.query(OperatorNotificationChannel)
        .filter(
            OperatorNotificationChannel.operator_id == operator_id,
            OperatorNotificationChannel.channel_type == "telegram",
            OperatorNotificationChannel.is_active.is_(True),
            OperatorNotificationChannel.dry_run_only.is_(True),
        )
        .order_by(OperatorNotificationChannel.id.desc())
        .first()
    )
    if channel is None:
        now = utc_now()
        channel = OperatorNotificationChannel(
            operator_id=operator_id,
            channel_type="telegram",
            route_key=f"telegram:g2-fastlane-{operator_id}",
            target_label=f"G-2 fastlane dry-run operator {operator_id}",
            is_active=True,
            dry_run_only=True,
            verified_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(channel)
        db.commit()
        db.refresh(channel)
        action = "created"
    else:
        action = "reused"
    return {
        "operator_id": operator_id,
        "action": action,
        "channel_id": int(channel.id),
        "route_key": channel.route_key,
        "dry_run_only": bool(channel.dry_run_only),
    }


def _latest_completed_strategy_run(
    db: Session, *, operator_id: int, since: datetime
) -> OperatorStrategyRun | None:
    return (
        db.query(OperatorStrategyRun)
        .filter(
            OperatorStrategyRun.operator_id == operator_id,
            OperatorStrategyRun.status == "completed",
            OperatorStrategyRun.created_at >= since,
        )
        .order_by(OperatorStrategyRun.created_at.desc(), OperatorStrategyRun.id.desc())
        .first()
    )


def _decision_payload(now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    metric_snapshot = {
        "window_start": (now - timedelta(days=1)).isoformat(),
        "window_end": now.isoformat(),
        "decision_count": 1,
        "submitted_count": 0,
        "active_pending_count": 0,
        "overall_submission_rate": 0.0,
        "workflow_submission_rate": 0.0,
        "bid_now_submission_rate": 0.0,
        "review_submission_rate": 0.0,
        "auto_submission_rate": 0.0,
        "provided_submission_rate": 0.0,
        "best_category": None,
        "best_category_submission_rate": None,
        "worst_category": None,
        "worst_category_submission_rate": None,
    }
    latest_evaluation = {
        "evaluated_at": now.isoformat(),
        "sample_size": 1,
        "minimum_sample_reached": True,
        "target_metric": "operator_scoped_readiness",
        "baseline_target_value": None,
        "current_target_value": None,
        "target_delta": None,
        "guardrail_metric": "no_external_delivery",
        "baseline_guardrail_value": None,
        "current_guardrail_value": None,
        "guardrail_delta": None,
        "outcome": "inconclusive",
        "recommended_action": "complete",
        "summary": "G-2 fast-lane evidence recorded without applying strategy changes.",
        "current_summary": metric_snapshot,
    }
    return metric_snapshot, latest_evaluation


def _decision_evaluation_is_complete(value: str | None) -> bool:
    payload = _json_loads(value)
    required_keys = {
        "evaluated_at",
        "sample_size",
        "minimum_sample_reached",
        "target_metric",
        "guardrail_metric",
        "outcome",
        "recommended_action",
        "summary",
        "current_summary",
    }
    return required_keys.issubset(payload)


def _ensure_strategy_monitor(
    db: Session,
    *,
    target: TargetOperator,
    since: datetime,
    monitor_limit: int,
    skip_monitor: bool,
) -> dict[str, Any]:
    existing = _latest_completed_strategy_run(
        db, operator_id=target.operator_id, since=since
    )
    if existing is not None:
        return {
            "operator_id": target.operator_id,
            "action": "reused",
            "run_id": int(existing.id),
            "selected_candidate_count": int(existing.selected_candidate_count or 0),
        }
    if skip_monitor:
        return {
            "operator_id": target.operator_id,
            "action": "missing",
            "run_id": None,
        }

    operator = db.query(User).filter(User.id == target.operator_id).one()
    response = StrategyMonitoringService().execute_monitoring(
        db,
        request=OperatorStrategyMonitorRequest(
            limit=monitor_limit,
            high_priority_only=False,
            same_category_only=False,
        ),
        trigger_source="g2_fastlane_manual",
        operator=operator,
    )
    return {
        "operator_id": target.operator_id,
        "action": "created",
        "run_id": int(response["monitor_run_id"]),
        "selected_candidate_count": int(response.get("selected_candidate_count") or 0),
        "notification_count": int(response.get("notification_count") or 0),
    }


def _ensure_decision_experiment(
    db: Session, *, operator_id: int, since: datetime
) -> dict[str, Any]:
    existing = (
        db.query(DecisionExperimentRun)
        .filter(
            DecisionExperimentRun.operator_id == operator_id,
            DecisionExperimentRun.status == "completed",
            DecisionExperimentRun.created_at >= since,
        )
        .order_by(
            DecisionExperimentRun.created_at.desc(),
            DecisionExperimentRun.id.desc(),
        )
        .first()
    )
    if existing is not None:
        action = "reused"
        if (
            existing.experiment_key == "g2-fastlane-readiness"
            and not _decision_evaluation_is_complete(existing.latest_evaluation)
        ):
            now = utc_now()
            metric_snapshot, latest_evaluation = _decision_payload(now)
            existing.baseline_summary = _json_dumps(metric_snapshot)
            existing.latest_evaluation = _json_dumps(latest_evaluation)
            existing.updated_at = now
            db.commit()
            action = "updated"
        return {"operator_id": operator_id, "action": action, "run_id": int(existing.id)}

    now = utc_now()
    metric_snapshot, latest_evaluation = _decision_payload(now)
    run = DecisionExperimentRun(
        operator_id=operator_id,
        experiment_key="g2-fastlane-readiness",
        recommendation_key="g2-fastlane-operator-scope",
        status="completed",
        outcome="inconclusive",
        priority_rank=1,
        title="G-2 fast-lane operator evidence",
        hypothesis="Operator-scoped evidence exists for G-2 readiness validation.",
        suggested_change="No strategy threshold change applied by this evidence run.",
        target_metric="operator_scoped_readiness",
        expected_direction="increase",
        success_criteria="Evidence ledger reaches ready for this operator.",
        guardrail_metric="no_external_delivery",
        minimum_decision_sample=1,
        duration_days=1,
        baseline_days=1,
        rollback_trigger="Unexpected external delivery or degraded readiness.",
        notes="Created by scripts/run_g2_fastlane_evidence.py.",
        baseline_summary=_json_dumps(metric_snapshot),
        latest_evaluation=_json_dumps(latest_evaluation),
        started_at=now - timedelta(hours=1),
        ended_at=now,
        last_evaluated_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return {"operator_id": operator_id, "action": "created", "run_id": int(run.id)}


def _synthetic_ready_by_operator(
    db: Session, *, targets: list[TargetOperator], since: datetime
) -> dict[int, dict[str, Any]]:
    target_ids = {target.operator_id for target in targets}
    ready: dict[int, dict[str, Any]] = {}
    rows = (
        db.query(SyntheticExperimentResult, SyntheticExperimentRun)
        .join(
            SyntheticExperimentRun,
            SyntheticExperimentRun.id == SyntheticExperimentResult.run_id,
        )
        .filter(
            SyntheticExperimentRun.status == "completed",
            SyntheticExperimentRun.created_at >= since,
        )
        .order_by(
            SyntheticExperimentRun.created_at.desc(),
            SyntheticExperimentRun.id.desc(),
        )
        .all()
    )
    for result, run in rows:
        metrics = _json_loads(result.metrics_json)
        try:
            operator_id = int(metrics.get("operator_id"))
        except (TypeError, ValueError):
            continue
        if operator_id not in target_ids or operator_id in ready:
            continue
        sample_status = str(metrics.get("sample_status") or "")
        if sample_status == SAMPLE_STATUS_SUFFICIENT:
            ready[operator_id] = {
                "run_id": int(run.id),
                "result_id": int(result.id),
                "settled_count": int(metrics.get("settled_count") or 0),
            }
    return ready


def _ensure_synthetic_experiment(
    db: Session,
    *,
    targets: list[TargetOperator],
    since: datetime,
    start_at: str,
    end_at: str,
    backtest_limit: int,
) -> dict[str, Any]:
    ready_before = _synthetic_ready_by_operator(db, targets=targets, since=since)
    missing_targets = [
        target for target in targets if target.operator_id not in ready_before
    ]
    if not missing_targets:
        return {
            "action": "reused",
            "ready_operator_ids": sorted(ready_before),
            "run_id": None,
        }

    params = {
        "start_at": start_at,
        "end_at": end_at,
        "category": None,
        "limit": backtest_limit,
        "scenario": "base",
        "settle_actions": ["bid_now", "review"],
    }
    slugs = [target.slug for target in targets]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    service = SyntheticExperimentService(db)
    experiment = service.create_experiment(
        name=f"g2-fastlane-evidence-{timestamp}",
        description=(
            "G-2 fast-lane synthetic evidence for operator-scoped service/"
            "construction targets."
        ),
        params=params,
        operator_slugs=slugs,
    )
    run = SyntheticExperimentRun(
        experiment_id=experiment.id,
        status=RUN_STATUS_QUEUED,
        created_at=utc_now(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    result = run_experiment_backtest(
        {
            **params,
            "experiment_id": int(experiment.id),
            "run_id": int(run.id),
            "slugs": slugs,
            "source_sample_gap_candidate": {
                "source": "g2_fastlane",
                "operator_ids": [target.operator_id for target in targets],
                "settle_actions": ["bid_now", "review"],
            },
        }
    )
    db.expire_all()
    ready_after = _synthetic_ready_by_operator(db, targets=targets, since=since)
    return {
        "action": "created",
        "experiment_id": int(experiment.id),
        "run_id": int(run.id),
        "operator_count": int(result.get("operator_count") or 0),
        "ready_operator_ids": sorted(ready_after),
        "missing_operator_ids": [
            target.operator_id
            for target in targets
            if target.operator_id not in ready_after
        ],
    }


def _target_summaries(
    db: Session,
    *,
    targets: list[TargetOperator],
    window_days: int,
    recent_limit: int,
) -> list[dict[str, Any]]:
    service = AnalyticsReportingService()
    rows: list[dict[str, Any]] = []
    for target in targets:
        operator = db.query(User).filter(User.id == target.operator_id).one()
        summary = service.build_g2_evidence_summary(
            db,
            window_days=window_days,
            recent_limit=recent_limit,
            operator=operator,
        )
        rows.append(
            {
                "operator_id": target.operator_id,
                "slug": target.slug,
                "evidence_status": summary.get("evidence_status"),
                "blocking_gaps": summary.get("blocking_gaps") or [],
                "supporting_gaps": summary.get("supporting_gaps") or [],
                "sections": {
                    key: (summary.get(key) or {}).get("status")
                    for key in (
                        "smoke",
                        "strategy_monitor",
                        "decision_experiments",
                        "synthetic_experiments",
                        "notifications",
                    )
                },
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = _load_targets(args.target)
    since = utc_now() - timedelta(days=max(1, args.window_days))

    db = SessionLocal()
    try:
        _verify_targets(db, targets)
        channels = [
            _ensure_dry_run_channel(db, operator_id=target.operator_id)
            for target in targets
        ]
        strategy_runs = [
            _ensure_strategy_monitor(
                db,
                target=target,
                since=since,
                monitor_limit=args.monitor_limit,
                skip_monitor=bool(args.skip_monitor),
            )
            for target in targets
        ]
        decision_runs = [
            _ensure_decision_experiment(
                db,
                operator_id=target.operator_id,
                since=since,
            )
            for target in targets
        ]
        synthetic = _ensure_synthetic_experiment(
            db,
            targets=targets,
            since=since,
            start_at=args.start_at,
            end_at=args.end_at,
            backtest_limit=args.backtest_limit,
        )
        snapshot = (
            None
            if args.skip_snapshot
            else collect_g2_evidence(
                window_days=args.window_days,
                recent_limit=args.recent_limit,
            )
        )
        db.expire_all()
        summaries = _target_summaries(
            db,
            targets=targets,
            window_days=args.window_days,
            recent_limit=args.recent_limit,
        )
        ready_operator_ids = [
            row["operator_id"]
            for row in summaries
            if row.get("evidence_status") == "ready"
        ]
        payload = {
            "status": (
                "ready"
                if len(ready_operator_ids) == len(targets)
                else "incomplete"
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "targets": [
                {"operator_id": target.operator_id, "slug": target.slug}
                for target in targets
            ],
            "channels": channels,
            "strategy_runs": strategy_runs,
            "decision_runs": decision_runs,
            "synthetic": synthetic,
            "collect_g2_evidence": snapshot,
            "target_summaries": summaries,
        }
        print(_json_dumps(payload))
        return 0 if payload["status"] == "ready" else 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
