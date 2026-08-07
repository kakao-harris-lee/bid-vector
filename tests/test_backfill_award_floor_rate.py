"""Tests for scripts/backfill_award_floor_rate.py against a SQLite test DB."""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.core.time import utc_now
from app.models.models import OperatorStrategy, Project, User

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


def test_load_targets_includes_re_notice(test_db):
    """Re-noticed opportunities are biddable, so the backfill must target them too.

    Regression guard for the open_projects() switch: the old ``status == "open"``
    literal silently dropped ``re_notice`` rows even though they are open for
    bidding and their predictions need the award floor just like a first-round
    notice. Both statuses must now appear in the target set.
    """
    now = utc_now()
    test_db.add_all(
        [
            Project(
                id=20,
                notice_number="R0020",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=1),
            ),
            Project(
                id=21,
                notice_number="R0021",
                category="service",
                status="re_notice",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=2),
            ),
        ]
    )
    test_db.commit()

    ids = [t[0] for t in backfill.load_targets(test_db)]
    assert 20 in ids  # first-round open notice
    assert 21 in ids  # re-noticed opportunity — previously dropped by == "open"


# --- Operator-candidate-first tiering -----------------------------------------


def _seed_strategy(db, **overrides) -> OperatorStrategy:
    """Persist the canonical operator plus a watch strategy (default: marine gate)."""
    operator = User(username="operator", email="op@example.com", hashed_password="x")
    db.add(operator)
    db.flush()
    fields = dict(
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        required_keywords="항만,방파제",
        exclude_keywords="",
        min_budget_estimate=0.0,
        max_budget_estimate=0.0,
    )
    fields.update(overrides)
    strategy = OperatorStrategy(user_id=operator.id, **fields)
    db.add(strategy)
    db.commit()
    return strategy


@pytest.fixture
def tiered_targets(test_db):
    """Two gate-matching notices with LATE deadlines + two imminent off-scope ones.

    Deadline order alone would pick the off-scope notices first, so any test that
    sees the gate-matching ids first is seeing the tiering work.
    """
    now = utc_now()
    test_db.add_all(
        [
            # off-scope, most imminent -> tier 2 despite the earliest deadline
            Project(
                id=1,
                notice_number="R0001",
                title="○○청사 신축공사",
                description="공고기관: ○○시청",
                requirements="철근콘크리트 구조",
                category="construction",
                status="open",
                eligibility_raw=None,
                deadline=now + timedelta(days=1),
            ),
            # off-scope, second most imminent -> tier 2
            Project(
                id=2,
                notice_number="R0002",
                title="여행업 위탁 용역",
                description="공고기관: ○○공사",
                requirements="",
                category="service",
                status="open",
                eligibility_raw=None,
                deadline=now + timedelta(days=2),
            ),
            # operator candidate, later deadline -> tier 1 (second inside tier)
            Project(
                id=3,
                notice_number="R0003",
                title="○○항 준설공사",
                description="공고기관: ○○지방해양수산청",
                requirements="항만 준설 및 사석 투하",
                category="construction",
                status="open",
                eligibility_raw=None,
                deadline=now + timedelta(days=9),
            ),
            # operator candidate, earlier of the two -> tier 1 first
            Project(
                id=4,
                notice_number="R0004",
                title="○○ 방파제 보강공사",
                description="공고기관: ○○시청",
                requirements="케이슨 거치",
                category="construction",
                status="open",
                eligibility_raw=None,
                deadline=now + timedelta(days=5),
            ),
        ]
    )
    test_db.commit()
    return test_db


def test_select_targets_puts_operator_candidates_first(tiered_targets):
    """Core regression: a gate-matching notice outranks a sooner off-scope one."""
    _seed_strategy(tiered_targets)
    selection = backfill.select_targets(tiered_targets)
    # tier 1 (ids 4, 3 — deadline asc) before tier 2 (ids 1, 2 — deadline asc)
    assert [t[0] for t in selection.targets] == [4, 3, 1, 2]
    assert selection.operator_priority is True
    assert selection.tier1_count == 2
    assert selection.tier2_count == 2


def test_select_targets_keeps_deadline_order_inside_each_tier(tiered_targets):
    """Tiering reorders across tiers only; inside a tier it stays imminent-first."""
    _seed_strategy(tiered_targets)
    ids = [t[0] for t in backfill.select_targets(tiered_targets).targets]
    assert ids[:2] == [4, 3]  # tier 1: day 5 before day 9
    assert ids[2:] == [1, 2]  # tier 2: day 1 before day 2


