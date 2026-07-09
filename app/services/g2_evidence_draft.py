"""Build a daily G-2 evidence manifest draft from ledger summaries.

Written by the read-only ``collect_g2_evidence`` beat task so ``counted_days``
can accumulate toward the G-2 exit review without a human running
``scripts/collect_g2_evidence.py`` every day. The output dict matches the schema
consumed by ``scripts/build_g2_exit_review.py`` (``_counted_days`` /
``_merged_operators`` / ``build_review_manifest``).

Honesty note: unlike the CLI collector (``scripts/collect_g2_evidence.py``), this
draft's daily "pass" verdict is derived purely from the per-operator evidence
*ledger* (``AnalyticsReportingService.build_g2_evidence_summary``). It does NOT
re-run the CLI's live endpoint-scope checks (candidate_preview scope, etc.),
which are a stable structural gate validated by the fastlane collector. Every
``operators`` / ``daily_status`` / ``blocking_gaps`` entry is stamped
``"source": "collect_g2_evidence_beat"`` so consumers can tell the two apart.

Rolling-window caveat (interpret ``counted_days`` accordingly): each daily
"pass" is a snapshot of ``build_g2_evidence_summary``, which evaluates a 30-day
*trailing* window. Once the ledger becomes ready, every subsequent day snapshots
"pass" until the window goes stale — so N counted beat-days can reflect a single
sustained 30-day-ready window, NOT N days of *fresh* forward evidence. This
matches the CLI collector's snapshot semantics (its per-day verdict is also a
rolling-window read), so the beat does not weaken the established definition;
but a human reading ``counted_days=7 / ready_for_review=true`` must read it as
"the ledger passed on 7 calendar dates", not "7 days of new activity". The exit
stays human-approved; add a freshness gate here if the roadmap ever requires
per-day fresh evidence.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# G-2 evidence sections rolled up per operator by build_g2_evidence_summary.
SECTION_KEYS: tuple[str, ...] = (
    "smoke",
    "strategy_monitor",
    "decision_experiments",
    "synthetic_experiments",
    "notifications",
)

# Provenance stamp distinguishing this ledger-based beat draft from the CLI
# collector's live endpoint-scope draft.
DRAFT_SOURCE = "collect_g2_evidence_beat"
DAILY_STATUS_SOURCE = "app/tasks/jobs.py::collect_g2_evidence"

# A day can only "pass" with at least this many independent operators, even if
# fewer targets are configured (guards against a shrunk target roster passing).
MIN_OPERATORS_FLOOR = 3


def _min_operators(target_operator_ids: list[int]) -> int:
    return max(MIN_OPERATORS_FLOOR, len(target_operator_ids))


def _sections(summary: dict[str, Any]) -> dict[str, str]:
    raw = summary.get("sections")
    if isinstance(raw, dict):
        return {key: str(raw.get(key) or "") for key in SECTION_KEYS}
    return {key: "" for key in SECTION_KEYS}


def _is_error_summary(summary: dict[str, Any] | None) -> bool:
    """A target with no summary or a recorded collection error is an error day."""
    return summary is None or bool(summary.get("error"))


def _evidence_window(
    *, run_date_kst: str, required_days: int, counted: bool
) -> dict[str, Any]:
    """One-day evidence window mirroring the CLI collector's keys.

    Mirrors ``scripts/collect_g2_evidence.py::_evidence_window`` (identical keys)
    so beat drafts and CLI drafts merge cleanly in ``build_g2_exit_review``.
    """
    required = max(1, required_days)
    end_date = date.fromisoformat(run_date_kst)
    start_date = (end_date - timedelta(days=required - 1)).isoformat()
    return {
        "start_date": start_date,
        "end_date": run_date_kst,
        "required_days": required_days,
        "observed_days": 1,
        "counted_days": 1 if counted else 0,
        "timezone": "Asia/Seoul",
    }


def _daily_operator_entry(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Per-operator cell for the daily_status row's ``operators`` map."""
    if summary is None:
        return {"evidence_status": "missing", "sections": {}}
    if summary.get("error"):
        return {
            "evidence_status": "collection_failed",
            "sections": {},
            "error": str(summary.get("error")),
        }
    return {
        "evidence_status": str(summary.get("evidence_status") or ""),
        "sections": _sections(summary),
    }


def _tally_targets(
    *,
    summaries_by_id: dict[int, dict[str, Any]],
    target_operator_ids: list[int],
) -> tuple[str, int, dict[str, dict[str, Any]]]:
    """Return (daily status, ready target count, per-operator cells)."""
    operators_row: dict[str, dict[str, Any]] = {}
    present: list[dict[str, Any]] = []
    has_error = False
    ready_count = 0
    total_gaps = 0
    for tid in target_operator_ids:
        summary = summaries_by_id.get(int(tid))
        operators_row[str(int(tid))] = _daily_operator_entry(summary)
        if _is_error_summary(summary):
            has_error = True
            continue
        present.append(summary)
        total_gaps += len(summary.get("blocking_gaps") or [])
        if str(summary.get("evidence_status")) == "ready":
            ready_count += 1

    all_ready = (
        len(present) >= _min_operators(target_operator_ids)
        and total_gaps == 0
        and all(str(s.get("evidence_status")) == "ready" for s in present)
    )
    if has_error:
        status = "fail"
    elif all_ready:
        status = "pass"
    else:
        status = "partial"
    return status, ready_count, operators_row


