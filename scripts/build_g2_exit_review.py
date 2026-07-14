#!/usr/bin/env python3
"""Build a local G-2 exit review bundle from manifest-draft.json files."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, TextIO

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._common import positive_int  # noqa: E402  (import after sys.path tweak)


UNRESOLVED_BLOCKING_GAP_STATUSES = {"open", "triaged", "accepted_hold"}


class ReviewBundleError(Exception):
    """Raised when local review bundle inputs cannot be processed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a local G-2 exit review bundle from manifest drafts."
    )
    parser.add_argument(
        "--evidence-root",
        required=True,
        type=Path,
        help="Directory to scan recursively for manifest-draft.json files.",
    )
    parser.add_argument("--review-id", required=True, help="Review bundle id.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help=(
            "Directory where manifest.json and exit-review.md are written. "
            "Default: <evidence-root>/<review-id>."
        ),
    )
    parser.add_argument(
        "--min-days",
        type=positive_int,
        default=7,
        help="Minimum counted pass days required for review. Default: 7.",
    )
    parser.add_argument(
        "--min-operators",
        type=positive_int,
        default=3,
        help="Minimum unique operators required for review. Default: 3.",
    )
    return parser


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReviewBundleError(f"failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewBundleError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReviewBundleError(f"{path} must contain a JSON object")
    return payload


def load_manifest_drafts(evidence_root: Path, output_dir: Path) -> list[dict[str, Any]]:
    """Load manifest drafts below evidence_root, excluding output_dir."""
    if not evidence_root.exists():
        return []

    drafts: list[dict[str, Any]] = []
    for path in sorted(evidence_root.rglob("manifest-draft.json")):
        if _is_relative_to(path, output_dir):
            continue
        draft = _read_json_object(path)
        draft["_source_path"] = str(path)
        try:
            draft["_source_mtime"] = path.stat().st_mtime
        except OSError:
            draft["_source_mtime"] = 0.0
        drafts.append(draft)
    return drafts


def _daily_rows(draft: dict[str, Any]) -> list[dict[str, Any]]:
    rows = draft.get("daily_status")
    if not isinstance(rows, list):
        return []
    return [deepcopy(row) for row in rows if isinstance(row, dict)]


def _draft_date(draft: dict[str, Any]) -> str:
    dates: list[str] = []
    window = draft.get("evidence_window")
    if isinstance(window, dict):
        for key in ("end_date", "start_date"):
            value = window.get(key)
            if value:
                dates.append(str(value))
    for row in _daily_rows(draft):
        value = row.get("date")
        if value:
            dates.append(str(value))
    return max(dates, default="")


def _draft_sort_key(draft: dict[str, Any]) -> tuple[str, float, str]:
    return (
        _draft_date(draft),
        float(draft.get("_source_mtime") or 0.0),
        str(draft.get("_source_path") or ""),
    )


def _operator_id(operator: dict[str, Any]) -> int | None:
    value = operator.get("operator_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, list, dict, tuple, set)) and len(value) == 0:
        return True
    return False


def _merge_operator_entry(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Field-level merge that prefers the newer (incoming) draft but never lets an
    absent or empty incoming value erase a value already recorded.

    Daily ``collect_g2_evidence`` beat drafts carry only rolling-ledger status
    (``evidence_status``/``sections``) and omit the file-backed operator identity
    (``company``/``profile``/``strategy``/``notification_channel``) that a fastlane
    draft records. A plain last-write-wins overwrite would drop that identity whenever
    the newest counted day is a ledger-only draft, breaking the readiness
    ``operator_independence`` gate. Merging per field keeps the file-backed identity
    while volatile status still tracks the newest draft.
    """
    merged = deepcopy(existing)
    for key, value in incoming.items():
        if _is_empty_value(value) and not _is_empty_value(merged.get(key)):
            continue
        merged[key] = deepcopy(value)
    return merged


def _merged_operators(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    operators_by_id: dict[int, dict[str, Any]] = {}
    for draft in sorted(drafts, key=_draft_sort_key):
        operators = draft.get("operators")
        if not isinstance(operators, list):
            continue
        for operator in operators:
            if not isinstance(operator, dict):
                continue
            operator_id = _operator_id(operator)
            if operator_id is None:
                continue
            clean = deepcopy(operator)
            clean["operator_id"] = operator_id
            existing = operators_by_id.get(operator_id)
            operators_by_id[operator_id] = (
                clean if existing is None else _merge_operator_entry(existing, clean)
            )
    return [operators_by_id[key] for key in sorted(operators_by_id)]


def _merged_daily_status(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for draft in drafts for row in _daily_rows(draft)]
    return sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row)))


def _merged_blocking_gaps(drafts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for draft in sorted(drafts, key=_draft_sort_key):
        draft_gaps = draft.get("blocking_gaps")
        if isinstance(draft_gaps, list):
            gaps.extend(deepcopy(gap) for gap in draft_gaps if isinstance(gap, dict))
    return gaps


def _merged_action_register(drafts: list[dict[str, Any]]) -> dict[str, list[Any]]:
    merged = {"dry_run_items": [], "approved_execution_items": []}
    for draft in sorted(drafts, key=_draft_sort_key):
        register = draft.get("action_register")
        if not isinstance(register, dict):
            continue
        for key in merged:
            items = register.get(key)
            if isinstance(items, list):
                merged[key].extend(deepcopy(items))
    return merged


def _counted_days(daily_status: list[dict[str, Any]]) -> int:
    pass_dates: set[str] = set()
    undated_pass_count = 0
    for row in daily_status:
        if row.get("status") != "pass":
            continue
        date = row.get("date")
        if date:
            pass_dates.add(str(date))
        else:
            undated_pass_count += 1
    return len(pass_dates) + undated_pass_count


def _open_blocking_gap_count(blocking_gaps: list[dict[str, Any]]) -> int:
    return sum(
        1
        for gap in blocking_gaps
        if str(gap.get("status") or "").strip().lower()
        in UNRESOLVED_BLOCKING_GAP_STATUSES
    )


def _evidence_window(
    daily_status: list[dict[str, Any]], *, counted_days: int, min_days: int
) -> dict[str, Any]:
    dates = sorted({str(row["date"]) for row in daily_status if row.get("date")})
    return {
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if dates else None,
        "required_days": min_days,
        "observed_days": len(dates) or len(daily_status),
        "counted_days": counted_days,
        "timezone": "Asia/Seoul",
    }


def _strip_internal_metadata(draft: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in draft.items() if not key.startswith("_")}


def build_review_manifest(
    drafts: list[dict[str, Any]],
    review_id: str,
    min_days: int,
    min_operators: int,
) -> dict[str, Any]:
    if not drafts:
        raise ReviewBundleError("no manifest-draft.json files found")

    newest_draft = max(drafts, key=_draft_sort_key)
    daily_status = _merged_daily_status(drafts)
    operators = _merged_operators(drafts)
    blocking_gaps = _merged_blocking_gaps(drafts)
    action_register = _merged_action_register(drafts)
    counted_days = _counted_days(daily_status)
    open_gap_count = _open_blocking_gap_count(blocking_gaps)
    operator_count = len(operators)
    ready_for_review = (
        counted_days >= min_days
        and operator_count >= min_operators
        and open_gap_count == 0
    )
    return {
        "review_id": review_id,
        "manifest_version": 1,
        "status": "ready_for_review" if ready_for_review else "draft",
        "basis": deepcopy(newest_draft.get("basis") or {}),
        "evidence_window": _evidence_window(
            daily_status,
            counted_days=counted_days,
            min_days=min_days,
        ),
        "operators": operators,
        "daily_status": daily_status,
        "blocking_gaps": blocking_gaps,
        "action_register": action_register,
        "review_gate_summary": {
            "counted_days": counted_days,
            "required_days": min_days,
            "operator_count": operator_count,
            "required_operator_count": min_operators,
            "open_blocking_gap_count": open_gap_count,
            "ready_for_review": ready_for_review,
        },
        "source_manifest_count": len(drafts),
        "source_manifests": [
            {
                "path": draft.get("_source_path"),
                "review_id": _strip_internal_metadata(draft).get("review_id"),
                "date": _draft_date(draft) or None,
            }
            for draft in sorted(drafts, key=_draft_sort_key)
        ],
    }


def render_exit_review(manifest: dict[str, Any]) -> str:
    gate = manifest.get("review_gate_summary") or {}
    lines = [
        "# G-2 Exit Review Draft",
        "",
        "Generated local draft. This file is not an approval.",
        "",
        f"- Review ID: {manifest.get('review_id')}",
        f"- Manifest status: {manifest.get('status')}",
        f"- Counted days: {gate.get('counted_days')} / {gate.get('required_days')}",
        (
            "- Operators: "
            f"{gate.get('operator_count')} / {gate.get('required_operator_count')}"
        ),
        f"- Unresolved blocking gaps: {gate.get('open_blocking_gap_count')}",
        f"- Ready for review: {gate.get('ready_for_review')}",
        "",
        "## Blocking Gaps",
        "",
    ]
    blocking_gaps = manifest.get("blocking_gaps") or []
    if blocking_gaps:
        for gap in blocking_gaps:
            if not isinstance(gap, dict):
                continue
            lines.append(
                "- "
                f"{gap.get('gap_id', 'gap')}: "
                f"{gap.get('status', 'unknown')} - "
                f"{gap.get('description', '')}"
            )
    else:
        lines.append("- None recorded.")
    lines.extend(["", "G-2 exit: pending"])
    return "\n".join(lines) + "\n"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_review_bundle(
    *,
    evidence_root: Path,
    review_id: str,
    output_dir: Path | None = None,
    min_days: int = 7,
    min_operators: int = 3,
) -> dict[str, Path]:
    output_dir = output_dir or evidence_root / review_id
    drafts = load_manifest_drafts(evidence_root, output_dir)
    manifest = build_review_manifest(drafts, review_id, min_days, min_operators)
    manifest_path = output_dir / "manifest.json"
    exit_review_path = output_dir / "exit-review.md"
    _write_json(manifest_path, manifest)
    exit_review_path.write_text(render_exit_review(manifest), encoding="utf-8")
    return {"manifest": manifest_path, "exit_review": exit_review_path}


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output_dir or args.evidence_root / args.review_id
    try:
        paths = write_review_bundle(
            evidence_root=args.evidence_root,
            review_id=args.review_id,
            output_dir=output_dir,
            min_days=args.min_days,
            min_operators=args.min_operators,
        )
    except ReviewBundleError as exc:
        stderr.write(f"{exc}\n")
        return 2

    stdout.write(
        json.dumps(
            {key: str(path) for key, path in paths.items()},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
