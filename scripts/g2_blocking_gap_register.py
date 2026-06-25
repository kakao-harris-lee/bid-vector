#!/usr/bin/env python3
"""Build a local G-2 blocking gap register from evidence manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, TextIO


OPEN_STATUSES = {"open", "triaged", "accepted_hold"}
CLOSED_STATUSES = {"resolved", "excluded"}
VALID_STATUSES = OPEN_STATUSES | CLOSED_STATUSES


class GapRegisterError(Exception):
    """Raised when gap register inputs cannot be processed."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a local G-2 blocking gap register from manifest files."
    )
    parser.add_argument(
        "--evidence-root",
        required=True,
        type=Path,
        help="Directory to scan recursively for manifest-draft.json and manifest.json.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path where the JSON gap register is written.",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional path where a compact markdown gap table is written.",
    )
    return parser


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GapRegisterError(f"failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise GapRegisterError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise GapRegisterError(f"{path} must contain a JSON object")
    return payload


def _relative_source_path(path: Path, evidence_root: Path) -> str:
    try:
        return path.relative_to(evidence_root).as_posix()
    except ValueError:
        return path.as_posix()


def _manifest_paths(evidence_root: Path) -> list[Path]:
    if not evidence_root.exists():
        raise GapRegisterError(f"evidence root does not exist: {evidence_root}")
    if not evidence_root.is_dir():
        raise GapRegisterError(f"evidence root is not a directory: {evidence_root}")

    paths = [
        path
        for path in evidence_root.rglob("*")
        if path.is_file() and path.name in {"manifest-draft.json", "manifest.json"}
    ]
    return sorted(paths, key=lambda path: _relative_source_path(path, evidence_root))


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in VALID_STATUSES:
        return status
    if status in {"", "missing", "mixed_scope"}:
        return "open"
    return "open"


def _recommended_treatment(gap: dict[str, Any], category: str, status: str) -> str:
    value = gap.get("recommended_treatment") or gap.get("treatment")
    if value:
        return str(value)
    if status == "accepted_hold":
        return "hold"
    if category == "mixed data":
        return "documented_not_counted"
    return "rerun"


def _stable_gap_id(
    *,
    gap: dict[str, Any],
    source_path: str,
    gap_index: int,
    operator_id: str | None,
    date: str | None,
    category: str,
    status: str,
    description: str,
    recommended_treatment: str,
) -> str:
    value = gap.get("gap_id")
    if value:
        return str(value)

    seed = json.dumps(
        {
            "source_path": source_path,
            "gap_index": gap_index,
            "operator_id": operator_id,
            "date": date,
            "category": category,
            "status": status,
            "description": description,
            "recommended_treatment": recommended_treatment,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12].upper()
    return f"GAP-AUTO-{digest}"


def _date_from_manifest(manifest: dict[str, Any]) -> str | None:
    window = manifest.get("evidence_window")
    if isinstance(window, dict):
        for key in ("end_date", "start_date"):
            value = window.get(key)
            if value:
                return str(value)
    daily_status = manifest.get("daily_status")
    if isinstance(daily_status, list):
        for row in daily_status:
            if isinstance(row, dict) and row.get("date"):
                return str(row["date"])
    return None


def _gap_rows_from_manifest(
    *, manifest: dict[str, Any], source_path: str
) -> list[dict[str, Any]]:
    gaps = manifest.get("blocking_gaps", [])
    if gaps is None:
        gaps = []
    if not isinstance(gaps, list):
        raise GapRegisterError(f"{source_path} blocking_gaps must be a list")

    rows: list[dict[str, Any]] = []
    manifest_date = _date_from_manifest(manifest)
    for index, gap in enumerate(gaps, start=1):
        if not isinstance(gap, dict):
            raise GapRegisterError(f"{source_path} blocking_gaps[{index}] must be an object")

        operator_id = _string_or_none(gap.get("operator_id"))
        date = _string_or_none(gap.get("date")) or manifest_date
        category = str(gap.get("category") or "missing evidence")
        status = _normalize_status(gap.get("status"))
        description = str(
            gap.get("description") or gap.get("summary") or gap.get("reason") or ""
        )
        recommended_treatment = _recommended_treatment(gap, category, status)
        gap_id = _stable_gap_id(
            gap=gap,
            source_path=source_path,
            gap_index=index,
            operator_id=operator_id,
            date=date,
            category=category,
            status=status,
            description=description,
            recommended_treatment=recommended_treatment,
        )
        rows.append(
            {
                "gap_id": gap_id,
                "operator_id": operator_id,
                "date": date,
                "category": category,
                "status": status,
                "source_path": source_path,
                "description": description,
                "recommended_treatment": recommended_treatment,
            }
        )
    return rows


def build_gap_register(evidence_root: Path) -> dict[str, Any]:
    manifest_paths = _manifest_paths(evidence_root)
    if not manifest_paths:
        raise GapRegisterError(
            f"no manifest-draft.json or manifest.json files found under {evidence_root}"
        )

    rows: list[dict[str, Any]] = []
    for path in manifest_paths:
        source_path = _relative_source_path(path, evidence_root)
        manifest = _read_json_object(path)
        rows.extend(_gap_rows_from_manifest(manifest=manifest, source_path=source_path))

    rows.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("operator_id") or ""),
            str(row.get("gap_id") or ""),
            str(row.get("source_path") or ""),
        )
    )
    open_gap_count = sum(1 for row in rows if row["status"] in OPEN_STATUSES)
    return {
        "schema_version": 1,
        "evidence_root": str(evidence_root),
        "manifest_count": len(manifest_paths),
        "gap_count": len(rows),
        "open_gap_count": open_gap_count,
        "open_statuses": sorted(OPEN_STATUSES),
        "rows": rows,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _markdown_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(register: dict[str, Any]) -> str:
    lines = [
        "| gap_id | operator_id | date | category | status | treatment | source |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in register["rows"]:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row["gap_id"],
                    row["operator_id"],
                    row["date"],
                    row["category"],
                    row["status"],
                    row["recommended_treatment"],
                    row["source_path"],
                )
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def write_gap_register(
    *, evidence_root: Path, output: Path, markdown: Path | None = None
) -> dict[str, Any]:
    register = build_gap_register(evidence_root)
    _write_json(output, register)
    if markdown is not None:
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(register), encoding="utf-8")
    return register


def main(
    argv: list[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        register = write_gap_register(
            evidence_root=args.evidence_root,
            output=args.output,
            markdown=args.markdown,
        )
    except GapRegisterError as exc:
        stderr.write(f"{exc}\n")
        return 2

    summary = {
        "output": str(args.output),
        "markdown": str(args.markdown) if args.markdown else None,
        "manifest_count": register["manifest_count"],
        "gap_count": register["gap_count"],
        "open_gap_count": register["open_gap_count"],
    }
    stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 1 if register["open_gap_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
