"""Declarative constants for the synthetic-experiment domain.

Fixed G-1 presets, budget-band boundaries, price-only/eligibility verdict keys,
CSV export columns, and A/B compare metric keys. No logic -- interpreters live in
the sibling modules.
"""

from __future__ import annotations

from typing import Any


RUN_STATUS_QUEUED = "queued"
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"
SYNTHETIC_OPERATOR_SAMPLE_TARGET = 30
SYNTHETIC_RUN_TOTAL_SAMPLE_TARGET = 100
SYNTHETIC_REPORT_GROUP_SAMPLE_TARGET = 30
SAMPLE_STATUS_SUFFICIENT = "sufficient"
SAMPLE_STATUS_INSUFFICIENT = "insufficient_sample"
REPORT_STATUS_READY = "ready_for_reporting"
REPORT_STATUS_DATA_MIXED = "canonical_synthetic_mixed"
SAMPLE_GAP_WARNING_MIXED_DATA = "canonical_synthetic_mixed"
SAMPLE_GAP_WARNING_LEGACY_SUMMARY = "legacy_summary_without_sample_report"
SAMPLE_GAP_DIMENSION_ORDER = {
    "preset": 0,
    "category": 1,
    "business_type": 2,
    "budget_band": 3,
}
SYNTHETIC_SETTLE_ACTIONS = ("bid_now", "review", "skip")

_G1_PRESET_WINDOW = {
    "start_at": "2025-01-01T00:00:00+00:00",
    "end_at": "2025-12-31T23:59:59+00:00",
    "limit": 200,
    "scenario": "base",
    "settle_actions": False,
}

SYNTHETIC_EXPERIMENT_PRESETS: dict[str, dict[str, Any]] = {
    "g1-construction-base-12m": {
        "description": "G-1 공사 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "construction"},
        "operator_slugs": [
            "cn-small-gangwon",
            "cn-mid-gyeonggi",
            "cn-electric-telecom-national",
        ],
    },
    "g1-service-base-12m": {
        "description": "G-1 일반/기술 용역 2025년 12개월 base preset",
        # Mixes general-service and technical-service operators. Leave category
        # unset so each operator's focus categories scope its award pool.
        "params": {**_G1_PRESET_WINDOW, "category": None},
        "operator_slugs": [
            "eng-supervision-busan",
            "eng-design-daejeon",
            "gs-cleaning-metro",
            "gs-security-national",
        ],
    },
    "g1-goods-base-12m": {
        "description": "G-1 물품 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "goods"},
        "operator_slugs": [
            "gd-office-sme",
            "gd-it-equipment-midcap",
        ],
    },
    "g1-software-base-12m": {
        "description": "G-1 소프트웨어 입찰 2025년 12개월 base preset",
        "params": {**_G1_PRESET_WINDOW, "category": "software"},
        "operator_slugs": [
            "sw-small-seoul",
            "sw-mid-metro",
            "sw-large-national",
        ],
    },
}

# Budget-band boundaries (KRW). A settlement is placed into the first band whose
# upper bound it is *strictly* below; the final band catches everything at or
# above the last boundary. Mirrors common 나라장터 budget tiers (1억/5억/10억/50억).
BUDGET_BAND_BOUNDARIES: tuple[tuple[str, float], ...] = (
    ("lt_1eok", 100_000_000.0),
    ("1eok_5eok", 500_000_000.0),
    ("5eok_10eok", 1_000_000_000.0),
    ("10eok_50eok", 5_000_000_000.0),
)
BUDGET_BAND_TOP_KEY = "gte_50eok"

# A settlement counts as a (price-only estimated) "win" when its price-only
# verdict is "plausible" -- IDENTICAL to ``would_have_won_price_only_count`` in
# the engine summary. This is a price-based ESTIMATE, not an actual award.
_WIN_VERDICTS = {"plausible"}

# Eligibility-gate (PR3) final verdict that counts as an estimated favorable win:
# the bid cleared the 낙찰하한가 AND landed at/under the realized winning amount.
# ``unknown`` (no 예가/하한 data) is EXCLUDED from the eligibility-rate denominator
# so the rate stays honest -- it is computed only over settlements we could judge.
_ELIGIBLE_FAVORABLE_VERDICT = "eligible_favorable"
_ELIGIBILITY_UNKNOWN_VERDICT = "unknown"

# CSV export columns (Phase 4). ``operator_slug`` is prepended to the CLI
# comparison columns (see ``scripts.backtest_synthetic_operators._write_comparison_csv``)
# so each row is self-identifying. ``win_rate_on_settled`` /
# ``win_rate_on_candidates`` remain PRICE-ONLY estimates (NOT actual awards); the
# column name is preserved unchanged so downstream readers keep that meaning.
EXPORT_CSV_COLUMNS: tuple[str, ...] = (
    "operator_slug",
    "business_type",
    "candidate_count",
    "paper_bid_count",
    "settled_count",
    "skipped_by_strategy_count",
    "would_have_won_price_only_count",
    "win_rate_on_settled",
    "win_rate_on_candidates",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
    "average_absolute_amount_error_rate",
)

# Per-operator metric keys surfaced in the A/B compare response. ``win_rate_on_settled``
# is a price-only estimate (NOT an actual award) and is carried through unchanged.
_COMPARE_METRIC_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "settled_count",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)

# Subset of compare metrics for which a signed delta (b - a) is computed. A delta
# is ``None`` when either side is missing/None (e.g. no settled rows -> win_rate None).
_COMPARE_DELTA_KEYS: tuple[str, ...] = (
    "win_rate_on_settled",
    "bid_submission_rate",
    "average_absolute_bid_rate_error",
)
