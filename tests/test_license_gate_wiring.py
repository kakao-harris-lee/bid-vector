"""License-eligibility gate wiring in StrategyMonitoringService (D2).

The gate screens recommendation candidates against the operator's held licenses
vs each notice's published 면허요건 (``Project.eligibility_raw``). Core safety
contract under test:

- **Default OFF ⇒ zero behavior change.** ``_resolve_license_gate_profile_codes``
  returns ``None`` (no DB query) while the flag is unset, and ``None`` codes make
  ``_license_gate_excludes`` a no-op for every notice — so no candidate can be
  dropped by a disabled gate.
- **ON ⇒ only ineligible excluded.** A data-confirmed ``ineligible`` verdict drops
  the candidate before the expensive ML analysis; ``unknown`` (no requirement data
  or no held-license data — the 92% coverage gap) and ``eligible`` pass through
  unchanged, so the gate can only lose precision, never recall (§2 정직).
- The exclusion reason (요구 면허) is logged for audit.

These mirror ``test_strategy_keyword_filter``: the gate helpers need no DB, so the
unit cases are built from in-memory ``Project`` / ``OperatorStrategy`` instances,
and the loop-level case drives ``_collect_candidate_evaluations`` with lightweight
fakes.
"""
from __future__ import annotations

import logging

from app.models.models import OperatorStrategy, Project
from app.services import opportunity_monitoring
from app.services.opportunity_monitoring import StrategyMonitoringService

# A notice whose published 면허요건 the marine/telecom operator provably lacks.
WASTE_LIMITS = {
    "license_limits": [
        {"lcnsLmtNm": "건설폐기물 중간처리업/1253", "lmtGrpNo": "1", "lmtSno": "1"},
    ]
}
# Held licenses unrelated to waste processing → the waste requirement is ineligible.
HELD_LICENSES = "정보통신공사업"


def _service() -> StrategyMonitoringService:
    return StrategyMonitoringService()


def _project(**overrides) -> Project:
    base = dict(
        id=1,
        title="공고",
        description="",
        requirements="",
        category="technical-service",
        budget_estimate=100_000_000.0,
        eligibility_raw=None,
        notice_number="R0001",
    )
    base.update(overrides)
    return Project(**base)


# ---------------------------------------------------------------------------
# _license_gate_excludes — pure verdict → drop/pass mapping
# ---------------------------------------------------------------------------


def test_excludes_ineligible_notice():
    """A data-confirmed ineligible notice is dropped by the gate."""
    project = _project(eligibility_raw=WASTE_LIMITS)
    assert _service()._license_gate_excludes(project, HELD_LICENSES) is True


def test_ineligible_exclusion_logs_reason(caplog):
    """The exclusion records an audit reason with the required license names."""
    project = _project(id=42, eligibility_raw=WASTE_LIMITS)
    with caplog.at_level(logging.INFO, logger="app.services.opportunity_monitoring"):
        assert _service()._license_gate_excludes(project, HELD_LICENSES) is True
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "project_id=42" in messages
    assert "필수면허 미보유" in messages
    assert "건설폐기물 중간처리업" in messages


def test_unknown_notice_passes():
    """A notice with no 면허요건 data is unknown → passes (never suppressed)."""
    project = _project(eligibility_raw=None)
    assert _service()._license_gate_excludes(project, HELD_LICENSES) is False


def test_eligible_notice_passes():
    """A notice whose required license the operator holds passes."""
    project = _project(eligibility_raw=WASTE_LIMITS)
    # Operator now holds exactly the waste license the notice requires.
    assert _service()._license_gate_excludes(project, "건설폐기물 중간처리업/1253") is False


def test_missing_profile_codes_is_neutral():
    """No held-license data → unknown → passes (미기재 is not 미보유, §2 정직)."""
    project = _project(eligibility_raw=WASTE_LIMITS)
    assert _service()._license_gate_excludes(project, "") is False


def test_none_codes_is_gate_off_noop():
    """None codes (gate disabled) never exclude, even an otherwise-ineligible notice."""
    project = _project(eligibility_raw=WASTE_LIMITS)
    assert _service()._license_gate_excludes(project, None) is False


# ---------------------------------------------------------------------------
# _resolve_license_gate_profile_codes — flag gating + no query while OFF
# ---------------------------------------------------------------------------