def test_select_targets_limit_spends_the_quota_on_tier1(tiered_targets):
    """A capped run fills from tier 1 first (the whole point of the change)."""
    _seed_strategy(tiered_targets)
    selection = backfill.select_targets(tiered_targets, limit=2)
    assert [t[0] for t in selection.targets] == [4, 3]
    assert selection.tier1_count == 2
    assert selection.tier2_count == 0


def test_select_targets_limit_counts_partial_tier1(tiered_targets):
    """Tier counts describe the SELECTED set, not the scanned one."""
    _seed_strategy(tiered_targets)
    selection = backfill.select_targets(tiered_targets, limit=1)
    assert [t[0] for t in selection.targets] == [4]
    assert (selection.tier1_count, selection.tier2_count) == (1, 0)

    selection = backfill.select_targets(tiered_targets, limit=3)
    assert [t[0] for t in selection.targets] == [4, 3, 1]
    assert (selection.tier1_count, selection.tier2_count) == (2, 1)


def test_select_targets_without_strategy_falls_back_to_deadline_order(tiered_targets):
    """No operator strategy at all -> unchanged legacy ordering."""
    selection = backfill.select_targets(tiered_targets)
    assert [t[0] for t in selection.targets] == [1, 2, 4, 3]
    assert selection.operator_priority is False
    assert (selection.tier1_count, selection.tier2_count) == (0, 4)


def test_select_targets_empty_watch_rules_falls_back(tiered_targets):
    """A strategy with no watch rules matches everything -> not a usable gate."""
    _seed_strategy(
        tiered_targets,
        required_keywords="",
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        exclude_keywords="",
    )
    selection = backfill.select_targets(tiered_targets)
    assert [t[0] for t in selection.targets] == [1, 2, 4, 3]
    assert selection.operator_priority is False


def test_select_targets_no_operator_priority_restores_legacy_order(tiered_targets):
    """The --no-operator-priority escape hatch ignores an existing gate."""
    _seed_strategy(tiered_targets)
    selection = backfill.select_targets(tiered_targets, operator_priority=False)
    assert [t[0] for t in selection.targets] == [1, 2, 4, 3]
    assert selection.operator_priority is False


def test_select_targets_does_not_create_operator_rows(tiered_targets):
    """Target selection is read-only: it must not seed an operator/strategy row."""
    backfill.select_targets(tiered_targets)
    assert tiered_targets.query(User).count() == 0
    assert tiered_targets.query(OperatorStrategy).count() == 0


def test_select_targets_gate_projects_are_never_persisted(tiered_targets):
    """The transient Projects built for the gate must not reach the session.

    The tier decision needs a Project-shaped object per candidate row; those are
    constructed detached, so a later commit in the run loop must not insert them.
    """
    _seed_strategy(tiered_targets)
    backfill.select_targets(tiered_targets)
    tiered_targets.commit()
    assert tiered_targets.query(Project).count() == 4


def test_select_targets_scan_cap_leaves_the_tail_in_sql_order(
    tiered_targets, monkeypatch
):
    """Past the scan cap, rows keep plain deadline order (never gate-checked)."""
    monkeypatch.setattr(backfill, "OPERATOR_PRIORITY_SCAN_CEILING", 2)
    monkeypatch.setattr(backfill, "OPERATOR_PRIORITY_SCAN_FLOOR", 1)
    monkeypatch.setattr(backfill, "OPERATOR_PRIORITY_SCAN_MULTIPLIER", 1)
    _seed_strategy(tiered_targets)

    selection = backfill.select_targets(tiered_targets)
    # Only ids 1, 2 (the two most imminent) are scanned; both are tier 2, so the
    # unscanned tail (4, 3) is appended in deadline order behind them.
    assert [t[0] for t in selection.targets] == [1, 2, 4, 3]
    assert selection.tier1_count == 0


def test_load_targets_is_a_thin_wrapper_over_select_targets(tiered_targets):
    """load_targets returns exactly the selection's target list."""
    _seed_strategy(tiered_targets)
    assert backfill.load_targets(tiered_targets) == (
        backfill.select_targets(tiered_targets).targets
    )


