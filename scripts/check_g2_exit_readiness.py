#!/usr/bin/env python3
"""Check whether a G-2 exit manifest is ready for human review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._common import positive_int  # noqa: E402
from scripts.verify_g2_notification_targets import (  # noqa: E402
    VerificationInputError,
    build_summary as build_notification_summary,
)


BLOCKING_GAP_STATUSES = {"open", "triaged", "accepted_hold"}
READY_MANIFEST_STATUSES = {"ready_for_review", "reviewed"}
PASS_STATUS = "pass"
READY_EVIDENCE_STATUS = "ready"
BAD_OPERATOR_STATUSES = {"fail", "missing", "mixed_scope"}
REQUIRED_EVIDENCE_PATH_KEYS = {
    "g2_evidence",
    "candidate_preview",
    "operations_dashboard",
}
OPTIONAL_EVIDENCE_PATH_KEYS = {
    "strategy_monitor",
    "decision_experiments",
    "decision_apply_dry_run",
}
GATE_LABELS = {
    "operator_independence": (
        "3+ synthetic operators have independent IDs, profiles, strategies, "
        "and company metadata"
    ),
    "routing_isolation": (
        "Per-operator recommendation, G-2 ledger, and notification evidence "
        "is not mixed"
    ),
    "admin_surface_separation": (
        "Admin/operations evidence can distinguish backtest, smoke, stats, "
        "and collection state by operator"
    ),
    "user_surface_focus": (
        "User-facing dashboard inspection evidence is present without relying "
        "on cross-operator admin evidence"
    ),
}


class InvalidManifestError(Exception):
    """Raised when the manifest file cannot be parsed as the expected JSON."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check a G-2 exit review manifest for human-review readiness."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to manifest.json produced by scripts/build_g2_exit_review.py.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path where the JSON readiness report should be written.",
    )
    parser.add_argument(
        "--min-days",
        type=positive_int,
        default=7,
        help="Minimum counted pass days required. Default: 7.",
    )
    parser.add_argument(
        "--min-operators",
        type=positive_int,
        default=3,
        help="Minimum included operators required. Default: 3.",
    )
    return parser


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise InvalidManifestError(f"failed to read manifest: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise InvalidManifestError(f"manifest is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise InvalidManifestError("manifest must contain a JSON object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _status(value: Any) -> str:
    return str(value or "").lower()


def _operator_id(operator: dict[str, Any]) -> int | str | None:
    value = operator.get("operator_id")
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, str)):
        return value
    return str(value)


def _operator_label(operator: dict[str, Any], index: int) -> str:
    operator_id = _operator_id(operator)
    return str(operator_id) if operator_id is not None else f"index-{index}"


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _pass_daily_status_count(daily_status: list[dict[str, Any]]) -> int:
    pass_dates: set[str] = set()
    undated_pass_count = 0
    for row in daily_status:
        if _status(row.get("status")) != PASS_STATUS:
            continue
        date = row.get("date")
        if date:
            pass_dates.add(str(date))
        else:
            undated_pass_count += 1
    return len(pass_dates) + undated_pass_count


def _path_exists(path: str, *, repo_root: Path) -> bool:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.exists()


def _record_missing_path(
    missing_paths: list[dict[str, Any]],
    *,
    path: str,
    location: str,
    reason: str,
) -> None:
    entry = {"path": path, "location": location, "reason": reason}
    if entry not in missing_paths:
        missing_paths.append(entry)


def _check_path(
    missing_paths: list[dict[str, Any]],
    *,
    path: Any,
    location: str,
    repo_root: Path,
    required: bool = True,
) -> None:
    if not path:
        if required:
            _record_missing_path(
                missing_paths,
                path=location,
                location=location,
                reason="empty_required_path",
            )
        return
    if not isinstance(path, str):
        _record_missing_path(
            missing_paths,
            path=location,
            location=location,
            reason="path_must_be_string",
        )
        return
    if not _path_exists(path, repo_root=repo_root):
        _record_missing_path(
            missing_paths,
            path=path,
            location=location,
            reason="path_does_not_exist",
        )


