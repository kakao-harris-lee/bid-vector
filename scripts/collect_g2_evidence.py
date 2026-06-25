#!/usr/bin/env python3
"""Collect read-only G-2 evidence snapshots for multiple operators.

The runner only performs GET requests and writes local JSON evidence files. It
does not call KONEPS directly, update the database, or send Telegram messages.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, TextIO
from urllib import error as urlerror
from urllib import parse, request


class EvidenceCollectionError(Exception):
    """Raised when a collection step fails outside an operator endpoint call."""


class UsageError(Exception):
    """Raised for CLI arguments that need validation beyond argparse."""


@dataclass(frozen=True)
class EndpointSpec:
    key: str
    file_name: str
    path: str
    include_days: bool = False
    limit: int | None = None
    recent_limit: int | None = None
    recommendation_limit: int | None = None
    sort: str | None = None
    static_params: tuple[tuple[str, Any], ...] = ()

    def params(self, *, operator_id: int, days: int) -> dict[str, Any]:
        payload: dict[str, Any] = {"operator_id": operator_id}
        if self.include_days:
            payload["days"] = days
        if self.limit is not None:
            payload["limit"] = self.limit
        if self.recent_limit is not None:
            payload["recent_limit"] = self.recent_limit
        if self.recommendation_limit is not None:
            payload["recommendation_limit"] = self.recommendation_limit
        if self.sort is not None:
            payload["sort"] = self.sort
        payload.update(self.static_params)
        return payload


@dataclass(frozen=True)
class CollectionConfig:
    base_url: str
    token: str | None
    operator_ids: list[int]
    evidence_dir: Path
    days: int = 30
    fail_on_blocking_gaps: bool = False
    timeout_seconds: float = 20.0
    run_id: str | None = None


HttpGetJson = Callable[..., dict[str, Any]]


ENDPOINTS: tuple[EndpointSpec, ...] = (
    EndpointSpec(
        key="profile",
        file_name="profile.json",
        path="/api/v1/operator/profile",
    ),
    EndpointSpec(
        key="strategy",
        file_name="strategy.json",
        path="/api/v1/operator/strategy",
    ),
    EndpointSpec(
        key="notification_channels",
        file_name="notification-channels.json",
        path="/api/v1/operator/notification-channels",
    ),
    EndpointSpec(
        key="g2_evidence",
        file_name="g2-evidence.json",
        path="/api/v1/analytics/g2-evidence",
        include_days=True,
    ),
    EndpointSpec(
        key="operator_dashboard",
        file_name="operator-dashboard.json",
        path="/api/v1/operator/dashboard",
        include_days=True,
        limit=5,
    ),
    EndpointSpec(
        key="operations_dashboard",
        file_name="operations-dashboard.json",
        path="/api/v1/analytics/operations-dashboard",
        include_days=True,
        recent_limit=5,
    ),
    EndpointSpec(
        key="strategy_candidates",
        file_name="strategy-candidates.json",
        path="/api/v1/operator/strategy/candidates",
        limit=20,
        static_params=(("high_priority_only", "true"),),
    ),
    EndpointSpec(
        key="decision_experiments",
        file_name="decision-experiments.json",
        path="/api/v1/analytics/decision-experiments",
        limit=20,
        sort="needs_attention",
    ),
    EndpointSpec(
        key="decision_recommendations",
        file_name="decision-recommendations.json",
        path="/api/v1/analytics/decision-recommendations",
        include_days=True,
        recommendation_limit=5,
    ),
)
ENDPOINT_PATHS_BY_KEY = {endpoint.key: endpoint.path for endpoint in ENDPOINTS}
ENDPOINT_KEYS_BY_PATH = {endpoint.path: endpoint.key for endpoint in ENDPOINTS}
REPO_ROOT = Path(__file__).resolve().parents[1]
DATE_PATTERN = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})|(?P<compact>\d{8})")


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect read-only G-2 evidence JSON snapshots for at least three "
            "operators."
        )
    )
    parser.add_argument("--base-url", required=True, help="API base URL.")
    parser.add_argument(
        "--token",
        default=os.environ.get("TOKEN"),
        help="Bearer token. Defaults to the TOKEN environment variable.",
    )
    operator_source = parser.add_mutually_exclusive_group(required=True)
    operator_source.add_argument(
        "--operator-id",
        action="append",
        type=positive_int,
        help="Operator id to collect. Repeat for at least three operators.",
    )
    operator_source.add_argument(
        "--operators-file",
        type=Path,
        help=(
            "JSON file containing operator ids. Accepts a list, "
            '{"operator_ids": [...]}, or {"operators": [{"operator_id": ...}]}.'
        ),
    )
    parser.add_argument(
        "--evidence-dir",
        required=True,
        type=Path,
        help="Directory where the timestamped evidence run will be written.",
    )
    parser.add_argument(
        "--days",
        type=positive_int,
        default=30,
        help="G-2 evidence window in days. Default: 30.",
    )
    parser.add_argument(
        "--fail-on-blocking-gaps",
        action="store_true",
        help="Exit non-zero when any operator has blocking_gaps.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=positive_float,
        default=20.0,
        help="HTTP request timeout in seconds. Default: 20.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional run directory name. Defaults to a UTC timestamp.",
    )
    return parser


def parse_json_or_text(raw_body: str) -> Any:
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return raw_body


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    normalized_base = base_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{normalized_base}{normalized_path}"
    clean_params = {
        key: value for key, value in (params or {}).items() if value is not None
    }
    if clean_params:
        url = f"{url}?{parse.urlencode(clean_params)}"
    return url


def http_get_json(
    *,
    base_url: str,
    path: str,
    params: dict[str, Any] | None = None,
    token: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = build_url(base_url, path, params)
    req = request.Request(url, headers=headers, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        detail = parse_json_or_text(raw_body)
        raise EvidenceCollectionError(
            f"GET {path} returned HTTP {exc.code}: {detail}"
        ) from exc
    except urlerror.URLError as exc:
        raise EvidenceCollectionError(f"GET {path} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise EvidenceCollectionError(
            f"GET {path} timed out after {timeout_seconds:g}s"
        ) from exc

    payload = parse_json_or_text(raw_body)
    if not isinstance(payload, dict):
        raise EvidenceCollectionError(
            f"GET {path} returned non-object JSON: {payload!r}"
        )
    return payload


def _coerce_operator_id(value: Any) -> int:
    if isinstance(value, dict):
        if "operator_id" in value:
            value = value["operator_id"]
        elif "id" in value:
            value = value["id"]
        else:
            raise UsageError(f"operator object missing operator_id/id: {value!r}")
    if isinstance(value, bool):
        raise UsageError(f"invalid operator id: {value!r}")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
    else:
        raise UsageError(f"invalid operator id: {value!r}")
    if parsed < 1:
        raise UsageError(f"operator id must be positive: {parsed!r}")
    return parsed


def load_operator_ids_from_file(path: Path) -> list[int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UsageError(f"failed to read operators file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise UsageError(f"operators file is not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        if "operator_ids" in payload:
            payload = payload["operator_ids"]
        elif "operators" in payload:
            payload = payload["operators"]
        else:
            raise UsageError(
                "operators file object must contain operator_ids or operators"
            )
    if not isinstance(payload, list):
        raise UsageError("operators file must contain a JSON list")
    return [_coerce_operator_id(item) for item in payload]


def normalize_operator_ids(operator_ids: list[int], *, min_count: int = 3) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for item in operator_ids:
        operator_id = _coerce_operator_id(item)
        if operator_id not in seen:
            normalized.append(operator_id)
            seen.add(operator_id)
    if len(normalized) < min_count:
        raise UsageError(
            f"at least {min_count} unique operator ids are required; got {len(normalized)}"
        )
    return normalized


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _validate_run_id(run_id: str) -> str:
    clean = run_id.strip()
    if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise UsageError(f"invalid run id: {run_id!r}")
    return clean


def config_from_args(args: argparse.Namespace) -> CollectionConfig:
    operator_ids = (
        list(args.operator_id or [])
        if args.operator_id is not None
        else load_operator_ids_from_file(args.operators_file)
    )
    return CollectionConfig(
        base_url=args.base_url,
        token=args.token,
        operator_ids=normalize_operator_ids(operator_ids),
        evidence_dir=args.evidence_dir,
        days=args.days,
        fail_on_blocking_gaps=args.fail_on_blocking_gaps,
        timeout_seconds=args.timeout_seconds,
        run_id=_validate_run_id(args.run_id) if args.run_id else _default_run_id(),
    )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _channel_status(channels_payload: dict[str, Any]) -> dict[str, Any]:
    channels = channels_payload.get("channels")
    if not isinstance(channels, list):
        channels = []
    channel_count = _optional_int(channels_payload.get("channel_count"))
    if channel_count is None:
        channel_count = len(channels)
    active_count = sum(
        1
        for item in channels
        if isinstance(item, dict) and item.get("is_active") is True
    )
    dry_run_only_count = sum(
        1
        for item in channels
        if isinstance(item, dict) and item.get("dry_run_only") is True
    )
    verified_count = sum(
        1 for item in channels if isinstance(item, dict) and item.get("verified_at")
    )
    if channel_count == 0:
        status = "missing"
    elif active_count == 0:
        status = "inactive"
    elif dry_run_only_count >= active_count:
        status = "dry_run_only"
    else:
        status = "active"
    return {
        "notification_channel_count": channel_count,
        "notification_channel_status": status,
        "notification_active_channel_count": active_count,
        "notification_dry_run_only_channel_count": dry_run_only_count,
        "notification_verified_channel_count": verified_count,
    }


def build_operator_summary(
    *,
    operator_id: int,
    payloads: dict[str, dict[str, Any]],
    raw_files: dict[str, str],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = payloads.get("profile") or {}
    strategy = payloads.get("strategy") or {}
    channels = payloads.get("notification_channels") or {}
    g2 = payloads.get("g2_evidence") or {}

    current_operator_ids = {
        key: payload.get("current_operator_id")
        for key, payload in payloads.items()
        if "current_operator_id" in payload
    }
    current_operator_id_matches = bool(current_operator_ids) and all(
        _optional_int(value) == operator_id for value in current_operator_ids.values()
    )
    mismatched_current_operator_ids = {
        key: value
        for key, value in current_operator_ids.items()
        if _optional_int(value) != operator_id
    }

    notifications = (
        g2.get("notifications") if isinstance(g2.get("notifications"), dict) else {}
    )
    summary: dict[str, Any] = {
        "operator_id": operator_id,
        "current_operator_id": g2.get("current_operator_id"),
        "current_operator_username": g2.get("current_operator_username"),
        "current_operator_ids_by_endpoint": current_operator_ids,
        "current_operator_id_matches": current_operator_id_matches,
        "mismatched_current_operator_ids": mismatched_current_operator_ids,
        "evidence_status": g2.get(
            "evidence_status",
            "collection_failed" if errors else "unknown",
        ),
        "blocking_gaps": _list_of_strings(g2.get("blocking_gaps")),
        "profile_configured": profile.get("profile_configured"),
        "strategy_configured": strategy.get("strategy_configured"),
        "g2_notification_status": notifications.get("status"),
        "raw_files": raw_files,
        "collection_errors": errors,
    }
    summary.update(_channel_status(channels))
    return summary


def collect_operator_evidence(
    *,
    config: CollectionConfig,
    run_dir: Path,
    operator_id: int,
    http_get_json_func: HttpGetJson = http_get_json,
) -> dict[str, Any]:
    operator_dir = run_dir / f"operator-{operator_id}"
    payloads: dict[str, dict[str, Any]] = {}
    raw_files: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    for endpoint in ENDPOINTS:
        endpoint_path = operator_dir / endpoint.file_name
        try:
            payload = http_get_json_func(
                base_url=config.base_url,
                path=endpoint.path,
                params=endpoint.params(operator_id=operator_id, days=config.days),
                token=config.token,
                timeout_seconds=config.timeout_seconds,
            )
            payloads[endpoint.key] = payload
            write_json(endpoint_path, payload)
            raw_files[endpoint.key] = str(endpoint_path.relative_to(run_dir))
        except Exception as exc:  # noqa: BLE001 - keep collecting other evidence.
            error_payload = {
                "endpoint": endpoint.path,
                "operator_id": operator_id,
                "error": str(exc),
            }
            errors.append(error_payload)
            error_path = endpoint_path.with_suffix(".error.json")
            write_json(error_path, error_payload)
            raw_files[endpoint.key] = str(error_path.relative_to(run_dir))

    return build_operator_summary(
        operator_id=operator_id,
        payloads=payloads,
        raw_files=raw_files,
        errors=errors,
    )


def build_collection_summary(
    *,
    config: CollectionConfig,
    run_dir: Path,
    operator_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    blocking_gap_todos = [
        {"operator_id": item["operator_id"], "gap_index": index + 1, "todo": gap}
        for item in operator_summaries
        for index, gap in enumerate(item.get("blocking_gaps") or [])
    ]
    collection_error_count = sum(
        len(item.get("collection_errors") or []) for item in operator_summaries
    )
    if collection_error_count:
        status = "collection_failed"
    elif blocking_gap_todos:
        status = "blocking_gaps"
    else:
        status = "ready"
    return {
        "status": status,
        "mode": "dry_run_read_only",
        "write_performed": False,
        "run_id": config.run_id,
        "run_dir": str(run_dir),
        "base_url": config.base_url,
        "days": config.days,
        "operator_count": len(config.operator_ids),
        "operators": operator_summaries,
        "blocking_gap_count": len(blocking_gap_todos),
        "blocking_gap_todos": blocking_gap_todos,
        "collection_error_count": collection_error_count,
    }


def _repo_relative_path(path: Path, *, repo_root: Path = REPO_ROOT) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root.resolve()))
    except ValueError:
        return str(resolved)


def _basis_commit(*, repo_root: Path = REPO_ROOT) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # noqa: BLE001 - manifest is still useful without git.
        return "unknown"
    return result.stdout.strip() or "unknown"


def _date_from_text(value: str | None) -> str | None:
    if not value:
        return None
    match = DATE_PATTERN.search(value)
    if not match:
        return None
    if match.group("date"):
        return match.group("date")
    compact = match.group("compact")
    try:
        return datetime.strptime(compact, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _run_date(run_dir: Path, run_id: str | None) -> str | None:
    return (
        _date_from_text(run_dir.parent.name)
        or _date_from_text(run_id)
        or _date_from_text(run_dir.name)
    )


def _evidence_window(
    *, required_days: int, run_date: str | None, counted: bool
) -> dict[str, Any]:
    start_date = None
    if run_date:
        end_date = datetime.strptime(run_date, "%Y-%m-%d").date()
        start_date = (end_date - timedelta(days=required_days - 1)).isoformat()
    return {
        "start_date": start_date,
        "end_date": run_date,
        "required_days": required_days,
        "observed_days": 1,
        "counted_days": 1 if counted else 0,
        "timezone": "Asia/Seoul",
    }


def _operator_raw_path(
    *,
    run_dir: Path,
    operator_summary: dict[str, Any],
    key: str,
    repo_root: Path = REPO_ROOT,
) -> str | None:
    raw_file = (operator_summary.get("raw_files") or {}).get(key)
    if not raw_file:
        return None
    return _repo_relative_path(run_dir / raw_file, repo_root=repo_root)


def _read_operator_payload(
    *, run_dir: Path, operator_summary: dict[str, Any], key: str
) -> dict[str, Any]:
    raw_file = (operator_summary.get("raw_files") or {}).get(key)
    if not raw_file or raw_file.endswith(".error.json"):
        return {}
    try:
        payload = json.loads((run_dir / raw_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _endpoint_has_error(operator_summary: dict[str, Any], key: str) -> bool:
    endpoint_path = ENDPOINT_PATHS_BY_KEY[key]
    return any(
        item.get("endpoint") == endpoint_path
        for item in operator_summary.get("collection_errors") or []
        if isinstance(item, dict)
    )


def _endpoint_scope_status(operator_summary: dict[str, Any], key: str) -> str:
    if _endpoint_has_error(operator_summary, key):
        return "missing"
    raw_file = (operator_summary.get("raw_files") or {}).get(key)
    if not raw_file or raw_file.endswith(".error.json"):
        return "missing"
    current_ids = operator_summary.get("current_operator_ids_by_endpoint") or {}
    if key not in current_ids:
        return "missing"
    if _optional_int(current_ids.get(key)) != operator_summary.get("operator_id"):
        return "mixed_scope"
    return "pass"


def _profile_status(operator_summary: dict[str, Any]) -> str:
    endpoint_status = _endpoint_scope_status(operator_summary, "profile")
    if endpoint_status != "pass":
        return endpoint_status
    if operator_summary.get("profile_configured") is True:
        return "pass"
    if operator_summary.get("profile_configured") is False:
        return "fail"
    return "missing"


def _strategy_status(operator_summary: dict[str, Any]) -> str:
    endpoint_status = _endpoint_scope_status(operator_summary, "strategy")
    if endpoint_status != "pass":
        return endpoint_status
    if operator_summary.get("strategy_configured") is True:
        return "pass"
    if operator_summary.get("strategy_configured") is False:
        return "fail"
    return "missing"


def _notification_status(operator_summary: dict[str, Any]) -> str:
    endpoint_status = _endpoint_scope_status(operator_summary, "notification_channels")
    if endpoint_status != "pass":
        return endpoint_status
    mode = operator_summary.get("notification_channel_status")
    if mode in {"active", "dry_run_only", "skipped"}:
        return "pass"
    if mode == "missing":
        return "missing"
    return "fail"


def _operator_scope_status(operator_summary: dict[str, Any]) -> str:
    if operator_summary.get("mismatched_current_operator_ids"):
        return "mixed_scope"
    if operator_summary.get("current_operator_id_matches") is True:
        return "pass"
    if operator_summary.get("collection_errors"):
        return "missing"
    return "missing"


def _gap_category(description: str) -> str:
    lowered = description.lower()
    if any(term in lowered for term in ("mixed", "canonical", "scope", "mismatch")):
        return "mixed data"
    if any(term in lowered for term in ("telegram", "notification", "app")):
        return "Telegram/app notification"
    if any(
        term in lowered for term in ("credential", "token", "secret", "401", "403")
    ):
        return "credential"
    if any(term in lowered for term in ("koneps", "crawl", "schema", "response")):
        return "KONEPS response"
    if any(
        term in lowered for term in ("celery", "task", "broker", "worker", "queue")
    ):
        return "task/broker"
    if any(term in lowered for term in ("no candidate", "candidate", "공고", "후보")):
        return "no candidates"
    return "missing evidence"


def _gap_treatment(category: str) -> str:
    if category == "mixed data":
        return "documented_not_counted"
    return "rerun"


def _blocking_gap_entries(
    *, operator_summaries: list[dict[str, Any]], run_date: str | None
) -> tuple[list[dict[str, Any]], dict[int, list[str]]]:
    entries: list[dict[str, Any]] = []
    ids_by_operator: dict[int, list[str]] = {}
    for operator_summary in operator_summaries:
        operator_id = int(operator_summary["operator_id"])
        for gap in operator_summary.get("blocking_gaps") or []:
            description = str(gap)
            gap_id = f"GAP-{len(entries) + 1:03d}"
            category = _gap_category(description)
            entry = {
                "gap_id": gap_id,
                "date": _date_from_text(description) or run_date,
                "operator_id": operator_id,
                "source": "g2-evidence.blocking_gaps",
                "category": category,
                "description": description,
                "status": "open",
                "treatment": _gap_treatment(category),
            }
            entries.append(entry)
            ids_by_operator.setdefault(operator_id, []).append(gap_id)
    return entries, ids_by_operator


def _build_manifest_operator(
    *,
    run_dir: Path,
    operator_summary: dict[str, Any],
    blocking_gap_ids: list[str],
) -> dict[str, Any]:
    operator_id = int(operator_summary["operator_id"])
    profile_payload = _read_operator_payload(
        run_dir=run_dir, operator_summary=operator_summary, key="profile"
    )
    username = (
        operator_summary.get("current_operator_username")
        or profile_payload.get("current_operator_username")
        or profile_payload.get("username")
    )
    company = profile_payload.get("company") or profile_payload.get("company_name")
    profile_status = _profile_status(operator_summary)
    strategy_status = _strategy_status(operator_summary)
    notification_status = _notification_status(operator_summary)
    g2_status = operator_summary.get("evidence_status") or "missing"
    return {
        "operator_id": operator_id,
        "username": username,
        "company": company,
        "is_synthetic": bool(
            isinstance(username, str) and username.startswith("synthetic-")
        ),
        "operator_scope_status": _operator_scope_status(operator_summary),
        "profile": {
            "status": profile_status,
            "path": _operator_raw_path(
                run_dir=run_dir, operator_summary=operator_summary, key="profile"
            ),
            "required_fields_present": profile_status == "pass",
        },
        "strategy": {
            "status": strategy_status,
            "path": _operator_raw_path(
                run_dir=run_dir, operator_summary=operator_summary, key="strategy"
            ),
            "thresholds_valid": strategy_status == "pass",
        },
        "notification_channel": {
            "status": notification_status,
            "mode": operator_summary.get("notification_channel_status") or "missing",
            "path": _operator_raw_path(
                run_dir=run_dir,
                operator_summary=operator_summary,
                key="notification_channels",
            ),
            "masked_target_present": (
                operator_summary.get("notification_channel_count", 0) > 0
            ),
            "raw_secret_absent": None,
        },
        "evidence_paths": {
            "g2_evidence": [
                path
                for path in [
                    _operator_raw_path(
                        run_dir=run_dir,
                        operator_summary=operator_summary,
                        key="g2_evidence",
                    )
                ]
                if path
            ],
            "candidate_preview": [
                path
                for path in [
                    _operator_raw_path(
                        run_dir=run_dir,
                        operator_summary=operator_summary,
                        key="strategy_candidates",
                    )
                ]
                if path
            ],
            "strategy_monitor": [],
            "decision_experiments": [
                path
                for path in [
                    _operator_raw_path(
                        run_dir=run_dir,
                        operator_summary=operator_summary,
                        key="decision_experiments",
                    )
                ]
                if path
            ],
            "decision_apply_dry_run": [],
            "operations_dashboard": [
                path
                for path in [
                    _operator_raw_path(
                        run_dir=run_dir,
                        operator_summary=operator_summary,
                        key="operator_dashboard",
                    ),
                    _operator_raw_path(
                        run_dir=run_dir,
                        operator_summary=operator_summary,
                        key="operations_dashboard",
                    ),
                ]
                if path
            ],
        },
        "_daily_status": {
            "profile": profile_status,
            "strategy": strategy_status,
            "notification_channel": notification_status,
            "candidate_preview": _endpoint_scope_status(
                operator_summary,
                "strategy_candidates",
            ),
            "strategy_monitor": "missing",
            "decision_experiment": _endpoint_scope_status(
                operator_summary,
                "decision_experiments",
            ),
            "g2_evidence_status": g2_status,
            "blocking_gap_ids": blocking_gap_ids,
        },
    }


def _daily_status_value(
    *,
    summary: dict[str, Any],
    manifest_operators: list[dict[str, Any]],
) -> str:
    if summary.get("collection_error_count", 0) > 0:
        return "fail"
    all_collected_evidence_passed = (
        summary.get("operator_count", 0) >= 3
        and summary.get("blocking_gap_count", 0) == 0
        and all(
            operator.get("operator_scope_status") == "pass"
            and operator.get("profile", {}).get("status") == "pass"
            and operator.get("strategy", {}).get("status") == "pass"
            and operator.get("notification_channel", {}).get("status") == "pass"
            and operator.get("_daily_status", {}).get("candidate_preview") == "pass"
            and operator.get("_daily_status", {}).get("decision_experiment") == "pass"
            and operator.get("_daily_status", {}).get("g2_evidence_status") == "ready"
            for operator in manifest_operators
        )
    )
    return "pass" if all_collected_evidence_passed else "partial"


def build_manifest_draft(
    *,
    config: CollectionConfig,
    run_dir: Path,
    summary: dict[str, Any],
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    run_date = _run_date(run_dir, config.run_id)
    blocking_gaps, gap_ids_by_operator = _blocking_gap_entries(
        operator_summaries=summary.get("operators") or [],
        run_date=run_date,
    )
    manifest_operators = [
        _build_manifest_operator(
            run_dir=run_dir,
            operator_summary=operator_summary,
            blocking_gap_ids=gap_ids_by_operator.get(
                int(operator_summary["operator_id"]), []
            ),
        )
        for operator_summary in summary.get("operators") or []
    ]
    daily_status = _daily_status_value(
        summary=summary, manifest_operators=manifest_operators
    )
    summary_path = run_dir / "g2-evidence-summary.json"
    manifest = {
        "review_id": f"g2-exit-draft-{config.run_id}",
        "manifest_version": 1,
        "status": "draft",
        "basis": {
            "roadmap": "docs/roadmap.md",
            "runbook": "docs/operations/g2-evidence-runbook.md",
            "review_template": "docs/operations/g2-exit-review-template.md",
            "basis_commit": _basis_commit(repo_root=repo_root),
        },
        "evidence_window": _evidence_window(
            required_days=config.days,
            run_date=run_date,
            counted=daily_status == "pass",
        ),
        "operators": [
            {key: value for key, value in operator.items() if key != "_daily_status"}
            for operator in manifest_operators
        ],
        "daily_status": [
            {
                "date": run_date,
                "status": daily_status,
                "summary": f"collect_g2_evidence snapshot status={summary.get('status')}",
                "collect_g2_evidence_snapshot": {
                    "status": "pass" if summary.get("status") == "ready" else "fail",
                    "path": _repo_relative_path(summary_path, repo_root=repo_root),
                    "source": "scripts/collect_g2_evidence.py",
                },
                "operators": {
                    str(operator["operator_id"]): operator["_daily_status"]
                    for operator in manifest_operators
                },
                "dry_run_item_ids": [],
                "approved_execution_item_ids": [],
                "excluded_evidence": [],
            }
        ],
        "blocking_gaps": blocking_gaps,
        "action_register": {
            "dry_run_items": [],
            "approved_execution_items": [],
        },
    }
    return manifest


def _daily_worklog_file_paths(operator_summary: dict[str, Any]) -> dict[str, str]:
    raw_files = operator_summary.get("raw_files") or {}
    return {
        endpoint.key: raw_files[endpoint.key]
        for endpoint in ENDPOINTS
        if endpoint.key in raw_files
    }


def _daily_worklog_next_actions(
    *, operator_summaries: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for operator_summary in operator_summaries:
        operator_id = int(operator_summary["operator_id"])
        file_paths = _daily_worklog_file_paths(operator_summary)

        for gap in operator_summary.get("blocking_gaps") or []:
            actions.append(
                {
                    "kind": "blocking_gap",
                    "operator_id": operator_id,
                    "endpoint_key": "g2_evidence",
                    "description": str(gap),
                    "action": (
                        "Resolve the blocking gap and rerun G-2 evidence collection."
                    ),
                }
            )

        for error_item in operator_summary.get("collection_errors") or []:
            if not isinstance(error_item, dict):
                continue
            endpoint = str(error_item.get("endpoint") or "")
            endpoint_key = ENDPOINT_KEYS_BY_PATH.get(endpoint, endpoint)
            actions.append(
                {
                    "kind": "collection_error",
                    "operator_id": operator_id,
                    "endpoint_key": endpoint_key,
                    "endpoint": endpoint,
                    "path": file_paths.get(endpoint_key),
                    "error": str(error_item.get("error") or ""),
                    "action": (
                        "Fix the endpoint collection error and rerun "
                        "G-2 evidence collection."
                    ),
                }
            )

        for endpoint in ENDPOINTS:
            path = file_paths.get(endpoint.key)
            if path and not path.endswith(".error.json"):
                continue
            actions.append(
                {
                    "kind": "missing_endpoint",
                    "operator_id": operator_id,
                    "endpoint_key": endpoint.key,
                    "path": path,
                    "action": (
                        "Collect the missing endpoint evidence and rerun "
                        "G-2 evidence collection."
                    ),
                }
            )
    return actions


def build_daily_worklog(
    *,
    config: CollectionConfig,
    summary: dict[str, Any],
) -> dict[str, Any]:
    operator_summaries = summary.get("operators") or []
    return {
        "worklog_version": 1,
        "run_id": config.run_id,
        "operator_count": len(operator_summaries),
        "write_performed": False,
        "endpoint_keys": [endpoint.key for endpoint in ENDPOINTS],
        "operators": [
            {
                "operator_id": int(operator_summary["operator_id"]),
                "file_paths": _daily_worklog_file_paths(operator_summary),
            }
            for operator_summary in operator_summaries
        ],
        "next_actions": _daily_worklog_next_actions(
            operator_summaries=operator_summaries
        ),
    }


def run_collection(
    config: CollectionConfig,
    *,
    http_get_json_func: HttpGetJson = http_get_json,
) -> dict[str, Any]:
    run_id = config.run_id or _default_run_id()
    effective_config = CollectionConfig(
        base_url=config.base_url,
        token=config.token,
        operator_ids=config.operator_ids,
        evidence_dir=config.evidence_dir,
        days=config.days,
        fail_on_blocking_gaps=config.fail_on_blocking_gaps,
        timeout_seconds=config.timeout_seconds,
        run_id=run_id,
    )
    run_dir = config.evidence_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    operator_summaries = [
        collect_operator_evidence(
            config=effective_config,
            run_dir=run_dir,
            operator_id=operator_id,
            http_get_json_func=http_get_json_func,
        )
        for operator_id in effective_config.operator_ids
    ]
    summary = build_collection_summary(
        config=effective_config,
        run_dir=run_dir,
        operator_summaries=operator_summaries,
    )
    write_json(run_dir / "g2-evidence-summary.json", summary)
    write_json(
        run_dir / "run-metadata.json",
        {
            "run_id": effective_config.run_id,
            "base_url": effective_config.base_url,
            "days": effective_config.days,
            "operator_ids": effective_config.operator_ids,
            "mode": "dry_run_read_only",
            "write_performed": False,
            "endpoints": [
                {"key": endpoint.key, "path": endpoint.path, "method": "GET"}
                for endpoint in ENDPOINTS
            ],
        },
    )
    write_json(
        run_dir / "manifest-draft.json",
        build_manifest_draft(
            config=effective_config,
            run_dir=run_dir,
            summary=summary,
        ),
    )
    write_json(
        run_dir / "daily-worklog.json",
        build_daily_worklog(config=effective_config, summary=summary),
    )
    return summary


def print_summary(summary: dict[str, Any], *, stdout: TextIO) -> None:
    stdout.write("G-2 evidence collection dry-run complete\n")
    stdout.write(f"run_dir: {summary['run_dir']}\n")
    stdout.write(
        f"operators: {summary['operator_count']} days={summary['days']} "
        f"status={summary['status']}\n"
    )
    for item in summary.get("operators", []):
        match_label = "match" if item.get("current_operator_id_matches") else "mismatch"
        gaps = item.get("blocking_gaps") or []
        errors = item.get("collection_errors") or []
        stdout.write(
            "operator "
            f"{item.get('operator_id')}: evidence={item.get('evidence_status')} "
            f"gaps={len(gaps)} current_operator_id={match_label} "
            f"profile_configured={item.get('profile_configured')} "
            f"strategy_configured={item.get('strategy_configured')} "
            f"channels={item.get('notification_channel_count')} "
            f"channel_status={item.get('notification_channel_status')}"
        )
        if errors:
            stdout.write(f" errors={len(errors)}")
        stdout.write("\n")
        for index, gap in enumerate(gaps, start=1):
            stdout.write(f"  TODO {index}: {gap}\n")
        for error_item in errors:
            stdout.write(
                f"  ERROR {error_item.get('endpoint')}: {error_item.get('error')}\n"
            )
    stdout.write(f"summary_file: {summary['run_dir']}/g2-evidence-summary.json\n")


def exit_code_for_summary(
    summary: dict[str, Any], *, fail_on_blocking_gaps: bool
) -> int:
    if summary.get("collection_error_count", 0) > 0:
        return 1
    if fail_on_blocking_gaps and summary.get("blocking_gap_count", 0) > 0:
        return 3
    return 0


def main(
    argv: list[str] | None = None,
    *,
    http_get_json_func: HttpGetJson = http_get_json,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    try:
        config = config_from_args(parser.parse_args(argv))
        summary = run_collection(config, http_get_json_func=http_get_json_func)
        print_summary(summary, stdout=stdout)
        code = exit_code_for_summary(
            summary,
            fail_on_blocking_gaps=config.fail_on_blocking_gaps,
        )
        if code == 3:
            stderr.write(
                "blocking_gaps found; failing because "
                "--fail-on-blocking-gaps was set\n"
            )
        return code
    except UsageError as exc:
        stderr.write(f"{exc}\n")
        return 2
    except EvidenceCollectionError as exc:
        stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
