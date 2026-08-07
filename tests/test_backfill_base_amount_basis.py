"""Tests for scripts/backfill_base_amount_basis.py against an in-memory-style DB."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_VAT,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_FRACTIONAL,
    BASIS_SUSPECT_RATIO,
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


def test_reclassify_clean_corrects_mislabeled_rows(test_db):
    """--reclassify-clean flips a mislabeled 'clean' 예정가-역산 row to derived-yega.

    Reproduces the P1 finding: an earlier backfill stamped a 예정가-역산 base 'clean'
    (e.g. before the settled TenderResult join was available). Re-examining the
    'clean' bucket with the winning result now re-classifies it, fills the reserve
    estimate, and leaves genuine clean rows untouched. base_amount is never mutated.
    """
    # genuine clean integer base (stays clean)
    test_db.add(
        HistoricalData(
            id=1,
            project_id=1,
            base_amount=43_996_200.0,
            base_amount_basis=BASIS_CLEAN,
            reserve_prices="[]",
        )
    )
    # mislabeled 'clean' but actually 예정가 역산 (non-integer, base×rate==winning)
    test_db.add(
        HistoricalData(
            id=2,
            project_id=2,
            base_amount=_YEGA_BASE,
            base_amount_basis=BASIS_CLEAN,
            reserve_prices=_reserves(_YEGA_BASE),
        )
    )
    # a non-clean row is NOT selected by the clean filter (left as-is)
    test_db.add(
        HistoricalData(
            id=3,
            project_id=3,
            base_amount=_VAT_BASE,
            base_amount_basis=BASIS_DERIVED_VAT,
            reserve_prices="[]",
        )
    )
    test_db.add(
        TenderResult(project_id=2, winning_amount=43_996_200.0, winning_rate=0.88035)
    )
    test_db.commit()

    # dry-run: measures without writing
    dry = backfill.run_backfill(test_db, apply=False, basis_filter=BASIS_CLEAN)
    assert dry.scanned == 2  # only the two 'clean' rows
    assert dry.reclassified == 1  # row 2 flips
    assert dry.by_basis[BASIS_DERIVED_YEGA] == 1
    assert dry.by_basis[BASIS_CLEAN] == 1
    assert len(dry.samples) == 1
    assert dry.samples[0]["id"] == 2
    assert dry.samples[0]["from_basis"] == BASIS_CLEAN
    assert dry.samples[0]["to_basis"] == BASIS_DERIVED_YEGA
    by_id = {r.id: r for r in test_db.query(HistoricalData).all()}
    assert by_id[2].base_amount_basis == BASIS_CLEAN  # dry-run wrote nothing

    # apply: persists the correction; base_amount never mutated
    applied = backfill.run_backfill(test_db, apply=True, basis_filter=BASIS_CLEAN)
    assert applied.reclassified == 1
    by_id = {r.id: r for r in test_db.query(HistoricalData).all()}
    assert by_id[1].base_amount_basis == BASIS_CLEAN  # genuine clean unchanged
    assert by_id[1].base_amount == 43_996_200.0
    assert by_id[2].base_amount_basis == BASIS_DERIVED_YEGA  # corrected
    assert by_id[2].base_amount == pytest.approx(_YEGA_BASE)  # original untouched
    assert by_id[2].base_amount_estimated is not None  # reserves ⇒ estimate filled
    assert by_id[3].base_amount_basis == BASIS_DERIVED_VAT  # not selected, unchanged

    # idempotent: a second pass finds one fewer clean row and no new flips
    again = backfill.run_backfill(test_db, apply=True, basis_filter=BASIS_CLEAN)
    assert again.scanned == 1  # only the genuine clean row remains in the bucket
    assert again.reclassified == 0


# --------------------------------------------------------------------------- #
# base ÷ 추정가격 비율 재분류: Project join 으로 추정가격을 분류기에 공급한다.
# --------------------------------------------------------------------------- #
_POLLUTED_BASE = 140_800_000.0  # 추정가격 100,000,000 대비 1.408 (실측 p50)
_VAT_INCLUSIVE_BASE = 110_000_000.0  # 1.10 — 부가세로 설명됨 ⇒ clean 유지


@pytest.fixture
def ratio_db(test_db):
    """clean 버킷 4행: 오염 1 + 과세 정상 1 + project 없음 1 + 추정가격 0 1."""
    test_db.add_all(
        [
            Project(
                id=1,
                budget_estimate=100_000_000.0,
                status="open",
                category="construction",
            ),
            Project(
                id=2,
                budget_estimate=100_000_000.0,
                status="awarded",
                category="service",
            ),
            Project(id=4, budget_estimate=0.0, status="open", category="construction"),
        ]
    )
    test_db.add_all(
        [
            HistoricalData(
                id=1,
                project_id=1,
                base_amount=_POLLUTED_BASE,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices="[]",
            ),
            HistoricalData(
                id=2,
                project_id=2,
                base_amount=_VAT_INCLUSIVE_BASE,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices="[]",
            ),
            # project_id 가 가리키는 Project 행이 없다 ⇒ 비율 검사 비적용
            HistoricalData(
                id=3,
                project_id=99,
                base_amount=1_000_000_000.0,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices="[]",
            ),
            # 추정가격 0 ⇒ 비율 검사 비적용
            HistoricalData(
                id=4,
                project_id=4,
                base_amount=1_000_000_000.0,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices="[]",
            ),
        ]
    )
    test_db.commit()
    return test_db


def test_reclassify_clean_retags_high_ratio_rows(ratio_db):
    """부가세로 설명 안 되는 base/추정가격 비율의 'clean' 행만 suspect-ratio 로 이동."""
    dry = backfill.run_backfill(ratio_db, apply=False, basis_filter=BASIS_CLEAN)

    assert dry.scanned == 4
    assert dry.reclassified == 1
    assert dry.by_basis[BASIS_SUSPECT_RATIO] == 1
    assert dry.by_basis[BASIS_CLEAN] == 3
    # dry-run 은 아무것도 쓰지 않는다
    assert all(
        row.base_amount_basis == BASIS_CLEAN
        for row in ratio_db.query(HistoricalData).all()
    )

    applied = backfill.run_backfill(ratio_db, apply=True, basis_filter=BASIS_CLEAN)
    assert applied.reclassified == 1

    by_id = {row.id: row for row in ratio_db.query(HistoricalData).all()}
    assert by_id[1].base_amount_basis == BASIS_SUSPECT_RATIO
    assert by_id[1].base_amount == _POLLUTED_BASE  # 원본 금액 불변
    assert by_id[1].base_amount_estimated is None  # 복구 reserve 없음
    assert by_id[2].base_amount_basis == BASIS_CLEAN  # 1.10 은 부가세로 설명됨
    assert by_id[2].base_amount == _VAT_INCLUSIVE_BASE
    assert by_id[3].base_amount_basis == BASIS_CLEAN  # Project 없음 ⇒ 비적용
    assert by_id[4].base_amount_basis == BASIS_CLEAN  # 추정가격 0 ⇒ 비적용

    # 멱등: 두 번째 apply 는 남은 clean 3행만 보고 이동 0
    again = backfill.run_backfill(ratio_db, apply=True, basis_filter=BASIS_CLEAN)
    assert again.scanned == 3
    assert again.reclassified == 0


def test_dry_run_reports_impact_breakdown(ratio_db):
    """dry-run 요약이 이동 행수를 status·category 로 분해하고 축소율을 낸다."""
    summary = backfill.run_backfill(
        ratio_db, apply=False, basis_filter=BASIS_CLEAN
    ).as_dict()

    assert summary["reclassified"] == 1
    assert summary["reclassified_by_status"] == {"open": 1}
    assert summary["reclassified_by_category"] == {"construction": 1}
    assert summary["bucket_shrink_ratio"] == pytest.approx(0.25)  # 1 / 4
    sample = summary["samples"][0]
    assert sample["id"] == 1
    assert sample["from_basis"] == BASIS_CLEAN
    assert sample["to_basis"] == BASIS_SUSPECT_RATIO
    assert sample["budget_estimate"] == pytest.approx(100_000_000.0)
    assert sample["base_to_estimate_ratio"] == pytest.approx(1.408)


def test_recheck_pass_records_movement_evidence(test_db):
    """--recheck 패스에도 이동 증적이 남는다 — 계수 기준은 '저장 라벨과 달라졌는가'다.

    룰 재정렬(비율 규칙 도입)을 전 행에 반영하는 패스는 ``--recheck`` 인데, 계수 조건이
    ``basis_filter`` 에만 걸려 있으면 바로 그 패스만 증적이 0 이 된다. 스탬프된 행의
    라벨이 바뀌면 어떤 패스든 샘플·분해가 남아야 한다.
    """
    test_db.add(
        Project(
            id=1, budget_estimate=100_000_000.0, status="open", category="construction"
        )
    )
    test_db.add_all(
        [
            HistoricalData(
                id=1,
                project_id=1,
                base_amount=_POLLUTED_BASE,
                base_amount_basis=BASIS_CLEAN,
                basis_checked_at=utc_now(),
                reserve_prices="[]",
            ),
            # 스탬프된 적 없는 행: 첫 태깅은 '이동'이 아니다(previous_basis 없음)
            HistoricalData(
                id=2,
                project_id=1,
                base_amount=_POLLUTED_BASE,
                reserve_prices="[]",
            ),
        ]
    )
    test_db.commit()

    stats = backfill.run_backfill(test_db, apply=False, recheck=True)

    assert stats.scanned == 2
    assert stats.reclassified == 1  # 스탬프된 행만 이동으로 센다
    assert stats.reclassified_by_status == {"open": 1}
    assert [sample["id"] for sample in stats.samples] == [1]
    assert stats.samples[0]["to_basis"] == BASIS_SUSPECT_RATIO


def test_est_equals_base_counter_exposes_blind_cohort(test_db):
    """추정가격이 base 폴백으로 채워진 행(비율 정확히 1.0)은 규칙이 구조적으로 못 본다.

    수집이 추정가격을 못 얻으면 ``matching.resolve_budget_estimate`` 가 base_amount 를
    그대로 추정가격으로 쓴다. 그 행의 비율은 항상 1.0 이라 base 가 아무리 오염돼도 이
    규칙에 걸리지 않는다 — 검증 커버리지의 구멍이므로 요약에 노출한다.
    """
    test_db.add_all(
        [
            Project(
                id=1,
                budget_estimate=_POLLUTED_BASE,  # est == base (수집 폴백)
                status="open",
                category="construction",
            ),
            Project(
                id=2,
                budget_estimate=100_000_000.0,
                status="open",
                category="construction",
            ),
        ]
    )
    test_db.add_all(
        [
            HistoricalData(id=1, project_id=1, base_amount=_POLLUTED_BASE),
            HistoricalData(id=2, project_id=2, base_amount=_POLLUTED_BASE),
        ]
    )
    test_db.commit()

    summary = backfill.run_backfill(test_db, apply=False).as_dict()

    assert summary["est_equals_base"] == 1
    assert summary["by_basis"][BASIS_SUSPECT_RATIO] == 1  # 비율이 살아 있는 쪽만 이동


def test_reserve_estimate_and_status_cross_counters(test_db):
    """재태깅 행 중 추정치 보유분과, 추정치 채움의 status 교차 분해를 낸다.

    ``get_reliable_base`` 가 금액을 실제로 바꾸는 유일한 축이 "non-clean + 양수 추정치"라,
    이 두 카운터가 apply 의 라이브 금액 영향(열린 공고 ∩ 추정치 = 0 이어야 함)을 증명한다.
    """
    test_db.add_all(
        [
            Project(
                id=1,
                budget_estimate=100_000_000.0,
                status="awarded",
                category="construction",
            ),
            Project(
                id=2,
                budget_estimate=100_000_000.0,
                status="open",
                category="construction",
            ),
        ]
    )
    test_db.add_all(
        [
            # 개찰 후 행: 복수예비가격 15개 ⇒ 추정치 복구 가능
            HistoricalData(
                id=1,
                project_id=1,
                base_amount=_POLLUTED_BASE,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices=_reserves(_POLLUTED_BASE),
            ),
            # 열린 공고: reserve 없음 ⇒ 추정치 없음 ⇒ 금액 불변
            HistoricalData(
                id=2,
                project_id=2,
                base_amount=_POLLUTED_BASE,
                base_amount_basis=BASIS_CLEAN,
                reserve_prices="[]",
            ),
        ]
    )
    test_db.commit()

    summary = backfill.run_backfill(
        test_db, apply=False, basis_filter=BASIS_CLEAN
    ).as_dict()

    assert summary["reclassified"] == 2
    assert summary["reclassified_with_reserve_estimate"] == 1
    assert summary["estimated_filled_by_status"] == {"awarded": 1}
    assert "open" not in summary["estimated_filled_by_status"]


def test_impact_report_prints_move_evidence(ratio_db, capsys):
    """dry-run 터미널 출력만으로 승인 판단(몇 행이 어디서 왜 이동하는가)이 가능해야 한다."""
    stats = backfill.run_backfill(ratio_db, apply=False, basis_filter=BASIS_CLEAN)
    backfill.print_impact_report(stats)

    out = capsys.readouterr().out
    assert "4행 중 1행 이동" in out
    assert "25.00%" in out
    assert "open=1" in out
    assert "construction=1" in out
    assert "ratio=1.408" in out


def test_default_pass_also_consumes_budget_estimate(ratio_db):
    """기본(미태깅) 패스도 Project 추정가격을 분류기에 공급한다."""
    for row in ratio_db.query(HistoricalData).all():
        row.base_amount_basis = None
    ratio_db.commit()

    stats = backfill.run_backfill(ratio_db, apply=True)

    assert stats.by_basis[BASIS_SUSPECT_RATIO] == 1
    by_id = {row.id: row for row in ratio_db.query(HistoricalData).all()}
    assert by_id[1].base_amount_basis == BASIS_SUSPECT_RATIO
    assert by_id[1].base_amount == _POLLUTED_BASE  # 원본 금액 불변


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
