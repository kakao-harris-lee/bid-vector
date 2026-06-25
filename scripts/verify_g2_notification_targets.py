#!/usr/bin/env python3
"""Verify G-2 notification channel evidence for target safety."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, TextIO


DEFAULT_OPERATOR_USERNAME = "operator"

_OPERATOR_PATH_RE = re.compile(r"^operator-(?P<operator_id>\d+)$")
_SAFE_OPERATOR_TELEGRAM_ROUTE_RE = re.compile(r"^operator:\d+:telegram:unconfigured$")
_SAFE_NAMED_ROUTE_RE = re.compile(r"^(?:telegram|app):[a-z][a-z0-9_-]{0,80}$")
_NUMERIC_TARGET_RE = re.compile(r"(?<!\d)-?\d{5,}(?!\d)")
_BRACKETED_TOKEN_RE = re.compile(r"(?i)(ExponentPushToken)\[([^\]]{8,})\]")
_KEYED_SECRET_RE = re.compile(
    r"(?i)\b(chat_id|device|device_token|target|token|secret)([=:]\s*)([^\s,;]{5,})"
)
_TOKENISH_TARGET_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9][A-Za-z0-9_.:-]{19,}[A-Za-z0-9])(?![A-Za-z0-9])"
)

_TARGET_FIELD_PARTS = (
    "target",
    "route",
    "chat",
    "token",
    "device",
    "webhook",
    "recipient",
)
_SKIP_POLICY_VALUES = {
    "skip",
    "skipped",
    "skipped_synthetic_operator",
    "skipped_non_canonical_operator",
    "telegram_route_non_canonical",
    "telegram_channel_dry_run",
    "telegram_channel_inactive",
}


class VerificationInputError(Exception):
    """Raised when local evidence inputs cannot be verified."""


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    path: Path
    message: str
    operator_id: int | None = None
    channel_index: int | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "path": str(self.path),
            "message": self.message,
        }
        if self.operator_id is not None:
            payload["operator_id"] = self.operator_id
        if self.channel_index is not None:
            payload["channel_index"] = self.channel_index
        if self.field is not None:
            payload["field"] = self.field
        return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify G-2 notification-channels.json target evidence."
    )
    parser.add_argument(
        "--evidence-root",
        required=True,
        type=Path,
        help="Directory to scan recursively for notification-channels.json files.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path where the JSON verification summary is written.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional path where a compact markdown table is written.",
    )
    parser.add_argument(
        "--allow-active-noncanonical",
        action="store_true",
        help=(
            "Downgrade active non-canonical Telegram routes from failure to warning."
        ),
    )
    return parser


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise VerificationInputError(f"failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise VerificationInputError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise VerificationInputError(f"{path} must contain a JSON object")
    return payload


def _notification_files(evidence_root: Path) -> list[Path]:
    if not evidence_root.exists() or not evidence_root.is_dir():
        raise VerificationInputError(f"evidence root does not exist: {evidence_root}")
    files = sorted(evidence_root.rglob("notification-channels.json"))
    if not files:
        raise VerificationInputError(
            f"no notification-channels.json files found under {evidence_root}"
        )
    return files


def _operator_id_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = _OPERATOR_PATH_RE.match(part)
        if match:
            return int(match.group("operator_id"))
    return None


def _is_canonical_operator(payload: dict[str, Any]) -> bool:
    marker = payload.get("is_canonical")
    if isinstance(marker, bool):
        return marker
    username = str(
        payload.get("current_operator_username")
        or payload.get("username")
        or ""
    )
    return username == DEFAULT_OPERATOR_USERNAME


def _channels(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("channels")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _channel_count(payload: dict[str, Any], channels: list[dict[str, Any]]) -> int:
    return _optional_int(payload.get("channel_count")) or len(channels)


def _has_skip_policy(mapping: dict[str, Any]) -> bool:
    for key in (
        "status",
        "delivery_status",
        "route_status",
        "send_policy",
        "delivery_policy",
        "notification_policy",
        "policy",
        "mode",
    ):
        value = mapping.get(key)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if (
            normalized in _SKIP_POLICY_VALUES
            or normalized.startswith("skipped_")
            or "skipped" in normalized
        ):
            return True
    detail = str(mapping.get("detail") or "").lower()
    return "skipped" in detail and "telegram" in detail


def _is_target_field(field: object) -> bool:
    name = str(field).lower()
    return any(part in name for part in _TARGET_FIELD_PARTS)


def _is_safe_route_key(value: str) -> bool:
    return (
        value == "telegram:legacy-configured-chat"
        or bool(_SAFE_OPERATOR_TELEGRAM_ROUTE_RE.match(value))
        or bool(_SAFE_NAMED_ROUTE_RE.match(value))
        or "*" in value
    )


def _contains_raw_secret_like_target(value: object, *, field: str) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if field == "route_key" and _is_safe_route_key(text):
        return False
    if any("*" not in match.group(0) for match in _NUMERIC_TARGET_RE.finditer(text)):
        return True
    for match in _KEYED_SECRET_RE.finditer(text):
        if "*" not in match.group(3):
            return True
    for match in _BRACKETED_TOKEN_RE.finditer(text):
        if "*" not in match.group(2):
            return True
    for match in _TOKENISH_TARGET_RE.finditer(text):
        token = match.group(1)
        if "*" not in token and not _is_safe_route_key(token):
            return True
    return False


def _mode(payload: dict[str, Any], channels: list[dict[str, Any]]) -> str:
    channel_count = _channel_count(payload, channels)
    if _has_skip_policy(payload):
        return "skipped"
    if channel_count == 0:
        return "missing"
    active_count = sum(1 for channel in channels if channel.get("is_active") is True)
    dry_run_only_count = sum(
        1
        for channel in channels
        if channel.get("is_active") is True
        and channel.get("dry_run_only") is True
    )
    if active_count == 0:
        return "inactive"
    if dry_run_only_count >= active_count:
        return "dry_run_only"
    return "active"


def _row_status(issues: list[Issue]) -> str:
    if any(issue.severity == "failure" for issue in issues):
        return "fail"
    if any(issue.severity == "warning" for issue in issues):
        return "warning"
    return "pass"


def _issue(
    code: str,
    *,
    severity: str = "failure",
    path: Path,
    message: str,
    operator_id: int | None = None,
    channel_index: int | None = None,
    field: str | None = None,
) -> Issue:
    return Issue(
        code=code,
        severity=severity,
        path=path,
        message=message,
        operator_id=operator_id,
        channel_index=channel_index,
        field=field,
    )


def _verify_file(
    path: Path, *, allow_active_noncanonical: bool
) -> tuple[dict[str, Any], list[Issue]]:
    payload = _read_json_object(path)
    channels = _channels(payload)
    operator_id = _optional_int(payload.get("operator_id"))
    current_operator_id = _optional_int(payload.get("current_operator_id"))
    path_operator_id = _operator_id_from_path(path)
    username = str(payload.get("current_operator_username") or "")
    canonical = _is_canonical_operator(payload)
    channel_count = _channel_count(payload, channels)
    active_count = sum(1 for channel in channels if channel.get("is_active") is True)
    dry_run_only_count = sum(
        1
        for channel in channels
        if channel.get("is_active") is True
        and channel.get("dry_run_only") is True
    )
    issues: list[Issue] = []

    if (
        operator_id is not None
        and current_operator_id is not None
        and operator_id != current_operator_id
    ):
        issues.append(
            _issue(
                "operator_mismatch",
                path=path,
                operator_id=operator_id,
                message=(
                    f"operator_id {operator_id} does not match "
                    f"current_operator_id {current_operator_id}"
                ),
            )
        )
    if (
        operator_id is not None
        and path_operator_id is not None
        and operator_id != path_operator_id
    ):
        issues.append(
            _issue(
                "operator_mismatch",
                path=path,
                operator_id=operator_id,
                message=(
                    f"operator_id {operator_id} does not match path operator "
                    f"{path_operator_id}"
                ),
            )
        )

    for field, value in payload.items():
        if field == "channels" or not _is_target_field(field):
            continue
        if _contains_raw_secret_like_target(value, field=str(field)):
            issues.append(
                _issue(
                    "raw_secret_like_target",
                    path=path,
                    operator_id=operator_id,
                    field=str(field),
                    message=f"{field} appears to contain an unmasked target",
                )
            )

    if channel_count == 0:
        if not _has_skip_policy(payload):
            issues.append(
                _issue(
                    "missing_channels",
                    path=path,
                    operator_id=operator_id,
                    message="notification evidence has no channels and no skip policy",
                )
            )
            if not canonical:
                issues.append(
                    _issue(
                        "missing_dry_run_or_skip_policy",
                        path=path,
                        operator_id=operator_id,
                        message=(
                            "non-canonical notification evidence has no channel "
                            "and no dry-run/skip policy"
                        ),
                    )
                )

    for index, channel in enumerate(channels):
        channel_operator_id = _optional_int(channel.get("operator_id"))
        if (
            operator_id is not None
            and channel_operator_id is not None
            and operator_id != channel_operator_id
        ):
            issues.append(
                _issue(
                    "operator_mismatch",
                    path=path,
                    operator_id=operator_id,
                    channel_index=index,
                    field="operator_id",
                    message=(
                        f"channel operator_id {channel_operator_id} does not "
                        f"match file operator_id {operator_id}"
                    ),
                )
            )

        for field, value in channel.items():
            if not _is_target_field(field):
                continue
            if _contains_raw_secret_like_target(value, field=str(field)):
                issues.append(
                    _issue(
                        "raw_secret_like_target",
                        path=path,
                        operator_id=operator_id,
                        channel_index=index,
                        field=str(field),
                        message=f"channel {field} appears to contain an unmasked target",
                    )
                )

        channel_type = str(channel.get("channel_type") or "").lower()
        channel_skip_policy = _has_skip_policy(payload) or _has_skip_policy(channel)
        is_active = channel.get("is_active") is True
        dry_run_value = channel.get("dry_run_only")
        if (
            not canonical
            and channel_type == "telegram"
            and is_active
            and dry_run_value is False
            and not channel_skip_policy
        ):
            issues.append(
                _issue(
                    "active_noncanonical_telegram",
                    severity="warning" if allow_active_noncanonical else "failure",
                    path=path,
                    operator_id=operator_id,
                    channel_index=index,
                    message=(
                        "non-canonical Telegram channel is active without "
                        "dry-run-only"
                    ),
                )
            )
        if (
            not canonical
            and is_active
            and dry_run_value is None
            and not channel_skip_policy
        ):
            issues.append(
                _issue(
                    "missing_dry_run_or_skip_policy",
                    path=path,
                    operator_id=operator_id,
                    channel_index=index,
                    field="dry_run_only",
                    message=(
                        "active non-canonical channel lacks dry_run_only or "
                        "skip policy evidence"
                    ),
                )
            )

    row = {
        "path": str(path),
        "operator_id": operator_id,
        "current_operator_id": current_operator_id,
        "current_operator_username": username,
        "username": username,
        "canonical": canonical,
        "channel_count": channel_count,
        "active_channel_count": active_count,
        "dry_run_only_channel_count": dry_run_only_count,
        "mode": _mode(payload, channels),
        "status": _row_status(issues),
        "issue_codes": [issue.code for issue in issues],
    }
    return row, issues


def build_summary(
    evidence_root: Path, *, allow_active_noncanonical: bool = False
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    issues: list[Issue] = []
    files = _notification_files(evidence_root)
    for path in files:
        row, row_issues = _verify_file(
            path,
            allow_active_noncanonical=allow_active_noncanonical,
        )
        rows.append(row)
        issues.extend(row_issues)

    failure_count = sum(1 for issue in issues if issue.severity == "failure")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    operator_ids = {
        row["operator_id"] for row in rows if isinstance(row.get("operator_id"), int)
    }
    return {
        "status": "fail" if failure_count else "pass",
        "evidence_root": str(evidence_root),
        "file_count": len(files),
        "operator_count": len(operator_ids),
        "failure_count": failure_count,
        "warning_count": warning_count,
        "allow_active_noncanonical": allow_active_noncanonical,
        "operators": rows,
        "issues": [issue.as_dict() for issue in issues],
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _markdown_escape(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "| operator_id | username | mode | status | issues | path |",
        "|---|---|---|---|---|---|",
    ]
    for row in summary["operators"]:
        issues = ", ".join(row.get("issue_codes") or []) or "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_escape(row.get("operator_id")),
                    _markdown_escape(row.get("username")),
                    _markdown_escape(row.get("mode")),
                    _markdown_escape(row.get("status")),
                    _markdown_escape(issues),
                    _markdown_escape(row.get("path")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(summary), encoding="utf-8")


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    del stdout
    stderr = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        summary = build_summary(
            args.evidence_root,
            allow_active_noncanonical=args.allow_active_noncanonical,
        )
        _write_json(args.output, summary)
        if args.markdown:
            write_markdown(args.markdown, summary)
    except VerificationInputError as exc:
        stderr.write(f"{exc}\n")
        return 2
    except OSError as exc:
        stderr.write(f"failed to write output: {exc}\n")
        return 2
    return 1 if summary["failure_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