def test_operator_priority_scan_cap_is_clamped():
    """Scan cap = limit x multiplier, clamped into [floor, ceiling]."""
    floor = backfill.OPERATOR_PRIORITY_SCAN_FLOOR
    ceiling = backfill.OPERATOR_PRIORITY_SCAN_CEILING
    multiplier = backfill.OPERATOR_PRIORITY_SCAN_MULTIPLIER

    assert backfill.operator_priority_scan_cap(None) == ceiling
    assert backfill.operator_priority_scan_cap(1) == floor  # tiny limit -> floor
    assert backfill.operator_priority_scan_cap(ceiling) == ceiling  # huge -> ceiling
    mid = (floor // multiplier) * 2
    assert backfill.operator_priority_scan_cap(mid) == mid * multiplier


def test_partition_by_tier_preserves_input_order():
    """The partition is pure and stable within each bucket."""
    rows = [(1, "A", None, None), (2, "B", None, None), (3, "C", None, None)]
    tier1, tier2 = backfill.partition_by_tier(rows, lambda row: row[0] % 2 == 1)
    assert [r[0] for r in tier1] == [1, 3]
    assert [r[0] for r in tier2] == [2]


def test_parser_defaults_operator_priority_on():
    """Priority is the default; the flag is opt-out only."""
    assert backfill.build_parser().parse_args([]).no_operator_priority is False
    opted_out = backfill.build_parser().parse_args(["--no-operator-priority"])
    assert opted_out.no_operator_priority is True


def test_stats_report_tier_counts():
    """The run summary carries the tier breakdown (dry-run included)."""
    stats = backfill.BackfillStats(dry_run=True, target_count=3)
    stats.tier1_selected = 2
    stats.tier2_selected = 1
    stats.operator_priority = True
    summary = stats.as_dict()
    assert summary["tier1_selected"] == 2
    assert summary["tier2_selected"] == 1
    assert summary["operator_priority"] is True


def test_priority_line_mentions_operator_candidate_count():
    """The one-line KST summary names how many operator candidates were included."""
    line = backfill._priority_line(
        backfill.TargetSelection(
            targets=[], tier1_count=7, tier2_count=3, operator_priority=True
        )
    )
    assert "운영자 후보 우선 7건 포함" in line
    assert "tier1_selected=7" in line and "tier2_selected=3" in line
    assert "KST" in line

    off = backfill._priority_line(
        backfill.TargetSelection(
            targets=[], tier1_count=0, tier2_count=3, operator_priority=False
        )
    )
    assert "미적용" in off


def test_main_dry_run_reports_operator_priority(tiered_targets, monkeypatch, capsys):
    """End-to-end CLI wiring: --dry-run tiers the set, prints it, makes no call."""
    _seed_strategy(tiered_targets)
    monkeypatch.setattr(backfill, "SessionLocal", lambda: tiered_targets)

    assert backfill.main(["--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "운영자 후보 우선 2건 포함" in out
    assert "tier1_selected=2" in out
    # The dry-run sample is the tiered order: operator candidates first.
    assert "'R0004', 'R0003'" in out


def test_main_no_operator_priority_reports_legacy_order(
    tiered_targets, monkeypatch, capsys
):
    """The opt-out flag reaches selection and is reflected in the summary line."""
    _seed_strategy(tiered_targets)
    monkeypatch.setattr(backfill, "SessionLocal", lambda: tiered_targets)

    assert backfill.main(["--dry-run", "--no-operator-priority"]) == 0

    out = capsys.readouterr().out
    assert "미적용" in out
    assert "'R0001', 'R0002'" in out


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


def test_run_backfill_skips_implausible_floor_rate(seeded_targets):
    """게시 하한율이 성립 불가값이면 쓰지 않고, ``no_value`` 와 구분해 따로 센다.

    ``no_value`` 는 "응답에 하한율이 없었다"이고 이쪽은 "값은 왔는데 하한으로 성립하지
    않는다"라, 회계를 합치면 원문 품질 문제가 응답 결손에 묻힌다.
    """
    targets = backfill.load_targets(seeded_targets)  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "100"}]),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "89.745"}]),
        }
    )

    stats = backfill.run_backfill(
        seeded_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=_FakeLicenseLimitFetch(),
        delay=0,
        chunk_size=1,
    )

    assert stats.processed == 2
    assert stats.updated == 1
    assert stats.floor_implausible == 1
    assert stats.no_value == 0  # 별도 회계 — 응답 결손이 아니다
    rows = {r.id: r for r in seeded_targets.query(Project).all()}
    assert rows[2].award_floor_rate is None  # 비개연 값은 NULL 로 남는다
    assert rows[1].award_floor_rate == pytest.approx(0.89745)


def test_backfill_stats_report_floor_implausible(seeded_targets):
    """새 카운터는 요약 dict 에도 노출된다(운영자가 회계를 볼 수 있어야 한다)."""
    stats = backfill.BackfillStats(floor_implausible=3)

    assert stats.as_dict()["floor_implausible"] == 3


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


# --- Floor-only mode (orphan sweep, opt-in) -----------------------------------


