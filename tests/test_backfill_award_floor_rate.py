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


def _result_payload(result_code: str, message: str = "") -> dict[str, Any]:
    """An OpenAPI envelope carrying a header resultCode (quota/throttle case)."""
    return {
        "response": {
            "header": {"resultCode": result_code, "resultMsg": message},
            "body": {"items": [], "totalCount": 0},
        }
    }


class _FakeFetch:
    """Records targeted-query calls and returns a per-notice payload (or raises)."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, notice_number: str, category: str | None) -> dict[str, Any]:
        self.calls.append((notice_number, category))
        result = self._responses[notice_number]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeLicenseLimitFetch:
    """Records license-limit sub-call args and returns a per-notice payload.

    Unconfigured notices default to an empty item list (a notice with no license
    limit), so a Y-flagged notice without an explicit sub-response still resolves
    to a flags-only eligibility dict.
    """

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, notice_number: str, bid_notice_ord: str) -> dict[str, Any]:
        self.calls.append((notice_number, bid_notice_ord))
        result = self._responses.get(notice_number, _payload([]))
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


def test_latest_item_from_payload_picks_latest_order():
    payload = _payload(
        [
            {"bidNtceOrd": "1", "tag": "a"},
            {"bidNtceOrd": "3", "tag": "c"},
            {"bidNtceOrd": "2", "tag": "b"},
        ]
    )
    assert backfill.latest_item_from_payload(payload)["tag"] == "c"


def test_latest_item_from_payload_empty_is_none():
    assert backfill.latest_item_from_payload(_payload([])) is None


def test_floor_rate_from_item():
    assert backfill.floor_rate_from_item({"sucsfbidLwltRate": "88"}) == pytest.approx(
        0.88
    )
    assert backfill.floor_rate_from_item({"sucsfbidLwltRate": "n/a"}) is None
    assert backfill.floor_rate_from_item({}) is None
    assert backfill.floor_rate_from_item(None) is None


def test_needs_license_limit_only_when_flagged_and_order_known():
    # 업종제한 flag "Y" (any case) + a known 차수 -> sub-call needed.
    assert backfill._needs_license_limit({"indstrytyLmtYn": "Y"}, 1) is True
    assert backfill._needs_license_limit({"indstrytyLmtYn": "y"}, 2) is True
    # Not flagged, or no 차수, or no flags at all -> no sub-call.
    assert backfill._needs_license_limit({"indstrytyLmtYn": "N"}, 1) is False
    assert backfill._needs_license_limit({"indstrytyLmtYn": "Y"}, None) is False
    assert backfill._needs_license_limit(None, 1) is False
    assert backfill._needs_license_limit({}, 1) is False


@pytest.mark.parametrize("code", ["00", "03", ""])
def test_raise_for_result_code_allows_ok_codes(code):
    # "00"/"03" are success; an empty/absent code is treated as OK.
    backfill.raise_for_result_code(_result_payload(code))


@pytest.mark.parametrize("code", ["22", "30", "99"])
def test_raise_for_result_code_raises_on_error_code(code):
    # Quota/throttle come back as HTTP 200 + non-OK resultCode -> must raise.
    with pytest.raises(ValueError):
        backfill.raise_for_result_code(_result_payload(code, "LIMITED_NUMBER"))


def test_raise_for_result_code_message_excludes_secret():
    # The error surfaces the code/message but never a service key.
    with pytest.raises(ValueError, match="resultCode=22"):
        backfill.raise_for_result_code(_result_payload("22", "quota exceeded"))


# --- Target selection ---------------------------------------------------------


@pytest.fixture
def seeded_targets(test_db):
    """Open notices missing eligibility_raw with staggered deadlines plus rows that must be excluded."""
    now = utc_now()
    test_db.add_all(
        [
            # eligible: NULL eligibility, open, future deadline (later)
            Project(
                id=1,
                notice_number="R0001",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=5),
            ),
            # eligible: NULL eligibility, open, future deadline (sooner -> first)
            Project(
                id=2,
                notice_number="R0002",
                category="construction",
                status="open",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=1),
            ),
            # excluded: already has eligibility_raw (resume key satisfied)
            Project(
                id=3,
                notice_number="R0003",
                category="service",
                status="open",
                award_floor_rate=0.88,
                eligibility_raw={"flags": {"indstrytyLmtYn": "N"}},
                deadline=now + timedelta(days=2),
            ),
            # excluded: not open
            Project(
                id=4,
                notice_number="R0004",
                category="service",
                status="closed",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=2),
            ),
            # excluded: deadline in the past (outside default window)
            Project(
                id=5,
                notice_number="R0005",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw=None,
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


def test_load_targets_keys_on_eligibility_null_not_floor(test_db):
    """Target selection keys on eligibility_raw IS NULL, independent of floor rate."""
    now = utc_now()
    test_db.add_all(
        [
            # floor already set but eligibility NULL -> still a target
            Project(
                id=10,
                notice_number="R0010",
                category="service",
                status="open",
                award_floor_rate=0.88,
                eligibility_raw=None,
                deadline=now + timedelta(days=1),
            ),
            # floor NULL but eligibility already set -> excluded
            Project(
                id=11,
                notice_number="R0011",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw={"flags": {"indstrytyLmtYn": "N"}},
                deadline=now + timedelta(days=2),
            ),
        ]
    )
    test_db.commit()

    ids = [t[0] for t in backfill.load_targets(test_db)]
    assert 10 in ids  # floor-set-but-eligibility-null is still targeted
    assert 11 not in ids  # eligibility already saved -> skipped


# --- Run loop -----------------------------------------------------------------


def test_run_backfill_counts_and_persists(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "87"}]),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "n/a"}]),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        chunk_size=1,
    )

    assert stats.processed == 2
    assert stats.updated == 1
    assert stats.no_value == 1
    assert stats.eligibility_saved == 0  # rate-only payloads carry no flags
    assert stats.license_limit_calls == 0
    assert stats.errors == 0
    assert license_limit.calls == []  # no 업종제한 flag -> no sub-call
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
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        log=lambda *_: None,
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
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        max_consecutive_errors=1,
        log=lambda *_: None,
    )

    assert stats.aborted is True
    assert stats.processed == 1  # stopped after the first consecutive error
    assert stats.errors == 1


def test_run_backfill_quota_200_trips_abort_guard(seeded_targets):
    """HTTP 200 + error resultCode must count as an error and hit the guard.

    Regression: a quota/throttle response is HTTP 200 (so the fetch itself does
    not raise) but carries a non-OK resultCode. Without the header check it would
    parse to zero items -> no_value -> the consecutive-error counter resets and
    the run never aborts against a live rate limit. The check runs inside
    run_backfill, so the injected fake fetch exercises the real guard path.
    """
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _result_payload("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS"),
            "R0001": _result_payload("22", "LIMITED_NUMBER_OF_SERVICE_REQUESTS"),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        max_consecutive_errors=1,
        log=lambda *_: None,
    )

    assert stats.aborted is True
    assert stats.errors == 1
    assert stats.no_value == 0  # quota-200 is an error, never a silent no_value
    assert stats.processed == 1  # aborted before touching the second notice


def test_run_backfill_blank_notice_counts_skipped_without_fetch(test_db):
    """A row with an empty notice number is skipped (no fetch, no throttle)."""
    test_db.add(
        Project(
            id=1,
            notice_number="   ",  # normalizes to empty
            category="service",
            status="open",
            award_floor_rate=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch({})  # any call would KeyError
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        test_db, targets, fetch=fetch, fetch_license_limit=license_limit, delay=0
    )

    assert fetch.calls == []  # blank notice never fetched
    assert license_limit.calls == []
    assert stats.skipped_blank == 1
    assert stats.no_value == 0
    assert stats.updated == 0


def test_run_backfill_idempotent_skips_already_valued(seeded_targets):
    """A project that already has eligibility_raw is never in the target set/fetched."""
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1 only (id 3 excluded)
    fetch = _FakeFetch(
        {
            "R0002": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "87", "indstrytyLmtYn": "N"}]
            ),
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "86", "indstrytyLmtYn": "N"}]
            ),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
    )

    fetched = {notice for notice, _ in fetch.calls}
    assert "R0003" not in fetched  # eligibility-set notice never queried
    # a second run finds nothing new (flags-only dicts completed both rows)
    remaining = backfill.load_targets(seeded_targets)
    assert remaining == []


def test_dry_run_does_not_call_fetch(seeded_targets):
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch({})  # any call would KeyError
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        dry_run=True,
        sample_size=5,
    )

    assert fetch.calls == []  # no API calls in dry-run
    assert license_limit.calls == []
    assert stats.dry_run is True
    assert stats.target_count == 2
    assert stats.processed == 0
    assert stats.updated == 0
    assert stats.sample == ["R0002", "R0001"]
    # nothing written
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[2].award_floor_rate is None


# --- Eligibility raw (flags + license-limit sub-call) -------------------------


def test_run_backfill_passes_zero_padded_ord_to_license_limit_call(seeded_targets):
    """The 차수 "000" must reach the sub-call verbatim (zero-padding preserved).

    Live-measured 2026-07-19: getBidPblancListInfoLicenseLimit returns
    totalCount=0 for bidNtceOrd=0 but real rows for bidNtceOrd="000" — an int
    coercion silently drops every first-order notice's license limits.
    """
    targets = backfill.load_targets(seeded_targets)
    fetch = _FakeFetch(
        {
            "R0002": _payload(
                [{"bidNtceOrd": "000", "sucsfbidLwltRate": "87", "indstrytyLmtYn": "Y"}]
            ),
            "R0001": _payload(
                [{"bidNtceOrd": "000", "sucsfbidLwltRate": "86", "indstrytyLmtYn": "Y"}]
            ),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        chunk_size=1,
    )

    assert [ord_value for _, ord_value in license_limit.calls] == ["000", "000"]


def test_run_backfill_saves_floor_and_synthesized_eligibility(seeded_targets):
    """A Y-flagged target gets floor + flags + license_limits from two calls; an
    N-flagged one saves flags-only with no sub-call."""
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload(
                [{"bidNtceOrd": "2", "sucsfbidLwltRate": "87", "indstrytyLmtYn": "Y"}]
            ),
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "86", "indstrytyLmtYn": "N"}]
            ),
        }
    )
    license_limit = _FakeLicenseLimitFetch(
        {
            "R0002": _payload(
                [
                    {
                        "lcnsLmtNm": "종합여행업/1261",
                        "permsnIndstrytyList": "",
                        "lmtGrpNo": "1",
                        "lmtSno": "1",
                    }
                ]
            ),
        }
    )

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        chunk_size=1,
    )

    assert stats.updated == 2
    assert stats.eligibility_saved == 2
    assert stats.license_limit_calls == 1
    assert stats.no_value == 0
    # Only the Y-flagged notice triggered a sub-call, keyed on its latest 차수.
    # The 차수 must be the raw zero-padding-preserving string, never an int:
    # KONEPS silently returns totalCount=0 for ord=0 (live-measured 2026-07-19).
    assert license_limit.calls == [("R0002", "2")]
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[2].award_floor_rate == pytest.approx(0.87)
    assert rows[2].eligibility_raw == {
        "flags": {"indstrytyLmtYn": "Y"},
        "license_limits": [{"lcnsLmtNm": "종합여행업/1261", "lmtGrpNo": "1", "lmtSno": "1"}],
    }
    assert rows[1].award_floor_rate == pytest.approx(0.86)
    assert rows[1].eligibility_raw == {"flags": {"indstrytyLmtYn": "N"}}


def test_run_backfill_does_not_overwrite_existing_floor(test_db):
    """A target already carrying a floor keeps it; flags-only eligibility is written."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="service",
            status="open",
            award_floor_rate=0.90,
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)  # id 1 (eligibility NULL)
    fetch = _FakeFetch(
        {
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "80", "indstrytyLmtYn": "N"}]
            ),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        test_db, targets, fetch=fetch, fetch_license_limit=license_limit, delay=0
    )

    assert stats.updated == 0  # floor already set -> not touched
    assert stats.no_value == 0  # and not counted as no_value either
    assert stats.eligibility_saved == 1
    assert license_limit.calls == []  # N-flag -> no sub-call
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.award_floor_rate == pytest.approx(0.90)  # unchanged, not 0.80
    assert row.eligibility_raw == {"flags": {"indstrytyLmtYn": "N"}}


