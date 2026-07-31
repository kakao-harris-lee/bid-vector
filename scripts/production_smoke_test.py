#!/usr/bin/env python3
"""Small production smoke-test runner for bid-vector.

Default mode skips crawl/monitor write checks. Pass --write to execute KONEPS
crawl and strategy monitoring, which persist records and may trigger Telegram
notifications.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
from urllib import error as urlerror
from urllib import parse, request

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.smoke_failure_taxonomy import classify_failure  # noqa: E402


class SmokeFailure(Exception):
    """Raised when a required smoke-test step fails."""


FAILURE_GUIDANCE: dict[str, dict[str, str]] = {
    "credential": {
        "action_required": "Rotate or restore the missing API/Telegram credential.",
        "retry_method": "Fix the credential, then rerun this smoke command.",
    },
    "koneps_response": {
        "action_required": "Check KONEPS OpenAPI availability and request parameters.",
        "retry_method": "Retry with `--write --max-items 3` after KONEPS responds normally.",
    },
    "candidate_generation": {
        "action_required": "Inspect the strategy monitor run and candidate filters.",
        "retry_method": "Rerun with `--write --monitor-all-candidates` to widen the monitor check.",
    },
    "no_candidate": {
        "action_required": "Confirm whether no active notices match the current strategy.",
        "retry_method": "Widen filters with `--monitor-all-candidates`; no retry is required if the skip reason is expected.",
    },
    "telegram": {
        "action_required": "Check bot token, chat id, and whether the operator started the bot conversation.",
        "retry_method": "Fix Telegram configuration, then rerun with `--write --telegram-sync`.",
    },
    "task_broker": {
        "action_required": "Check Celery broker/backend and worker health.",
        "retry_method": "Repair broker/workers, then rerun this smoke command.",
    },
    "db_schema": {
        "action_required": "Apply pending migrations and verify production schema.",
        "retry_method": "Run migrations, restart services, then rerun this smoke command.",
    },
    "prediction": {
        "action_required": "Check embedding and price prediction dependencies plus recent project data.",
        "retry_method": "Repair model/data issues, then rerun this smoke command.",
    },
    "unknown": {
        "action_required": "Inspect the step error and application logs.",
        "retry_method": "Rerun the same command after the logged root cause is corrected.",
    },
}


def failure_guidance(category: str) -> dict[str, str]:
    return FAILURE_GUIDANCE.get(category) or FAILURE_GUIDANCE["unknown"]


def parse_bool_env(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    normalized_base = base_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    url = f"{normalized_base}{normalized_path}"
    if params:
        clean_params = {key: value for key, value in params.items() if value is not None}
        if clean_params:
            url = f"{url}?{parse.urlencode(clean_params)}"
    return url


def request_json(
    *,
    base_url: str,
    path: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_seconds: float = 20.0,
    bearer_token: str | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    data = None
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = request.Request(
        build_url(base_url, path, params),
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
            status_code = response.status
    except urlerror.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        detail = parse_json_or_text(raw_body)
        raise SmokeFailure(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
    except urlerror.URLError as exc:
        raise SmokeFailure(f"{method} {path} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SmokeFailure(f"{method} {path} timed out after {timeout_seconds}s") from exc

    payload = parse_json_or_text(raw_body)
    if not isinstance(payload, dict):
        payload = {"raw": payload}
    return status_code, payload


def parse_json_or_text(raw_body: str) -> Any:
    if not raw_body:
        return {}
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError:
        return raw_body


def require_keys(payload: dict[str, Any], keys: set[str], *, name: str) -> None:
    missing = keys - set(payload)
    if missing:
        raise SmokeFailure(f"{name} missing required key(s): {', '.join(sorted(missing))}")


_MONITOR_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def poll_monitor_result(call, poll_url: str, *, attempts: int, interval_seconds: float) -> dict[str, Any]:
    """202 async monitor 의 task-status 를 terminal 까지 폴링해 result 를 반환한다.

    설계 §6.2: POST /strategy/monitor 가 인라인 실행에서 async 위임(202)으로
    바뀌어, 스모크 write-check 는 kickoff 후 task-status 를 폴링해 완료 결과를
    읽는다. eager(테스트) 환경은 첫 폴에서 이미 completed 다.
    """
    last: dict[str, Any] = {}
    for attempt in range(max(1, attempts)):
        last = call(poll_url)
        status_value = str(last.get("status") or "")
        if status_value in _MONITOR_TERMINAL_STATUSES:
            if status_value != "completed":
                raise SmokeFailure(
                    f"strategy monitor task ended status={status_value}: "
                    f"{last.get('error') or last.get('detail')}"
                )
            result = last.get("result")
            if not isinstance(result, dict):
                raise SmokeFailure("strategy monitor completed without a result payload")
            return result
        if attempt + 1 < max(1, attempts):
            time.sleep(interval_seconds)
    raise SmokeFailure(
        f"strategy monitor task did not finish after {attempts} polls "
        f"(last status={last.get('status')})"
    )


def card_status_summary(cards: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(card.get("key", "unknown")): str(card.get("status", "unknown"))
        for card in cards
        if isinstance(card, dict)
    }


def build_client(args: argparse.Namespace):
    def call(
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _, payload = request_json(
            base_url=args.base_url,
            path=path,
            method=method,
            body=body,
            params=params,
            timeout_seconds=args.timeout_seconds,
            bearer_token=args.bearer_token,
        )
        return payload

    return call


def run_step(
    evidence: dict[str, Any],
    name: str,
    func,
    *,
    required: bool = True,
) -> None:
    print(f"==> {name}")
    record: dict[str, Any] = {"name": name, "required": required}
    try:
        result = func()
    except Exception as exc:
        category = classify_failure(name, str(exc))
        guidance = failure_guidance(category)
        record.update({
            "status": "failed",
            "error": str(exc),
            "failure_category": category,
            "action_required": guidance["action_required"],
            "retry_method": guidance["retry_method"],
        })
        evidence["steps"].append(record)
        prefix = "[fail]" if required else "[warn]"
        print(f"{prefix} {name}: {exc}")
        if required:
            raise
        return

    record.update({"status": "passed", **result})
    evidence["steps"].append(record)
    summary = result.get("summary")
    if summary:
        print(f"[ok] {summary}")
    else:
        print("[ok]")


def smoke_read_checks(args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    call = build_client(args)

    def health() -> dict[str, Any]:
        payload = call("/health")
        if payload.get("status") != "healthy":
            raise SmokeFailure(f"unexpected health payload: {payload}")
        return {"summary": f"service={payload.get('service')} status={payload.get('status')}", "payload": payload}

    def operator_profile() -> dict[str, Any]:
        payload = call("/api/v1/operator/profile")
        require_keys(payload, {"operator_id", "profile_configured"}, name="operator profile")
        return {
            "summary": (
                f"operator_id={payload.get('operator_id')} "
                f"profile_configured={payload.get('profile_configured')}"
            ),
            "payload": payload,
        }

    def operator_strategy() -> dict[str, Any]:
        payload = call("/api/v1/operator/strategy")
        require_keys(
            payload,
            {"operator_id", "strategy_configured", "notify_only_high_priority", "max_recommended_candidates"},
            name="operator strategy",
        )
        return {
            "summary": (
                f"strategy_configured={payload.get('strategy_configured')} "
                f"high_priority_only={payload.get('notify_only_high_priority')} "
                f"limit={payload.get('max_recommended_candidates')}"
            ),
            "payload": payload,
        }

    def operator_dashboard() -> dict[str, Any]:
        payload = call(
            "/api/v1/operator/dashboard",
            params={"days": args.days, "limit": args.recent_limit},
        )
        require_keys(
            payload,
            {
                "cards",
                "recent_decisions",
                "recent_monitor_runs",
                "feedback_summary",
                "action_hrefs",
            },
            name="operator dashboard",
        )
        required_hrefs = {
            "opportunity_analysis",
            "decision_list",
            "strategy_candidates",
            "strategy_monitor",
            "strategy_monitor_runs",
            "prediction_feedback",
            "operations_dashboard",
        }
        missing_hrefs = required_hrefs - set(payload.get("action_hrefs") or {})
        if missing_hrefs:
            raise SmokeFailure(f"operator dashboard missing action_hrefs: {sorted(missing_hrefs)}")
        return {
            "summary": (
                f"cards={len(payload.get('cards') or [])} "
                f"decisions={len(payload.get('recent_decisions') or [])} "
                f"runs={len(payload.get('recent_monitor_runs') or [])}"
            ),
            "payload": payload,
        }

    def operations_dashboard() -> dict[str, Any]:
        payload = call(
            "/api/v1/analytics/operations-dashboard",
            params={"days": args.days, "recent_limit": args.recent_limit},
        )
        require_keys(
            payload,
            {"crawl", "strategy", "tasks", "notifications", "ml_release", "smoke_test", "cards"},
            name="operations dashboard",
        )
        notifications = payload.get("notifications") or {}
        tasks = payload.get("tasks") or {}
        return {
            "summary": (
                f"cards={card_status_summary(payload.get('cards') or [])} "
                f"telegram={notifications.get('telegram_status')} "
                f"task_backlog={tasks.get('backlog_status')}"
            ),
            "payload": payload,
        }

    def telegram_status() -> dict[str, Any]:
        payload = call("/api/v1/operations/telegram/status")
        require_keys(payload, {"configured", "pending_update_count", "known_chat_ids"}, name="telegram status")
        return {
            "summary": (
                f"configured={payload.get('configured')} "
                f"pending_updates={payload.get('pending_update_count')} "
                f"known_chat_ids={len(payload.get('known_chat_ids') or [])}"
            ),
            "payload": payload,
        }

    def strategy_candidates() -> dict[str, Any]:
        payload = call(
            "/api/v1/operator/strategy/candidates",
            params={
                "limit": args.monitor_limit,
                "high_priority_only": str(args.candidate_high_priority_only).lower(),
            },
        )
        require_keys(
            payload,
            {"evaluated_project_count", "returned_candidate_count", "candidates"},
            name="strategy candidates",
        )
        return {
            "summary": (
                f"evaluated={payload.get('evaluated_project_count')} "
                f"returned={payload.get('returned_candidate_count')}"
            ),
            "payload": payload,
        }

    run_step(evidence, "health", health)
    run_step(evidence, "operator profile", operator_profile)
    run_step(evidence, "operator strategy", operator_strategy)
    run_step(evidence, "operator dashboard contract", operator_dashboard)
    run_step(evidence, "operations dashboard", operations_dashboard)
    run_step(evidence, "telegram status", telegram_status, required=False)
    if not args.skip_candidates:
        run_step(evidence, "strategy candidate preview", strategy_candidates, required=False)


def smoke_write_checks(args: argparse.Namespace, evidence: dict[str, Any]) -> None:
    call = build_client(args)

    def koneps_crawl() -> dict[str, Any]:
        body = {
            "source": args.crawl_source,
            "category": args.crawl_category,
            "target_date": args.target_date,
            "keyword": args.keyword,
            "execution_mode": args.execution_mode,
            "max_items": args.max_items,
        }
        payload = call("/api/v1/operations/crawl", method="POST", body=body)
        require_keys(payload, {"job_status", "source", "collected_count", "metadata"}, name="crawl")
        metadata = payload.get("metadata") or {}
        return {
            "summary": (
                f"source={payload.get('source')} status={payload.get('job_status')} "
                f"collected={payload.get('collected_count')} crawl_job_id={metadata.get('crawl_job_id')}"
            ),
            "payload": payload,
        }

    def strategy_monitor() -> dict[str, Any]:
        body = {
            "limit": args.monitor_limit,
            "high_priority_only": args.monitor_high_priority_only,
            "max_active_bids": args.max_active_bids,
            "current_workload_score": args.current_workload_score,
            "same_category_only": args.same_category_only,
            "similar_limit": args.similar_limit,
            "min_similarity": args.min_similarity,
        }
        # POST /strategy/monitor 는 이제 202 async envelope 을 반환한다(설계 §6.2:
        # 요청 경로 인라인 ML 폐쇄). write-check 는 kickoff 후 결과를 폴링해 읽는다.
        kickoff = call("/api/v1/operator/strategy/monitor", method="POST", body=body)
        require_keys(kickoff, {"task_id", "monitor_run_id", "poll_url"}, name="strategy monitor kickoff")
        payload = poll_monitor_result(
            call,
            kickoff["poll_url"],
            attempts=args.monitor_poll_attempts,
            interval_seconds=args.monitor_poll_interval_seconds,
        )
        require_keys(payload, {"monitor_run_id", "persisted_candidate_count", "notification_count"}, name="strategy monitor")
        monitor_run_id = payload.get("monitor_run_id")
        detail = None
        if monitor_run_id:
            detail = call(f"/api/v1/operator/strategy/monitor/runs/{monitor_run_id}")
        skip_reason = None
        if int(payload.get("notification_count") or 0) == 0:
            if int(payload.get("selected_candidate_count") or 0) == 0:
                skip_reason = "no strategy candidates selected"
            elif int(payload.get("persisted_candidate_count") or 0) == 0:
                skip_reason = "selected candidates already persisted or skipped"
            else:
                skip_reason = "no new notification created"
        return {
            "summary": (
                f"run_id={monitor_run_id} persisted={payload.get('persisted_candidate_count')} "
                f"notifications={payload.get('notification_count')} new={payload.get('new_candidate_count')}"
            ),
            "skip_reason": skip_reason,
            "payload": {"result": payload, "detail": detail},
        }

    def telegram_sync() -> dict[str, Any]:
        payload = call(
            "/api/v1/operations/telegram/sync",
            method="POST",
            params={"limit": args.telegram_sync_limit, "timeout_seconds": args.telegram_sync_timeout},
        )
        require_keys(payload, {"status", "processed_count", "processed_update_ids"}, name="telegram sync")
        return {
            "summary": (
                f"status={payload.get('status')} processed={payload.get('processed_count')} "
                f"known_chat_ids={payload.get('known_chat_ids')}"
            ),
            "payload": payload,
        }

    if not args.skip_crawl:
        run_step(evidence, "KONEPS crawl write check", koneps_crawl)
    if not args.skip_monitor:
        run_step(evidence, "strategy monitor write check", strategy_monitor)
    if args.telegram_sync:
        run_step(evidence, "telegram sync", telegram_sync, required=False)


def write_evidence(path: str, evidence: dict[str, Any]) -> None:
    evidence_path = Path(path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"[evidence] wrote {evidence_path}")


def _add_monitor_poll_arguments(parser: argparse.ArgumentParser) -> None:
    """202 async monitor 폴링 인자 (설계 §6.2: monitor kickoff 후 task-status 폴)."""
    parser.add_argument("--monitor-poll-attempts", type=int, default=int(os.getenv("SMOKE_MONITOR_POLL_ATTEMPTS", "30")))
    parser.add_argument(
        "--monitor-poll-interval-seconds",
        type=float,
        default=float(os.getenv("SMOKE_MONITOR_POLL_INTERVAL_SECONDS", "2.0")),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run production smoke checks against a bid-vector API.")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL") or os.getenv("BASE_URL") or "http://localhost:3000")
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("SMOKE_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--bearer-token", default=os.getenv("SMOKE_BEARER_TOKEN") or "")
    parser.add_argument("--days", type=int, default=int(os.getenv("SMOKE_DAYS", "1")))
    parser.add_argument("--recent-limit", type=int, default=int(os.getenv("SMOKE_RECENT_LIMIT", "3")))
    parser.add_argument("--skip-candidates", action="store_true", default=parse_bool_env(os.getenv("SMOKE_SKIP_CANDIDATES")))
    parser.add_argument(
        "--candidate-high-priority-only",
        action="store_true",
        default=parse_bool_env(os.getenv("SMOKE_CANDIDATE_HIGH_PRIORITY_ONLY")),
    )

    parser.add_argument("--write", action="store_true", default=parse_bool_env(os.getenv("SMOKE_WRITE")))
    parser.add_argument("--skip-crawl", action="store_true", default=parse_bool_env(os.getenv("SMOKE_SKIP_CRAWL")))
    parser.add_argument("--skip-monitor", action="store_true", default=parse_bool_env(os.getenv("SMOKE_SKIP_MONITOR")))
    parser.add_argument("--crawl-source", default=os.getenv("SMOKE_CRAWL_SOURCE", "koneps-openapi"))
    parser.add_argument("--crawl-category", default=os.getenv("SMOKE_CRAWL_CATEGORY", "general-service"))
    parser.add_argument("--target-date", default=os.getenv("SMOKE_TARGET_DATE") or datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--keyword", default=os.getenv("SMOKE_KEYWORD", "AI"))
    parser.add_argument("--execution-mode", choices=["mock", "live", "auto"], default=os.getenv("SMOKE_EXECUTION_MODE", "auto"))
    parser.add_argument("--max-items", type=int, default=int(os.getenv("SMOKE_MAX_ITEMS", "3")))
    parser.add_argument("--monitor-limit", type=int, default=int(os.getenv("SMOKE_MONITOR_LIMIT", "3")))
    _add_monitor_poll_arguments(parser)
    parser.add_argument(
        "--monitor-all-candidates",
        action="store_true",
        default=parse_bool_env(os.getenv("SMOKE_MONITOR_ALL_CANDIDATES")),
        help="Set strategy monitor high_priority_only=false.",
    )
    parser.add_argument("--max-active-bids", type=int, default=int(os.getenv("SMOKE_MAX_ACTIVE_BIDS", "3")))
    parser.add_argument("--current-workload-score", type=float, default=float(os.getenv("SMOKE_CURRENT_WORKLOAD_SCORE", "0.0")))
    parser.add_argument(
        "--all-categories-for-similarity",
        action="store_true",
        default=parse_bool_env(os.getenv("SMOKE_ALL_CATEGORIES_FOR_SIMILARITY")),
        help="Set same_category_only=false for strategy monitor.",
    )
    parser.add_argument("--similar-limit", type=int, default=int(os.getenv("SMOKE_SIMILAR_LIMIT", "3")))
    parser.add_argument("--min-similarity", type=float, default=float(os.getenv("SMOKE_MIN_SIMILARITY", "0.15")))
    parser.add_argument("--telegram-sync", action="store_true", default=parse_bool_env(os.getenv("SMOKE_TELEGRAM_SYNC")))
    parser.add_argument("--telegram-sync-limit", type=int, default=int(os.getenv("SMOKE_TELEGRAM_SYNC_LIMIT", "10")))
    parser.add_argument("--telegram-sync-timeout", type=int, default=int(os.getenv("SMOKE_TELEGRAM_SYNC_TIMEOUT", "0")))
    parser.add_argument("--evidence-out", default=os.getenv("SMOKE_EVIDENCE_OUT", ""))
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.base_url = args.base_url.rstrip("/")
    args.bearer_token = args.bearer_token or None
    args.monitor_high_priority_only = not args.monitor_all_candidates
    args.same_category_only = not args.all_categories_for_similarity

    evidence: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "write_mode": args.write,
        "steps": [],
    }

    print(f"[smoke] base_url={args.base_url} write_mode={args.write}")
    if not args.write:
        print("[smoke] crawl/monitor write checks are disabled. Pass --write to enable them.")

    try:
        smoke_read_checks(args, evidence)
        if args.write:
            smoke_write_checks(args, evidence)
    except Exception as exc:
        evidence["status"] = "failed"
        evidence["error"] = str(exc)
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        if args.evidence_out:
            write_evidence(args.evidence_out, evidence)
        print(f"[smoke] failed: {exc}", file=sys.stderr)
        return 1

    evidence["status"] = "passed"
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    if args.evidence_out:
        write_evidence(args.evidence_out, evidence)
    print("[smoke] passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