def _check_path_list(
    missing_paths: list[dict[str, Any]],
    *,
    paths: Any,
    location: str,
    repo_root: Path,
    required: bool,
) -> list[str]:
    if not isinstance(paths, list) or not paths:
        if required:
            _record_missing_path(
                missing_paths,
                path=location,
                location=location,
                reason="empty_required_path",
            )
        return []

    clean_paths: list[str] = []
    for index, path in enumerate(paths):
        if isinstance(path, str):
            clean_paths.append(path)
        _check_path(
            missing_paths,
            path=path,
            location=f"{location}[{index}]",
            repo_root=repo_root,
            required=required,
        )
    return clean_paths


def _operator_path_checks(
    operators: list[dict[str, Any]],
    *,
    repo_root: Path,
    missing_paths: list[dict[str, Any]],
) -> None:
    for index, operator in enumerate(operators):
        operator_label = _operator_label(operator, index)
        for section in ("profile", "strategy", "notification_channel"):
            section_payload = _as_dict(operator.get(section))
            _check_path(
                missing_paths,
                path=section_payload.get("path"),
                location=f"operators[{operator_label}].{section}.path",
                repo_root=repo_root,
            )

        evidence_paths = _as_dict(operator.get("evidence_paths"))
        for key in sorted(REQUIRED_EVIDENCE_PATH_KEYS):
            _check_path_list(
                missing_paths,
                paths=evidence_paths.get(key),
                location=f"operators[{operator_label}].evidence_paths.{key}",
                repo_root=repo_root,
                required=True,
            )
        for key in sorted(OPTIONAL_EVIDENCE_PATH_KEYS):
            _check_path_list(
                missing_paths,
                paths=evidence_paths.get(key),
                location=f"operators[{operator_label}].evidence_paths.{key}",
                repo_root=repo_root,
                required=False,
            )


def _daily_path_checks(
    daily_status: list[dict[str, Any]],
    *,
    repo_root: Path,
    missing_paths: list[dict[str, Any]],
) -> None:
    for index, row in enumerate(daily_status):
        snapshot = _as_dict(row.get("collect_g2_evidence_snapshot"))
        _check_path(
            missing_paths,
            path=snapshot.get("path"),
            location=f"daily_status[{index}].collect_g2_evidence_snapshot.path",
            repo_root=repo_root,
        )