def test_run_backfill_eligibility_only_target_flags_only(test_db):
    """A floor-set target with a flags-only notice saves flags, no floor, no no_value."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="service",
            status="open",
            award_floor_rate=0.90,  # already set -> floor path skipped entirely
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch(
        {"R0001": _payload([{"bidNtceOrd": "1", "indstrytyLmtYn": "N"}])}
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        test_db, targets, fetch=fetch, fetch_license_limit=license_limit, delay=0
    )

    assert stats.no_value == 0
    assert stats.updated == 0
    assert stats.eligibility_saved == 1
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.eligibility_raw == {"flags": {"indstrytyLmtYn": "N"}}


def test_run_backfill_flagged_notice_with_license_rows(test_db):
    """A Y-flagged notice with no floor still fetches and stores license limits."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="construction",
            status="open",
            award_floor_rate=None,
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch(
        {"R0001": _payload([{"bidNtceOrd": "3", "indstrytyLmtYn": "Y"}])}
    )
    license_limit = _FakeLicenseLimitFetch(
        {
            "R0001": _payload(
                [
                    {"lcnsLmtNm": "엔지니어링사업자(항만및해안)", "permsnIndstrytyList": "항만설계"},
                    {"lcnsLmtNm": "해양엔지니어링"},
                ]
            )
        }
    )

    stats = backfill.run_backfill(
        test_db, targets, fetch=fetch, fetch_license_limit=license_limit, delay=0
    )

    assert stats.license_limit_calls == 1
    assert stats.eligibility_saved == 1
    assert license_limit.calls == [("R0001", "3")]
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.eligibility_raw == {
        "flags": {"indstrytyLmtYn": "Y"},
        "license_limits": [
            {"lcnsLmtNm": "엔지니어링사업자(항만및해안)", "permsnIndstrytyList": "항만설계"},
            {"lcnsLmtNm": "해양엔지니어링"},
        ],
    }


