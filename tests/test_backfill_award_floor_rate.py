"""Tests for scripts/backfill_award_floor_rate.py against a SQLite test DB."""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.time import utc_now
from app.models.models import Project

# Load the script module by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "backfill_award_floor_rate",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_award_floor_rate.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = backfill
_SPEC.loader.exec_module(backfill)


def _payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap raw item rows in the KONEPS OpenAPI response envelope."""
    return {"response": {"body": {"items": items, "totalCount": len(items)}}}


class _FakeFetch:
    """Records calls and returns a per-notice payload (or raises)."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, notice_number: str, category: str | None) -> dict[str, Any]:
        self.calls.append((notice_number, category))
        result = self._responses[notice_number]
        if isinstance(result, Exception):
            raise result
        return result


# --- Pure decision logic ------------------------------------------------------


def test_parse_floor_rate_single_item_percent_to_fraction():
    payload = _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "88"}])
    assert backfill.parse_floor_rate(payload) == pytest.approx(0.88)


def test_parse_floor_rate_picks_latest_order():
    payload = _payload(
        [
            {"bidNtceOrd": "1", "sucsfbidLwltRate": "88"},
            {"bidNtceOrd": "3", "sucsfbidLwltRate": "85"},
            {"bidNtceOrd": "2", "sucsfbidLwltRate": "87"},
        ]
    )
    # order 3 wins -> 85% -> 0.85 (not the first row's 0.88)
    assert backfill.parse_floor_rate(payload) == pytest.approx(0.85)


def test_parse_floor_rate_missing_field_is_none():
    payload = _payload([{"bidNtceOrd": "1", "bidNtceNm": "no rate field"}])
    assert backfill.parse_floor_rate(payload) is None


def test_parse_floor_rate_garbage_value_is_none():
    payload = _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "n/a"}])
    assert backfill.parse_floor_rate(payload) is None


def test_parse_floor_rate_empty_items_is_none():
    assert backfill.parse_floor_rate(_payload([])) is None


def test_latest_order_item_handles_unparseable_orders():
    items = [
        {"bidNtceOrd": None, "sucsfbidLwltRate": "80"},
        {"bidNtceOrd": "2", "sucsfbidLwltRate": "85"},
    ]
    assert backfill.latest_order_item(items)["sucsfbidLwltRate"] == "85"


# --- Target selection ---------------------------------------------------------


@pytest.fixture
def seeded_targets(test_db):
    """Open+NULL notices with staggered deadlines plus rows that must be excluded."""
    now = utc_now()
    test_db.add_all(
        [
            # eligible: NULL rate, open, future deadline (later)
            Project(
                id=1,
                notice_number="R0001",
                category="service",
                status="open",
                award_floor_rate=None,
                deadline=now + timedelta(days=5),
            ),
            # eligible: NULL rate, open, future deadline (sooner -> first)
            Project(
                id=2,
                notice_number="R0002",
                category="construction",
                status="open",
                award_floor_rate=None,
                deadline=now + timedelta(days=1),
            ),
            # excluded: already has a floor rate
            Project(
                id=3,
                notice_number="R0003",
                category="service",
                status="open",
                award_floor_rate=0.88,
                deadline=now + timedelta(days=2),
            ),
            # excluded: not open
            Project(
                id=4,
                notice_number="R0004",
                category="service",
                status="closed",
                award_floor_rate=None,
                deadline=now + timedelta(days=2),
            ),
            # excluded: deadline in the past (outside default window)
            Project(
                id=5,
                notice_number="R0005",
                category="service",
                status="open",
                award_floor_rate=None,
                deadline=now - timedelta(days=2),
            ),
        ]
    )
    test_db.commit()
    return test_db


def test_load_targets_filters_and_orders_by_deadline(seeded_targets):
    targets = backfill.load_targets(seeded_targets)
    # only ids 2 and 1 qualify, sorted by deadline asc (2 sooner than 1)
    assert [t[0] for t in targets] == [2, 1]
    assert [t[1] for t in targets] == ["R0002", "R0001"]


def test_load_targets_include_past_days_widens_window(seeded_targets):
    targets = backfill.load_targets(seeded_targets, include_past_days=3)
    # past-deadline id 5 now qualifies; still ordered by deadline asc
    assert [t[0] for t in targets] == [5, 2, 1]


def test_load_targets_limit_caps_results(seeded_targets):
    targets = backfill.load_targets(seeded_targets, limit=1)
    assert [t[0] for t in targets] == [2]


# --- Run loop -----------------------------------------------------------------


def test_run_backfill_counts_and_persists(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "87"}]),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "n/a"}]),
        }
    )

    stats = backfill.run_backfill(
        seeded_targets, targets, fetch=fetch, delay=0, chunk_size=1
    )

    assert stats.processed == 2
    assert stats.updated == 1
    assert stats.no_value == 1
    assert stats.errors == 0
    # persisted value on the updated project
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[2].award_floor_rate == pytest.approx(0.87)
    assert rows[1].award_floor_rate is None  # no_value stays NULL


def test_run_backfill_records_errors_without_dying(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": RuntimeError("HTTP 500"),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "85"}]),
        }
    )

    stats = backfill.run_backfill(
        seeded_targets, targets, fetch=fetch, delay=0, log=lambda *_: None
    )

    assert stats.processed == 2  # error on first did not abort the run
    assert stats.errors == 1
    assert stats.updated == 1
    assert stats.aborted is False
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[1].award_floor_rate == pytest.approx(0.85)


def test_run_backfill_aborts_on_consecutive_errors(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": RuntimeError("HTTP 429"),
            "R0001": RuntimeError("HTTP 429"),
        }
    )

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        delay=0,
        max_consecutive_errors=1,
        log=lambda *_: None,
    )

    assert stats.aborted is True
    assert stats.processed == 1  # stopped after the first consecutive error
    assert stats.errors == 1


def test_run_backfill_idempotent_skips_already_valued(seeded_targets):
    """A project that already has a floor rate is never in the target set/fetched."""
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1 only (id 3 excluded)
    fetch = _FakeFetch(
        {
            "R0002": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "87"}]),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "86"}]),
        }
    )

    backfill.run_backfill(seeded_targets, targets, fetch=fetch, delay=0)

    fetched = {notice for notice, _ in fetch.calls}
    assert "R0003" not in fetched  # already-valued notice never queried
    # a second run finds nothing new (all now valued)
    remaining = backfill.load_targets(seeded_targets)
    assert remaining == []


def test_dry_run_does_not_call_fetch(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch({})  # any call would KeyError

    stats = backfill.run_backfill(
        seeded_targets, targets, fetch=fetch, dry_run=True, sample_size=5
    )

    assert fetch.calls == []  # no API calls in dry-run
    assert stats.dry_run is True
    assert stats.target_count == 2
    assert stats.processed == 0
    assert stats.updated == 0
    assert stats.sample == ["R0002", "R0001"]
    # nothing written
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[2].award_floor_rate is None