def _collect_snapshot_failures(
    daily_status: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(daily_status):
        snapshot = _as_dict(row.get("collect_g2_evidence_snapshot"))
        if _status(row.get("status")) not in {PASS_STATUS, "partial"}:
            continue
        if _status(snapshot.get("status")) == PASS_STATUS:
            continue
        failures.append(
            {
                "date": row.get("date"),
                "location": f"daily_status[{index}].collect_g2_evidence_snapshot.status",
                "path": snapshot.get("path"),
                "status": snapshot.get("status"),
                "reason": "collect_snapshot_status_not_pass",
            }
        )
    return failures


def _action_path_checks(
    action_register: dict[str, Any],
    *,
    repo_root: Path,
    missing_paths: list[dict[str, Any]],
) -> None:
    for key in ("dry_run_items", "approved_execution_items"):
        for index, item in enumerate(_as_list(action_register.get(key))):
            if not isinstance(item, dict):
                continue
            _check_path(
                missing_paths,
                path=item.get("output_path"),
                location=f"action_register.{key}[{index}].output_path",
                repo_root=repo_root,
                required=key == "approved_execution_items",
            )


def _blocking_gaps(gaps: list[Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        if _status(gap.get("status")) in BLOCKING_GAP_STATUSES:
            blockers.append(dict(gap))
    return blockers


def _resolve_repo_path(path: str, *, repo_root: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate


def _notification_file_failures(
    operators: list[dict[str, Any]],
    *,
    repo_root: Path,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for index, operator in enumerate(operators):
        operator_id = _operator_id(operator)
        operator_label = _operator_label(operator, index)
        channel = _as_dict(operator.get("notification_channel"))
        raw_path = channel.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = _resolve_repo_path(raw_path, repo_root=repo_root)
        if path in seen_paths or not path.exists():
            continue
        seen_paths.add(path)
        try:
            summary = build_notification_summary(path.parent)
        except VerificationInputError as exc:
            failures.append(
                {
                    "operator_id": operator_id,
                    "location": f"operators[{operator_label}].notification_channel.path",
                    "status": channel.get("status"),
                    "mode": channel.get("mode"),
                    "reason": "notification_file_invalid_input",
                    "path": raw_path,
                    "message": str(exc),
                }
            )
            continue
        for issue in _as_list(summary.get("issues")):
            if not isinstance(issue, dict) or issue.get("severity") != "failure":
                continue
            failures.append(
                {
                    "operator_id": issue.get("operator_id", operator_id),
                    "location": f"operators[{operator_label}].notification_channel.path",
                    "status": channel.get("status"),
                    "mode": channel.get("mode"),
                    "reason": f"notification_file_{issue.get('code')}",
                    "path": raw_path,
                    "message": issue.get("message"),
                }
            )
    return failures


def _notification_failures(
    operators: list[dict[str, Any]],
    daily_status: list[dict[str, Any]],
    *,
    repo_root: Path,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    seen: set[tuple[Any, str, str]] = set()

    def add_failure(
        *,
        operator_id: Any,
        location: str,
        status: Any,
        reason: str,
        mode: Any = None,
    ) -> None:
        key = (operator_id, location, reason)
        if key in seen:
            return
        seen.add(key)
        failures.append(
            {
                "operator_id": operator_id,
                "location": location,
                "status": status,
                "mode": mode,
                "reason": reason,
            }
        )

    for index, operator in enumerate(operators):
        operator_id = _operator_id(operator)
        operator_label = _operator_label(operator, index)
        channel = _as_dict(operator.get("notification_channel"))
        status = _status(channel.get("status"))
        mode = _status(channel.get("mode"))
        if status != PASS_STATUS:
            add_failure(
                operator_id=operator_id,
                location=f"operators[{operator_label}].notification_channel.status",
                status=channel.get("status"),
                mode=channel.get("mode"),
                reason="notification_channel_not_pass",
            )
        if mode in {"", "missing"}:
            add_failure(
                operator_id=operator_id,
                location=f"operators[{operator_label}].notification_channel.mode",
                status=channel.get("status"),
                mode=channel.get("mode"),
                reason="notification_mode_missing",
            )
        if channel.get("masked_target_present") is False:
            add_failure(
                operator_id=operator_id,
                location=(
                    f"operators[{operator_label}].notification_channel"
                    ".masked_target_present"
                ),
                status=channel.get("status"),
                mode=channel.get("mode"),
                reason="masked_target_missing",
            )
        if channel.get("raw_secret_absent") is False:
            add_failure(
                operator_id=operator_id,
                location=(
                    f"operators[{operator_label}].notification_channel"
                    ".raw_secret_absent"
                ),
                status=channel.get("status"),
                mode=channel.get("mode"),
                reason="raw_secret_present",
            )

    for row_index, row in enumerate(daily_status):
        daily_operators = _as_dict(row.get("operators"))
        for operator_id, operator_status in daily_operators.items():
            if not isinstance(operator_status, dict):
                continue
            status = _status(operator_status.get("notification_channel"))
            if status in BAD_OPERATOR_STATUSES:
                add_failure(
                    operator_id=operator_id,
                    location=(
                        f"daily_status[{row_index}].operators[{operator_id}]"
                        ".notification_channel"
                    ),
                    status=operator_status.get("notification_channel"),
                    reason="daily_notification_status_not_pass",
                )
    failures.extend(
        _notification_file_failures(
            operators,
            repo_root=repo_root,
        )
    )
    return failures


def _gate_result(
    gate_id: str,
    *,
    failures: list[str],
    evidence_paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "label": GATE_LABELS[gate_id],
        "passed": not failures,
        "failures": failures,
        "evidence_paths": sorted(set(evidence_paths or [])),
    }


def _build_failure_summary(
    *,
    manifest_status: Any,
    counted_days: int,
    pass_daily_status_count: int,
    min_days: int,
    operator_count: int,
    min_operators: int,
    blocking_gaps: list[dict[str, Any]],
    notification_failures: list[dict[str, Any]],
    missing_paths: list[dict[str, Any]],
    collect_snapshot_failures: list[dict[str, Any]],
    gates: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build a compact failure summary for operators reviewing hold reasons."""
    global_failures: list[dict[str, Any]] = []

    if _status(manifest_status) not in READY_MANIFEST_STATUSES:
        global_failures.append(
            {
                "check": "manifest_status_ready",
                "reason": (
                    f"manifest status {manifest_status!r} is not one of "
                    f"{sorted(READY_MANIFEST_STATUSES)}"
                ),
            }
        )
    if counted_days < min_days:
        global_failures.append(
            {
                "check": "counted_days_ready",
                "reason": f"counted_days {counted_days} is below required {min_days}",
            }
        )
    if pass_daily_status_count < min_days:
        global_failures.append(
            {
                "check": "pass_daily_status_count_ready",
                "reason": (
                    f"pass_daily_status_count {pass_daily_status_count} "
                    f"is below required {min_days}"
                ),
            }
        )
    if operator_count < min_operators:
        global_failures.append(
            {
                "check": "operator_count_ready",
                "reason": f"operator_count {operator_count} is below required {min_operators}",
            }
        )
    if blocking_gaps:
        global_failures.append(
            {
                "check": "no_open_blocking_gaps",
                "reason": f"{len(blocking_gaps)} unresolved blocking gap(s)",
            }
        )
    if notification_failures:
        global_failures.append(
            {
                "check": "no_notification_failures",
                "reason": f"{len(notification_failures)} notification failure(s)",
            }
        )
    if missing_paths:
        global_failures.append(
            {
                "check": "no_missing_evidence_paths",
                "reason": f"{len(missing_paths)} missing evidence path(s)",
            }
        )
    if collect_snapshot_failures:
        global_failures.append(
            {
                "check": "all_collect_snapshots_passed",
                "reason": f"{len(collect_snapshot_failures)} collect snapshot failure(s)",
            }
        )

    failed_gates = [
        {
            "gate_id": gate["gate_id"],
            "label": gate["label"],
            "failures": gate["failures"],
        }
        for gate in gates
        if not gate.get("passed")
    ]
    return {
        "global_failures": global_failures,
        "failed_gates": failed_gates,
    }


def _operator_independence_gate(
    operators: list[dict[str, Any]],
    *,
    min_operators: int,
) -> dict[str, Any]:
    failures: list[str] = []
    evidence_paths: list[str] = []
    if len(operators) < min_operators:
        failures.append(f"operator_count {len(operators)} is below {min_operators}")

    seen_ids: set[Any] = set()
    for index, operator in enumerate(operators):
        operator_id = _operator_id(operator)
        operator_label = _operator_label(operator, index)
        if operator_id is None:
            failures.append(f"operator {operator_label} is missing operator_id")
        elif operator_id in seen_ids:
            failures.append(f"operator_id {operator_id} appears more than once")
        else:
            seen_ids.add(operator_id)
        for field in ("username", "company"):
            if not operator.get(field):
                failures.append(f"operator {operator_label} is missing {field}")
        if _status(operator.get("operator_scope_status")) in BAD_OPERATOR_STATUSES:
            failures.append(
                f"operator {operator_label} scope status is "
                f"{operator.get('operator_scope_status')}"
            )
        for section in ("profile", "strategy"):
            section_payload = _as_dict(operator.get(section))
            evidence_path = section_payload.get("path")
            if isinstance(evidence_path, str):
                evidence_paths.append(evidence_path)
            if _status(section_payload.get("status")) != PASS_STATUS:
                failures.append(
                    f"operator {operator_label} {section} status is "
                    f"{section_payload.get('status')}"
                )
    return _gate_result(
        "operator_independence",
        failures=failures,
        evidence_paths=evidence_paths,
    )


def _routing_isolation_gate(
    operators: list[dict[str, Any]],
    daily_status: list[dict[str, Any]],
    notification_failures: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    failures = [
        f"{len(notification_failures)} notification failure(s) require review"
    ] if notification_failures else []
    evidence_paths: list[str] = []
    if blockers:
        failures.append(f"{len(blockers)} blocking gap(s) are unresolved")

    for index, operator in enumerate(operators):
        operator_label = _operator_label(operator, index)
        notification_path = _as_dict(operator.get("notification_channel")).get("path")
        if isinstance(notification_path, str):
            evidence_paths.append(notification_path)
        g2_paths = _as_dict(operator.get("evidence_paths")).get("g2_evidence")
        if not isinstance(g2_paths, list) or not g2_paths:
            failures.append(f"operator {operator_label} has no G-2 ledger path")
        else:
            evidence_paths.extend(path for path in g2_paths if isinstance(path, str))

    for row_index, row in enumerate(daily_status):
        if _status(row.get("status")) not in {"pass", "excluded"}:
            failures.append(
                f"daily_status[{row_index}] status is {row.get('status')}"
            )
        for operator_id, operator_status in _as_dict(row.get("operators")).items():
            if not isinstance(operator_status, dict):
                failures.append(
                    f"daily_status[{row_index}].operators[{operator_id}] is invalid"
                )
                continue
            g2_status = _status(operator_status.get("g2_evidence_status"))
            if g2_status != READY_EVIDENCE_STATUS:
                failures.append(
                    f"daily_status[{row_index}].operators[{operator_id}] "
                    f"g2_evidence_status is {operator_status.get('g2_evidence_status')}"
                )
            for key, value in operator_status.items():
                if key == "blocking_gap_ids":
                    continue
                if _status(value) in BAD_OPERATOR_STATUSES:
                    failures.append(
                        f"daily_status[{row_index}].operators[{operator_id}] "
                        f"{key} is {value}"
                    )
    return _gate_result(
        "routing_isolation",
        failures=failures,
        evidence_paths=evidence_paths,
    )


def _dashboard_paths(operator: dict[str, Any]) -> list[str]:
    paths = _as_dict(operator.get("evidence_paths")).get("operations_dashboard")
    return [path for path in _as_list(paths) if isinstance(path, str)]


def _admin_surface_gate(
    operators: list[dict[str, Any]],
    *,
    min_operators: int,
) -> dict[str, Any]:
    failures: list[str] = []
    evidence_paths: list[str] = []
    if len(operators) < min_operators:
        failures.append(f"operator_count {len(operators)} is below {min_operators}")
    for index, operator in enumerate(operators):
        operator_label = _operator_label(operator, index)
        dashboard_paths = _dashboard_paths(operator)
        operations_paths = [
            path for path in dashboard_paths if path.endswith("operations-dashboard.json")
        ]
        evidence_paths.extend(operations_paths)
        if not operations_paths:
            failures.append(
                f"operator {operator_label} has no operations-dashboard evidence"
            )
    return _gate_result(
        "admin_surface_separation",
        failures=failures,
        evidence_paths=evidence_paths,
    )


def _user_surface_gate(
    operators: list[dict[str, Any]],
    *,
    min_operators: int,
) -> dict[str, Any]:
    failures: list[str] = []
    evidence_paths: list[str] = []
    if len(operators) < min_operators:
        failures.append(f"operator_count {len(operators)} is below {min_operators}")
    for index, operator in enumerate(operators):
        operator_label = _operator_label(operator, index)
        dashboard_paths = _dashboard_paths(operator)
        operator_paths = [
            path for path in dashboard_paths if path.endswith("operator-dashboard.json")
        ]
        evidence_paths.extend(operator_paths)
        if not operator_paths:
            failures.append(
                f"operator {operator_label} has no operator-dashboard evidence"
            )
    return _gate_result(
        "user_surface_focus",
        failures=failures,
        evidence_paths=evidence_paths,
    )


def evaluate_readiness(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
    min_days: int = 7,
    min_operators: int = 3,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    evidence_window = _as_dict(manifest.get("evidence_window"))
    operators = [
        operator
        for operator in _as_list(manifest.get("operators"))
        if isinstance(operator, dict)
    ]
    daily_status = [
        row for row in _as_list(manifest.get("daily_status")) if isinstance(row, dict)
    ]
    action_register = _as_dict(manifest.get("action_register"))
    blocking_gaps = _blocking_gaps(_as_list(manifest.get("blocking_gaps")))
    counted_days = _int_or_zero(evidence_window.get("counted_days"))
    pass_daily_status_count = _pass_daily_status_count(daily_status)
    operator_count = len(operators)

    missing_paths: list[dict[str, Any]] = []
    _operator_path_checks(operators, repo_root=repo_root, missing_paths=missing_paths)
    _daily_path_checks(daily_status, repo_root=repo_root, missing_paths=missing_paths)
    _action_path_checks(
        action_register,
        repo_root=repo_root,
        missing_paths=missing_paths,
    )

    notification_failures = _notification_failures(
        operators,
        daily_status,
        repo_root=repo_root,
    )
    collect_snapshot_failures = _collect_snapshot_failures(daily_status)
    gates = [
        _operator_independence_gate(operators, min_operators=min_operators),
        _routing_isolation_gate(
            operators,
            daily_status,
            notification_failures,
            blocking_gaps,
        ),
        _admin_surface_gate(operators, min_operators=min_operators),
        _user_surface_gate(operators, min_operators=min_operators),
    ]
    global_checks = {
        "manifest_status_ready": _status(manifest.get("status"))
        in READY_MANIFEST_STATUSES,
        "counted_days_ready": counted_days >= min_days,
        "pass_daily_status_count_ready": pass_daily_status_count >= min_days,
        "operator_count_ready": operator_count >= min_operators,
        "no_open_blocking_gaps": not blocking_gaps,
        "no_notification_failures": not notification_failures,
        "no_missing_evidence_paths": not missing_paths,
        "all_collect_snapshots_passed": not collect_snapshot_failures,
    }
    ready_for_human_review = (
        all(gate["passed"] for gate in gates) and all(global_checks.values())
    )
    return {
        "manifest": str(manifest_path),
        "review_id": manifest.get("review_id"),
        "valid_manifest": True,
        "ready_for_human_review": ready_for_human_review,
        "manifest_status": manifest.get("status"),
        "counts": {
            "counted_days": counted_days,
            "required_days": min_days,
            "operator_count": operator_count,
            "required_operator_count": min_operators,
            "open_blocking_gap_count": len(blocking_gaps),
            "notification_failure_count": len(notification_failures),
            "missing_evidence_path_count": len(missing_paths),
        },
        "global_checks": global_checks,
        "gates": gates,
        "open_blocking_gaps": blocking_gaps,
        "notification_failures": notification_failures,
        "collect_snapshot_failures": collect_snapshot_failures,
        "missing_evidence_paths": missing_paths,
        "failure_summary": _build_failure_summary(
            manifest_status=manifest.get("status"),
            counted_days=counted_days,
            pass_daily_status_count=pass_daily_status_count,
            min_days=min_days,
            operator_count=operator_count,
            min_operators=min_operators,
            blocking_gaps=blocking_gaps,
            notification_failures=notification_failures,
            missing_paths=missing_paths,
            collect_snapshot_failures=collect_snapshot_failures,
            gates=gates,
        ),
    }


def _invalid_report(manifest_path: Path, message: str) -> dict[str, Any]:
    return {
        "manifest": str(manifest_path),
        "valid_manifest": False,
        "ready_for_human_review": False,
        "invalid_manifest_errors": [message],
    }


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = _read_manifest(args.manifest)
    except InvalidManifestError as exc:
        report = _invalid_report(args.manifest, str(exc))
        try:
            _write_json(args.output, report)
        except OSError as write_exc:
            stderr.write(f"{write_exc}\n")
            return 2
        stderr.write(f"{exc}\n")
        return 2

    report = evaluate_readiness(
        manifest,
        manifest_path=args.manifest,
        min_days=args.min_days,
        min_operators=args.min_operators,
    )
    try:
        _write_json(args.output, report)
    except OSError as exc:
        stderr.write(f"{exc}\n")
        return 2

    return 0 if report["ready_for_human_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