def test_mode_writes_eligibility_predicate():
    """Default mode writes eligibility; floor-only mode never does (pure predicate)."""
    assert backfill.mode_writes_eligibility(backfill.DEFAULT_MODE) is True
    assert backfill.mode_writes_eligibility(backfill.FLOOR_ONLY_MODE) is False


def test_parser_floor_only_defaults_off():
    """--floor-only is opt-in; absent it stays False (default backfill unchanged)."""
    assert backfill.build_parser().parse_args([]).floor_only is False
    assert backfill.build_parser().parse_args(["--floor-only"]).floor_only is True


@pytest.fixture
def orphan_floor_targets(test_db):
    """Floor-NULL-but-eligibility-set orphans plus rows the floor-only key excludes.

    These are the rows no default run can ever reach: saving eligibility_raw drops
    them from the default (eligibility IS NULL) key while their floor is still NULL.
    """
    now = utc_now()
    test_db.add_all(
        [
            # orphan: floor NULL, eligibility set, open, future (later)
            Project(
                id=1,
                notice_number="R0001",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw={"flags": {"indstrytyLmtYn": "N"}},
                deadline=now + timedelta(days=5),
            ),
            # orphan: floor NULL, eligibility set, open, future (sooner -> first)
            Project(
                id=2,
                notice_number="R0002",
                category="construction",
                status="open",
                award_floor_rate=None,
                eligibility_raw={"flags": {"indstrytyLmtYn": "Y"}},
                deadline=now + timedelta(days=1),
            ),
            # excluded (floor-only): floor already set -> not floor-NULL
            Project(
                id=3,
                notice_number="R0003",
                category="service",
                status="open",
                award_floor_rate=0.88,
                eligibility_raw={"flags": {"indstrytyLmtYn": "N"}},
                deadline=now + timedelta(days=2),
            ),
            # excluded (floor-only): eligibility NULL -> this is the DEFAULT target
            Project(
                id=4,
                notice_number="R0004",
                category="service",
                status="open",
                award_floor_rate=None,
                eligibility_raw=None,
                deadline=now + timedelta(days=2),
            ),
            # excluded: not open
            Project(
                id=5,
                notice_number="R0005",
                category="service",
                status="closed",
                award_floor_rate=None,
                eligibility_raw={"flags": {}},
                deadline=now + timedelta(days=2),
            ),
        ]
    )
    test_db.commit()
    return test_db


def test_floor_only_selects_only_floor_null_eligibility_set_orphans(orphan_floor_targets):
    """Floor-only key = floor NULL AND eligibility NOT NULL (open, in window)."""
    targets = backfill.load_targets(
        orphan_floor_targets, mode=backfill.FLOOR_ONLY_MODE
    )
    ids = [t[0] for t in targets]
    # ids 2, 1 qualify, deadline asc; the eligibility-NULL and floor-set rows drop.
    assert ids == [2, 1]
    assert 3 not in ids  # floor already set
    assert 4 not in ids  # eligibility NULL (a default-mode target, not floor-only)
    assert 5 not in ids  # closed


def test_default_mode_never_reaches_the_floor_only_orphans(orphan_floor_targets):
    """The gap the PR closes: the default key can only see the eligibility-NULL row.

    Ids 1 and 2 carry eligibility already, so the default (eligibility IS NULL)
    sweep can never fill their floor — only --floor-only reaches them.
    """
    ids = [t[0] for t in backfill.load_targets(orphan_floor_targets)]
    assert ids == [4]


def test_floor_only_reuses_operator_tiering(test_db):
    """Floor-only reuses the operator-candidate-first tiering (same sweep)."""
    now = utc_now()
    elig = {"flags": {"indstrytyLmtYn": "N"}}
    test_db.add_all(
        [
            # off-scope, most imminent -> tier 2 despite the earliest deadline
            Project(
                id=1,
                notice_number="R0001",
                title="○○청사 신축공사",
                description="공고기관: ○○시청",
                requirements="철근콘크리트 구조",
                category="construction",
                status="open",
                award_floor_rate=None,
                eligibility_raw=elig,
                deadline=now + timedelta(days=1),
            ),
            # operator candidate (marine gate), later deadline -> tier 1
            Project(
                id=2,
                notice_number="R0002",
                title="○○항 준설공사",
                description="공고기관: ○○지방해양수산청",
                requirements="항만 준설 및 사석 투하",
                category="construction",
                status="open",
                award_floor_rate=None,
                eligibility_raw=elig,
                deadline=now + timedelta(days=5),
            ),
        ]
    )
    test_db.commit()
    _seed_strategy(test_db)  # required_keywords="항만,방파제"

    selection = backfill.select_targets(test_db, mode=backfill.FLOOR_ONLY_MODE)
    # tier 1 (id 2, marine) before tier 2 (id 1) despite id 1's earlier deadline.
    assert [t[0] for t in selection.targets] == [2, 1]
    assert selection.operator_priority is True
    assert (selection.tier1_count, selection.tier2_count) == (1, 1)


