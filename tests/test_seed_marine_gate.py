"""Tests for scripts/seed_marine_gate.py.

Verifies the canonical-operator marine gate is:
  * dry-run by default — outputs a before→after plan but does NOT commit;
  * applied (--apply) — sets profile.license_codes (4 groups),
    strategy.focus_categories (3: technical-service/service/construction) and
    required_keywords (32) with unlimited budget, leaving amount fields /
    thresholds untouched;
  * idempotent — re-running --apply yields the same persisted state.

Uses the SQLite ``test_db`` fixture (conftest.py); the script's
``run_marine_gate(db, apply=...)`` accepts an injected session so no real DB is
touched.
"""

from __future__ import annotations

# Import ORM models at module top so SQLAlchemy registers their tables on
# Base.metadata before the `test_db` fixture runs create_all.
from app.core.single_user import (  # noqa: F401
    ensure_operator_profile,
    ensure_operator_strategy,
    split_multi_value_text,
)
from app.models.models import (  # noqa: F401 — registers table metadata
    CompanyProfile,
    OperatorStrategy,
    User,
)
from scripts.seed_marine_gate import (
    MARINE_BUSINESS_TYPE,
    MARINE_FOCUS_CATEGORIES,
    MARINE_LICENSE_CODES,
    MARINE_REQUIRED_KEYWORDS,
    run_marine_gate,
)


def test_required_keyword_catalogue_is_unique():
    # 32 unique tokens after dropping 6 over-matching generic terms.
    assert len(MARINE_REQUIRED_KEYWORDS) == 32
    assert len(set(MARINE_REQUIRED_KEYWORDS)) == 32


def test_removed_generic_keywords_are_absent():
    """The 6 over-matching generic terms must not be in the catalogue."""
    removed = {"해양", "수산", "해상", "매립", "등대", "수중"}
    assert removed.isdisjoint(set(MARINE_REQUIRED_KEYWORDS))


def test_retained_specific_keywords_present():
    """Specific marine-engineering compounds are retained."""
    for kept in ("항만", "방파제", "준설", "해양조사", "해양환경", "항만설계", "마리나"):
        assert kept in MARINE_REQUIRED_KEYWORDS


def test_focus_categories_include_construction():
    """construction is in focus_categories so 해양 공사가 키워드 AND 로 통과한다."""
    assert MARINE_FOCUS_CATEGORIES == ["technical-service", "service", "construction"]
    assert "construction" in MARINE_FOCUS_CATEGORIES


def test_dry_run_outputs_plan_without_committing(test_db):
    result = run_marine_gate(test_db, apply=False)

    assert result["applied"] is False
    assert result["status"] == "dry-run"
    # The plan reflects the intended marine values…
    assert result["after"]["business_type"] == MARINE_BUSINESS_TYPE
    assert result["after"]["license_codes"] == MARINE_LICENSE_CODES
    assert result["after"]["focus_categories"] == MARINE_FOCUS_CATEGORIES
    assert len(result["after"]["required_keywords"]) == 32
    assert result["after"]["min_budget_estimate"] == 0.0
    assert result["after"]["max_budget_estimate"] == 0.0

    # …but nothing the gate touches is persisted (dry-run rolled back).
    profile = ensure_operator_profile(test_db)
    strategy = ensure_operator_strategy(test_db)
    assert split_multi_value_text(profile.license_codes) == []
    assert split_multi_value_text(strategy.required_keywords) == []
    # The default-created profile keeps its bootstrap business_type.
    assert profile.business_type != MARINE_BUSINESS_TYPE


def test_apply_sets_profile_and_strategy(test_db):
    result = run_marine_gate(test_db, apply=True)

    assert result["applied"] is True
    assert result["status"] == "applied"

    profile = ensure_operator_profile(test_db)
    strategy = ensure_operator_strategy(test_db)

    assert profile.business_type == MARINE_BUSINESS_TYPE
    license_codes = split_multi_value_text(profile.license_codes)
    assert license_codes == MARINE_LICENSE_CODES
    assert len(license_codes) == 4
    assert split_multi_value_text(profile.region_codes) == ["전국"]

    focus = split_multi_value_text(strategy.focus_categories)
    assert focus == MARINE_FOCUS_CATEGORIES
    assert len(focus) == 3
    assert "construction" in focus

    keywords = split_multi_value_text(strategy.required_keywords)
    assert len(keywords) == 32
    assert "항만" in keywords
    assert "방파제" in keywords
    # Dropped generic term must not leak back in.
    assert "수산" not in keywords

    assert strategy.min_budget_estimate == 0.0
    assert strategy.max_budget_estimate == 0.0


def test_apply_leaves_amount_fields_and_thresholds_untouched(test_db):
    """Amount fields + match thresholds are NOT part of the gate."""
    profile = ensure_operator_profile(test_db)
    strategy = ensure_operator_strategy(test_db)
    profile.annual_revenue = 1234.0
    profile.capacity_score = 0.42
    strategy.minimum_match_score = 0.6
    strategy.minimum_probability_score = 0.55
    test_db.commit()

    run_marine_gate(test_db, apply=True)

    profile = ensure_operator_profile(test_db)
    strategy = ensure_operator_strategy(test_db)
    assert profile.annual_revenue == 1234.0
    assert profile.capacity_score == 0.42
    assert strategy.minimum_match_score == 0.6
    assert strategy.minimum_probability_score == 0.55


def test_apply_is_idempotent(test_db):
    first = run_marine_gate(test_db, apply=True)
    second = run_marine_gate(test_db, apply=True)

    assert first["after"] == second["after"]
    # Second run's "before" already equals the marine state (no drift).
    assert second["before"] == second["after"]

    profile = ensure_operator_profile(test_db)
    assert split_multi_value_text(profile.license_codes) == MARINE_LICENSE_CODES