class _ExplodingDB:
    """DB double that fails if touched — proves the OFF path issues no query."""

    def query(self, *args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("license gate must not query the DB while disabled")


class _FakeOperator:
    id = 7


def test_resolve_codes_returns_none_and_skips_db_when_disabled(monkeypatch):
    """Flag OFF → returns None without querying the DB (true early-return safety)."""
    monkeypatch.setattr(
        opportunity_monitoring.settings, "LICENSE_ELIGIBILITY_GATE_ENABLED", False
    )
    result = _service()._resolve_license_gate_profile_codes(
        _ExplodingDB(), _FakeOperator()
    )
    assert result is None


def test_resolve_codes_reads_profile_when_enabled(monkeypatch):
    """Flag ON → returns the operator profile's license_codes."""
    monkeypatch.setattr(
        opportunity_monitoring.settings, "LICENSE_ELIGIBILITY_GATE_ENABLED", True
    )

    class _Profile:
        license_codes = HELD_LICENSES

    monkeypatch.setattr(
        opportunity_monitoring,
        "ensure_operator_profile_for",
        lambda db, operator: _Profile(),
    )
    result = _service()._resolve_license_gate_profile_codes(object(), _FakeOperator())
    assert result == HELD_LICENSES


# ---------------------------------------------------------------------------
# _collect_candidate_evaluations — loop-level: OFF unchanged, ON drops ineligible
# ---------------------------------------------------------------------------


class _FakeQuery:
    def __init__(self, projects):
        self._projects = projects

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def yield_per(self, *args, **kwargs):
        # The open-notice scan streams rows in batches (no row bound): the
        # collection loop only iterates the result.
        return list(self._projects)


class _FakeDB:
    def __init__(self, projects):
        self._projects = projects

    def query(self, *args, **kwargs):
        return _FakeQuery(self._projects)


def _passing_analysis() -> dict:
    """Canned analysis that clears the monitor thresholds."""
    return {
        "matched_score": 0.9,
        "probability_score": 0.9,
        "recommended_amount": 1_000.0,
        "analysis_summary": "",
        "decision": {"priority_score": 0.9, "action": "review", "pursue_bid": True},
    }


def _keyword_strategy() -> OperatorStrategy:
    """A strategy with a watch rule so _has_configured_watch_rules is True."""
    return OperatorStrategy(
        focus_categories="",
        focus_regions="",
        exclude_regions="",
        required_keywords="용역",
        exclude_keywords="",
        min_budget_estimate=0.0,
        max_budget_estimate=0.0,
        minimum_match_score=0.0,
        minimum_probability_score=0.0,
    )


def _collect(service, db, operator) -> list:
    evaluations, _ = service._collect_candidate_evaluations(
        db,
        strategy=_keyword_strategy(),
        operator=operator,
        high_priority_only=False,
        max_active_bids=3,
        current_workload_score=None,
        same_category_only=True,
        similar_limit=3,
        min_similarity=0.15,
    )
    return evaluations


def _wire_loop_fakes(monkeypatch, service):
    """Stub the expensive ML analysis so the loop exercises only the gate wiring."""
    monkeypatch.setattr(
        service, "_analyze_project", lambda *a, **k: _passing_analysis()
    )


def test_collect_unchanged_when_gate_disabled(monkeypatch):
    """Flag OFF: both an ineligible and an unknown notice survive (list unchanged)."""
    monkeypatch.setattr(
        opportunity_monitoring.settings, "LICENSE_ELIGIBILITY_GATE_ENABLED", False
    )
    ineligible = _project(id=1, title="폐기물 처리 용역", eligibility_raw=WASTE_LIMITS)
    unknown = _project(id=2, title="시스템 유지보수 용역", eligibility_raw=None)
    service = _service()
    _wire_loop_fakes(monkeypatch, service)

    evaluations = _collect(service, _FakeDB([ineligible, unknown]), _FakeOperator())

    assert {ev.project_id for ev in evaluations} == {1, 2}


def test_collect_drops_ineligible_when_gate_enabled(monkeypatch):
    """Flag ON: the ineligible notice is dropped; the unknown one still passes."""
    monkeypatch.setattr(
        opportunity_monitoring.settings, "LICENSE_ELIGIBILITY_GATE_ENABLED", True
    )

    class _Profile:
        license_codes = HELD_LICENSES

    monkeypatch.setattr(
        opportunity_monitoring,
        "ensure_operator_profile_for",
        lambda db, operator: _Profile(),
    )
    ineligible = _project(id=1, title="폐기물 처리 용역", eligibility_raw=WASTE_LIMITS)
    unknown = _project(id=2, title="시스템 유지보수 용역", eligibility_raw=None)
    service = _service()
    _wire_loop_fakes(monkeypatch, service)

    evaluations = _collect(service, _FakeDB([ineligible, unknown]), _FakeOperator())

    assert {ev.project_id for ev in evaluations} == {2}
