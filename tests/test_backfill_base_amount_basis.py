"""Tests for scripts/backfill_base_amount_basis.py against an in-memory-style DB."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.models.models import HistoricalData, TenderResult
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_VAT,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
)

# Load the script module by path (scripts/ is not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "backfill_base_amount_basis",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_base_amount_basis.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
# Register before exec so dataclass introspection can resolve the module.
sys.modules[_SPEC.name] = backfill
_SPEC.loader.exec_module(backfill)

_YEGA_BASE = 43_996_200 / 0.88035
_VAT_BASE = 45_000_001 / 1.1


def _reserves(base: float, count: int = 15) -> str:
    spread = base * 0.025
    step = (2 * spread) / (count - 1)
    return json.dumps([round(base - spread + step * i) for i in range(count)])


@pytest.fixture
def seeded_db(test_db):
    """Four rows covering each basis + a settled/unsettled TenderResult pair."""
    test_db.add_all(
        [
            # clean integer (estimate not needed even though reserves exist)
            HistoricalData(
                id=1,
                project_id=1,
                base_amount=43_996_200.0,
                reserve_prices=_reserves(43_996_200.0),
            ),
            # derived-yega: base × rate == winning_amount; reserves ⇒ estimate
            HistoricalData(
                id=2,
                project_id=2,
                base_amount=_YEGA_BASE,
                reserve_prices=_reserves(_YEGA_BASE),
            ),
            # derived-vat: no winning result, empty reserves ⇒ estimate missing
            HistoricalData(
                id=3,
                project_id=3,
                base_amount=_VAT_BASE,
                reserve_prices="[]",
            ),
            # suspect-fractional: fractional, no winning, reserves ⇒ estimate
            HistoricalData(
                id=4,
                project_id=4,
                base_amount=12_345_678.4321,
                reserve_prices=_reserves(12_345_678.4321),
            ),
        ]
    )
    # project 2 has both an unsettled and a settled result; the backfill must
    # pick the settled (winning_amount > 0) row for the 예정가 역산 match.
    test_db.add_all(
        [
            TenderResult(project_id=2, winning_amount=0.0, winning_rate=0.0),
            TenderResult(
                project_id=2, winning_amount=43_996_200.0, winning_rate=0.88035
            ),
        ]
    )
    test_db.commit()
    return test_db


def test_dry_run_counts_without_writing(seeded_db):
    stats = backfill.run_backfill(seeded_db, apply=False)

    assert stats.scanned == 4
    assert stats.by_basis[BASIS_CLEAN] == 1
    assert stats.by_basis[BASIS_DERIVED_YEGA] == 1
    assert stats.by_basis[BASIS_DERIVED_VAT] == 1
    assert stats.by_basis[BASIS_SUSPECT_FRACTIONAL] == 1
    assert stats.estimated_filled == 2  # yega + suspect rows have reserves
    assert stats.estimated_missing == 1  # vat row has empty reserves

    # nothing persisted
    rows = seeded_db.query(HistoricalData).all()
    assert all(r.basis_checked_at is None for r in rows)
    assert all(r.base_amount_basis is None for r in rows)


def test_apply_persists_and_is_idempotent(seeded_db):
    first = backfill.run_backfill(seeded_db, apply=True)
    assert first.scanned == 4

    by_id = {r.id: r for r in seeded_db.query(HistoricalData).all()}
    assert by_id[1].base_amount_basis == BASIS_CLEAN
    assert by_id[1].base_amount_estimated is None  # clean ⇒ no estimate
    assert by_id[1].base_amount == 43_996_200.0  # original untouched
    assert by_id[2].base_amount_basis == BASIS_DERIVED_YEGA
    assert by_id[2].base_amount_estimated is not None  # reserves ⇒ estimate
    assert by_id[2].base_amount == pytest.approx(_YEGA_BASE)  # original untouched
    assert by_id[3].base_amount_basis == BASIS_DERIVED_VAT
    assert by_id[3].base_amount_estimated is None  # empty reserves
    assert by_id[4].base_amount_basis == BASIS_SUSPECT_FRACTIONAL
    assert all(r.basis_checked_at is not None for r in by_id.values())

    # second apply run skips already-stamped rows (idempotent)
    second = backfill.run_backfill(seeded_db, apply=True)
    assert second.scanned == 0


def test_recheck_reprocesses_stamped_rows(seeded_db):
    backfill.run_backfill(seeded_db, apply=True)
    rechecked = backfill.run_backfill(seeded_db, apply=True, recheck=True)
    assert rechecked.scanned == 4
    assert rechecked.by_basis[BASIS_DERIVED_YEGA] == 1


def test_chunking_covers_all_rows(seeded_db):
    """A chunk_size smaller than the row count still scans every row once."""
    stats = backfill.run_backfill(seeded_db, apply=True, chunk_size=1)
    assert stats.scanned == 4
    assert sum(stats.by_basis.values()) == 4


def test_limit_caps_scanned_rows(seeded_db):
    stats = backfill.run_backfill(seeded_db, apply=False, limit=2)
    assert stats.scanned == 2


def test_percent_form_winning_rate_classified_as_derived_yega(test_db):
    """A percentage-scale winning_rate (88.035) must be normalized before classify.

    TenderResult.winning_rate is mixed-scale (HTML parsing persists a percentage).
    Without normalization, base × 88.035 ≠ winning_amount, so this 예정가-역산 row
    would be mislabeled suspect-fractional instead of derived-yega (and the two
    backfill/holdout paths would disagree on the same row).
    """
    test_db.add(
        HistoricalData(id=1, project_id=1, base_amount=_YEGA_BASE, reserve_prices="[]")
    )
    test_db.add(
        TenderResult(project_id=1, winning_amount=43_996_200.0, winning_rate=88.035)
    )
    test_db.commit()

    backfill.run_backfill(test_db, apply=True)

    row = {r.id: r for r in test_db.query(HistoricalData).all()}[1]
    assert row.base_amount_basis == BASIS_DERIVED_YEGA