def test_floor_only_fills_floor_without_sub_call_or_eligibility_write(
    orphan_floor_targets,
):
    """Core regression: floor-only writes only the floor.

    Both target notices carry a Y flag — in the DEFAULT mode that would trigger a
    license-limit sub-call — but floor-only must ignore the flag, make no sub-call,
    and never overwrite the existing eligibility_raw (the daily quota is protected).
    """
    targets = backfill.load_targets(
        orphan_floor_targets, mode=backfill.FLOOR_ONLY_MODE
    )  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload(
                [{"bidNtceOrd": "3", "sucsfbidLwltRate": "87", "indstrytyLmtYn": "Y"}]
            ),
            "R0001": _payload(
                [{"bidNtceOrd": "1", "sucsfbidLwltRate": "88", "indstrytyLmtYn": "Y"}]
            ),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        orphan_floor_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        chunk_size=1,
        mode=backfill.FLOOR_ONLY_MODE,
    )

    assert stats.updated == 2
    assert stats.no_value == 0
    assert stats.eligibility_saved == 0  # floor-only never writes eligibility
    assert stats.license_limit_calls == 0
    assert license_limit.calls == []  # NO sub-call despite the Y flag
    rows = {r.id: r for r in orphan_floor_targets.query(Project).all()}
    assert rows[2].award_floor_rate == pytest.approx(0.87)
    assert rows[1].award_floor_rate == pytest.approx(0.88)
    # existing eligibility preserved verbatim, never overwritten
    assert rows[2].eligibility_raw == {"flags": {"indstrytyLmtYn": "Y"}}
    assert rows[1].eligibility_raw == {"flags": {"indstrytyLmtYn": "N"}}


def test_floor_only_no_value_leaves_floor_null_and_reselects(orphan_floor_targets):
    """Known limitation: a no_value row keeps floor NULL and is re-selected next run."""
    targets = backfill.load_targets(
        orphan_floor_targets, mode=backfill.FLOOR_ONLY_MODE
    )  # ids 2, 1
    fetch = _FakeFetch(
        {
            "R0002": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "n/a"}]),
            "R0001": _payload([{"bidNtceOrd": "1", "sucsfbidLwltRate": "88"}]),
        }
    )
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        orphan_floor_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        delay=0,
        mode=backfill.FLOOR_ONLY_MODE,
    )

    assert stats.no_value == 1
    assert stats.updated == 1
    assert license_limit.calls == []
    rows = {r.id: r for r in orphan_floor_targets.query(Project).all()}
    assert rows[2].award_floor_rate is None  # no_value stays NULL
    assert rows[1].award_floor_rate == pytest.approx(0.88)
    # the no_value row still qualifies (floor NULL + eligibility set); id 1 dropped.
    remaining = [
        t[0]
        for t in backfill.load_targets(
            orphan_floor_targets, mode=backfill.FLOOR_ONLY_MODE
        )
    ]
    assert remaining == [2]


def test_floor_only_dry_run_counts_without_fetch(orphan_floor_targets):
    """Floor-only --dry-run reports the orphan count and sample; makes no call."""
    targets = backfill.load_targets(
        orphan_floor_targets, mode=backfill.FLOOR_ONLY_MODE
    )
    fetch = _FakeFetch({})  # any call would KeyError
    license_limit = _FakeLicenseLimitFetch()

    stats = backfill.run_backfill(
        orphan_floor_targets,
        targets,
        fetch=fetch,
        fetch_license_limit=license_limit,
        dry_run=True,
        mode=backfill.FLOOR_ONLY_MODE,
        sample_size=5,
    )

    assert fetch.calls == []
    assert license_limit.calls == []
    assert stats.target_count == 2
    assert stats.sample == ["R0002", "R0001"]


def test_main_floor_only_dry_run_targets_orphans(
    orphan_floor_targets, monkeypatch, capsys
):
    """End-to-end CLI wiring: --floor-only --dry-run sweeps orphans, labels the mode."""
    monkeypatch.setattr(backfill, "SessionLocal", lambda: orphan_floor_targets)

    assert backfill.main(["--floor-only", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "[floor-only]" in out
    assert "target=2" in out
    assert "'R0002', 'R0001'" in out