def test_run_backfill_license_limit_failure_leaves_eligibility_null(test_db):
    """A sub-call failure is a per-notice error: floor lands, eligibility stays NULL."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="service",
            status="open",
            award_floor_rate=None,
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch(
        {
            "R0001": _payload(
                [{"bidNtceOrd": "3", "sucsfbidLwltRate": "88", "indstrytyLmtYn": "Y"}]
            )
        }
    )
    license_limit = _FakeLicenseLimitFetch({"R0001": RuntimeError("HTTP 500")})

    stats = backfill.run_backfill(
        test_db,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        log=lambda *_: None,
    )

    assert stats.errors == 1
    assert stats.eligibility_saved == 0
    assert stats.license_limit_calls == 0  # counted only on success
    assert stats.updated == 1  # floor still landed
    assert license_limit.calls == [("R0001", "3")]
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.award_floor_rate == pytest.approx(0.88)
    assert row.eligibility_raw is None  # NULL -> retried next run
    assert [t[0] for t in backfill.load_targets(test_db)] == [1]  # still a target


def test_run_backfill_sub_call_quota_200_counts_error(test_db):
    """A quota/throttle 200 on the sub-call is a per-notice error, not silent."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="service",
            status="open",
            award_floor_rate=None,
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch(
        {
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "88", "indstrytyLmtYn": "Y"}]
            )
        }
    )
    license_limit = _FakeLicenseLimitFetch({"R0001": _result_payload("22", "LIMITED")})

    stats = backfill.run_backfill(
        test_db,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        log=lambda *_: None,
    )

    assert stats.errors == 1
    assert stats.eligibility_saved == 0
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.eligibility_raw is None


def test_run_backfill_throttles_between_all_calls(test_db):
    """The throttle sleeps between every API call, including the license-limit sub-call."""
    test_db.add(
        Project(
            id=1,
            notice_number="R0001",
            category="service",
            status="open",
            award_floor_rate=None,
            eligibility_raw=None,
            deadline=utc_now() + timedelta(days=1),
        )
    )
    test_db.commit()
    targets = backfill.load_targets(test_db)
    fetch = _FakeFetch(
        {
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "88", "indstrytyLmtYn": "Y"}]
            )
        }
    )
    license_limit = _FakeLicenseLimitFetch(
        {"R0001": _payload([{"lcnsLmtNm": "토목공사업"}])}
    )
    sleeps: list[float] = []

    backfill.run_backfill(
        test_db,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=1.5,
        sleep=lambda seconds: sleeps.append(seconds),
    )

    # Two calls for one notice: the first is not delayed, the sub-call sleeps once.
    assert sleeps == [1.5]
    assert license_limit.calls == [("R0001", "1")]
    row = test_db.query(Project).filter(Project.id == 1).one()
    assert row.eligibility_raw == {
        "flags": {"indstrytyLmtYn": "Y"},
        "license_limits": [{"lcnsLmtNm": "토목공사업"}],
    }
