"""Honest reporting + field-level breakdown for the synthetic backtest CLI.

Covers PR6 (honest reporting breakdown):

* (A) the comparison summary/CSV use PRICE-ONLY/eligibility-qualified names and
  drop the bare ``win_rate`` qualifier (CLI artifacts have no frontend consumer);
* (B) field-level breakdown reaches the output stage as sibling CSVs carrying
  per-group sample size (``settled_count``) + freshness (``latest_result_time``);
* the eligibility-gate estimate (``eligible_favorable_rate``) excludes
  ``unknown`` settlements from its denominator;
* (C) the per-operator award window is scoped to the operator's focus
  categories so a minority/recently-backfilled category (goods) is not starved
  by the global award pool.
"""

from __future__ import annotations

import csv
import io

from scripts.backtest_synthetic_operators import (
    _condense_summary,
    _write_breakdown_csv,
    _write_comparison_csv,
)


# --- (A) honest naming in condensed summary -----------------------------------


def _engine_summary(**overrides):
    base = {
        "candidate_count": 10,
        "paper_bid_count": 8,
        "settled_count": 8,
        "skipped_by_strategy_count": 2,
        "would_have_won_price_only_count": 4,
        "would_have_won_final_eligible_favorable_count": 3,
        "would_have_won_final_unknown_count": 2,
        "average_absolute_bid_rate_error": 0.01,
        "average_absolute_amount_error_rate": 0.02,
    }
    base.update(overrides)
    return base


def test_condense_summary_uses_price_only_qualified_names():
    condensed = _condense_summary(_engine_summary())
    # Honest, qualified names present; bare ``win_rate_*`` names are gone.
    assert condensed["est_price_close_rate_on_settled"] == 0.5  # 4 / 8
    assert condensed["est_price_close_rate_on_candidates"] == 0.4  # 4 / 10
    assert "win_rate_on_settled" not in condensed
    assert "win_rate_on_candidates" not in condensed


def test_condense_summary_eligible_favorable_rate_excludes_unknown():
    # settled=8, unknown=2 -> judged=6; favorable=3 -> rate=0.5.
    condensed = _condense_summary(_engine_summary())
    assert condensed["eligibility_judged_count"] == 6
    assert condensed["eligible_favorable_rate"] == 0.5
    assert condensed["would_have_won_final_eligible_favorable_count"] == 3


def test_condense_summary_eligible_rate_none_when_all_unknown():
    summary = _engine_summary(
        settled_count=3,
        would_have_won_final_eligible_favorable_count=0,
        would_have_won_final_unknown_count=3,
    )
    condensed = _condense_summary(summary)
    assert condensed["eligibility_judged_count"] == 0
    assert condensed["eligible_favorable_rate"] is None


# --- (A) honest CSV header ----------------------------------------------------


def _comparison_row(slug, business_type, summary, breakdown=None):
    return {
        "slug": slug,
        "operator": {"business_type": business_type},
        "summary": summary,
        "breakdown": breakdown or {"by_category": [], "by_budget_band": []},
    }


def test_comparison_csv_header_has_no_bare_win_rate(tmp_path):
    path = tmp_path / "comparison.csv"
    rows = [
        _comparison_row(
            "gd-office-sme", "goods", _condense_summary(_engine_summary())
        )
    ]
    _write_comparison_csv(path, rows)

    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8")))
    header = next(reader)
    # Bare/ambiguous ``win_rate*`` columns are forbidden -- only qualified names.
    assert not any(col.startswith("win_rate") for col in header)
    assert "est_price_close_rate_on_settled" in header
    assert "est_price_close_rate_on_candidates" in header
    assert "eligible_favorable_rate" in header
    assert "eligibility_judged_count" in header


# --- (B) field-level breakdown sibling CSVs -----------------------------------


def _breakdown_fixture():
    return {
        "by_category": [
            {
                "category": "goods",
                "settled_count": 12,
                "would_have_won_count": 5,
                "win_rate": 0.4167,
                "est_price_close_rate": 0.4167,
                "eligible_favorable_count": 4,
                "eligibility_unknown_count": 2,
                "eligibility_judged_count": 10,
                "eligible_favorable_rate": 0.4,
                "avg_abs_bid_rate_error": 0.008,
                "latest_result_time": "2026-06-10T00:00:00+00:00",
            }
        ],
        "by_budget_band": [
            {
                "budget_band": "1eok_5eok",
                "settled_count": 7,
                "would_have_won_count": 3,
                "win_rate": 0.4286,
                "est_price_close_rate": 0.4286,
                "eligible_favorable_count": 2,
                "eligibility_unknown_count": 1,
                "eligibility_judged_count": 6,
                "eligible_favorable_rate": 0.3333,
                "avg_abs_bid_rate_error": 0.006,
                "latest_result_time": "2026-05-20T00:00:00+00:00",
            }
        ],
    }


def test_write_breakdown_by_category_csv_has_sample_and_freshness(tmp_path):
    path = tmp_path / "comparison_by_category.csv"
    rows = [
        _comparison_row(
            "gd-office-sme",
            "goods",
            _condense_summary(_engine_summary()),
            breakdown=_breakdown_fixture(),
        )
    ]
    _write_breakdown_csv(
        path, rows, breakdown_key="by_category", group_label="category"
    )

    parsed = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert len(parsed) == 1
    row = parsed[0]
    assert row["slug"] == "gd-office-sme"
    assert row["category"] == "goods"
    # Sample size + freshness reach the CSV.
    assert row["settled_count"] == "12"
    assert row["latest_result_time"] == "2026-06-10T00:00:00+00:00"
    # Honest price-only estimate + eligibility estimate both present; no bare
    # ``win_rate`` column.
    assert row["est_price_close_rate"] == "0.4167"
    assert row["eligible_favorable_rate"] == "0.4"
    assert "win_rate" not in row


def test_write_breakdown_by_budget_band_csv(tmp_path):
    path = tmp_path / "comparison_by_budget_band.csv"
    rows = [
        _comparison_row(
            "gd-office-sme",
            "goods",
            _condense_summary(_engine_summary()),
            breakdown=_breakdown_fixture(),
        )
    ]
    _write_breakdown_csv(
        path, rows, breakdown_key="by_budget_band", group_label="budget_band"
    )
    parsed = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert parsed[0]["budget_band"] == "1eok_5eok"
    assert parsed[0]["settled_count"] == "7"
    assert parsed[0]["latest_result_time"] == "2026-05-20T00:00:00+00:00"


def test_write_breakdown_csv_header_only_when_no_groups(tmp_path):
    path = tmp_path / "comparison_by_category.csv"
    rows = [_comparison_row("empty-op", "goods", _condense_summary(_engine_summary()))]
    _write_breakdown_csv(
        path, rows, breakdown_key="by_category", group_label="category"
    )
    parsed = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))))
    assert parsed == []