def _summaries_by_id(
    operator_summaries: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    return {
        int(item["operator_id"]): item
        for item in operator_summaries
        if item.get("operator_id") is not None
    }


def daily_status_from_target_summaries(
    *,
    operator_summaries: list[dict[str, Any]],
    target_operator_ids: list[int],
    run_date_kst: str,
) -> dict[str, Any]:
    """Compute the one-day ledger ``daily_status`` row for the target operators.

    Rule: any target missing or with a collection error -> "fail". Otherwise
    "pass" only when >= min_operators targets are present, every present target
    is ``evidence_status == "ready"``, and no target carries a blocking gap; else
    "partial". ``counted_days`` credit (in the window) is granted only on "pass".
    """
    status, ready_count, operators_row = _tally_targets(
        summaries_by_id=_summaries_by_id(operator_summaries),
        target_operator_ids=target_operator_ids,
    )
    return {
        "date": run_date_kst,
        "status": status,
        "summary": (
            "collect_g2_evidence beat ledger snapshot: "
            f"{ready_count}/{len(target_operator_ids)} targets ready "
            "(ledger-based, not CLI endpoint-scope)"
        ),
        "source": DAILY_STATUS_SOURCE,
        "collect_g2_evidence_snapshot": {
            "status": "pass" if status == "pass" else "fail",
            "source": DAILY_STATUS_SOURCE,
        },
        "operators": operators_row,
    }


def _blocking_gap_ids(*, tid: int, gap_count: int) -> list[str]:
    return [f"GAP-{tid}-{index}" for index in range(1, gap_count + 1)]


def _operator_entry(*, tid: int, summary: dict[str, Any] | None) -> dict[str, Any]:
    """Top-level ``operators`` roster entry for one target operator."""
    base = {"operator_id": tid, "source": DRAFT_SOURCE}
    if summary is None:
        return {
            **base,
            "username": None,
            "evidence_status": "missing",
            "sections": {},
            "blocking_gap_ids": [],
        }
    if summary.get("error"):
        return {
            **base,
            "username": summary.get("username"),
            "evidence_status": "collection_failed",
            "sections": {},
            "blocking_gap_ids": [],
            "error": str(summary.get("error")),
        }
    gaps = summary.get("blocking_gaps") or []
    return {
        **base,
        "username": summary.get("username"),
        "evidence_status": str(summary.get("evidence_status") or ""),
        "sections": _sections(summary),
        "blocking_gap_ids": _blocking_gap_ids(tid=tid, gap_count=len(gaps)),
    }


def _blocking_gaps(
    *,
    summaries_by_id: dict[int, dict[str, Any]],
    target_operator_ids: list[int],
) -> list[dict[str, Any]]:
    """Flatten each present target's ledger blocking gaps into open gap rows."""
    entries: list[dict[str, Any]] = []
    for tid in target_operator_ids:
        summary = summaries_by_id.get(int(tid))
        if summary is None or summary.get("error"):
            continue
        for index, gap in enumerate(summary.get("blocking_gaps") or [], start=1):
            entries.append(
                {
                    "gap_id": f"GAP-{int(tid)}-{index}",
                    "operator_id": int(tid),
                    "status": "open",
                    "detail": str(gap),
                    "description": str(gap),
                    "source": DRAFT_SOURCE,
                }
            )
    return entries


def build_daily_evidence_draft(
    *,
    operator_summaries: list[dict[str, Any]],
    target_operator_ids: list[int],
    run_date_kst: str,
    required_days: int,
    basis_commit: str | None = None,
) -> dict[str, Any]:
    """Assemble the one-day G-2 manifest draft consumed by build_g2_exit_review.

    ``operator_summaries`` are the per-target ledger summaries collected by the
    beat task; each present entry carries ``operator_id`` / ``username`` /
    ``evidence_status`` / ``sections`` / ``blocking_gaps`` and each errored entry
    carries ``operator_id`` / ``username`` / ``error``.
    """
    summaries_by_id = _summaries_by_id(operator_summaries)
    daily_row = daily_status_from_target_summaries(
        operator_summaries=operator_summaries,
        target_operator_ids=target_operator_ids,
        run_date_kst=run_date_kst,
    )
    return {
        "review_id": f"g2-exit-draft-daily-{run_date_kst.replace('-', '')}",
        "manifest_version": 1,
        "status": "draft",
        "basis": {
            "roadmap": "docs/roadmap.md",
            "runbook": "docs/operations/g2-evidence-runbook.md",
            "review_template": "docs/operations/g2-exit-review-template.md",
            "basis_commit": basis_commit,
            "source": DRAFT_SOURCE,
        },
        "evidence_window": _evidence_window(
            run_date_kst=run_date_kst,
            required_days=required_days,
            counted=daily_row["status"] == "pass",
        ),
        "operators": [
            _operator_entry(tid=int(tid), summary=summaries_by_id.get(int(tid)))
            for tid in target_operator_ids
        ],
        "daily_status": [daily_row],
        "blocking_gaps": _blocking_gaps(
            summaries_by_id=summaries_by_id,
            target_operator_ids=target_operator_ids,
        ),
        "action_register": {
            "dry_run_items": [],
            "approved_execution_items": [],
        },
    }
