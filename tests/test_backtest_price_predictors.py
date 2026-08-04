from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.ai.backtest_dataset_quality import (
    MAX_DATASET_AGE_DAYS,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_WARNING,
    DatasetQualitySample,
    assess_backtest_dataset_quality,
)
from app.models.models import HistoricalData
from app.services.base_amount_basis import BASIS_CLEAN, BASIS_DERIVED_YEGA
from app.services.ml_release import MLReleasePromotionService
from app.services.ml_release.base import _MLReleaseBase
from scripts import backtest_price_predictors as backtest_script
from scripts.backtest_price_predictors import load_records

_REFERENCE_TIME = datetime(2026, 8, 4, tzinfo=UTC)


def _quality_sample(
    *,
    basis: str = BASIS_CLEAN,
    bid_rate: float = 0.88,
    age_days: float = 1.0,
) -> DatasetQualitySample:
    return DatasetQualitySample(
        base_amount_basis=basis,
        bid_rate=bid_rate,
        observed_at=_REFERENCE_TIME - timedelta(days=age_days),
    )


def _assess(samples: list[DatasetQualitySample], *, required: int = 10):
    return assess_backtest_dataset_quality(
        samples,
        base_amount_basis=BASIS_CLEAN,
        required_sample_count=required,
        reference_time=_REFERENCE_TIME,
    )


def _failed_check_names(report) -> set[str]:
    return {check.name for check in report.checks if not check.passed}


def test_backtest_records_default_to_clean_base_amount_basis(test_db):
    test_db.add_all(
        [
            HistoricalData(
                notice_number="CLEAN",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=BASIS_CLEAN,
            ),
            HistoricalData(
                notice_number="DERIVED",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=BASIS_DERIVED_YEGA,
            ),
            HistoricalData(
                notice_number="UNCLASSIFIED",
                category="service",
                base_amount=100.0,
                bid_rate=0.9,
                base_amount_basis=None,
            ),
        ]
    )
    test_db.commit()

    records = load_records(
        test_db,
        category="service",
        start_at=None,
        end_at=None,
        limit=10,
    )

    assert [record.notice_number for record in records] == ["CLEAN"]


def test_backtest_records_require_explicit_any_to_include_unclassified(test_db):
    test_db.add(
        HistoricalData(
            notice_number="UNCLASSIFIED",
            category="service",
            base_amount=100.0,
            bid_rate=0.9,
            base_amount_basis=None,
        )
    )
    test_db.commit()

    records = load_records(
        test_db,
        category="service",
        start_at=None,
        end_at=None,
        limit=10,
        base_amount_basis=None,
    )

    assert [record.notice_number for record in records] == ["UNCLASSIFIED"]


def _run_backtest_cli(tmp_path, monkeypatch, records: list) -> dict:
    """Run the CLI with a stubbed DB/backtest so only dataset quality is measured."""

    class _Db:
        def close(self):
            pass

    output_path = tmp_path / "backtest.json"
    monkeypatch.setattr(backtest_script, "SessionLocal", _Db)
    monkeypatch.setattr(backtest_script, "load_records", lambda *args, **kwargs: records)
    monkeypatch.setattr(
        backtest_script,
        "build_predictor_backtest_report",
        lambda *args, **kwargs: {
            "status": "completed",
            "sample_count": 5,
            "guardrail_rate": 0.0,
            "fallback_rate": 0.0,
            "best_predictor_key": "historical",
            "best_average_absolute_error_rate": 0.01,
            "results": [],
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["backtest_price_predictors.py", "--out", str(output_path)],
    )

    assert backtest_script.main() == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    report["report_path"] = str(output_path)
    return report


def _cli_record(*, basis: str = BASIS_CLEAN, bid_rate: float = 0.88, age_days: float = 1.0):
    return SimpleNamespace(
        base_amount=100_000_000.0,
        base_amount_basis=basis,
        bid_rate=bid_rate,
        opened_at=datetime.now(UTC) - timedelta(days=age_days),
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )


def test_backtest_cli_writes_flat_promotion_gate_report(tmp_path, monkeypatch):
    report = _run_backtest_cli(
        tmp_path, monkeypatch, [_cli_record() for _ in range(12)]
    )

    assert report["status"] == "completed"
    assert report["best_predictor_key"] == "historical"
    assert "backtest" not in report
    assert report["settings"]["base_amount_basis"] == BASIS_CLEAN
    assert report["dataset_quality_status"] == STATUS_PASSED
    assert report["dataset_quality"]["metrics"]["record_count"] == 12


def test_backtest_cli_dataset_quality_reports_failed_for_empty_holdout(
    tmp_path, monkeypatch
):
    """빈 홀드아웃은 게이트 최소 통과값(warning)을 받지 못한다 — 자기충족 상수 회귀."""
    report = _run_backtest_cli(tmp_path, monkeypatch, [])

    assert report["dataset_quality_status"] == STATUS_FAILED
    assert report["dataset_quality"]["status"] == STATUS_FAILED
    assert report["dataset_quality"]["blocking_issue_count"] > 0


def test_empty_holdout_dataset_quality_fails_the_promotion_gate(tmp_path, monkeypatch):
    """측정된 status 가 promotion gate 의 dataset quality 축을 실제로 막는가."""
    cli_report = _run_backtest_cli(tmp_path, monkeypatch, [])
    service = MLReleasePromotionService(repo_root=tmp_path)

    gate = service._build_predictor_promotion_gate(
        service._load_predictor_backtest_report(cli_report["report_path"]),
        has_predictor_artifact=True,
    )

    assert gate["passed"] is False
    assert any(
        f"Dataset quality status '{STATUS_FAILED}' is below required" in reason
        for reason in gate["reasons"]
    )


def test_healthy_holdout_dataset_quality_clears_the_promotion_gate(
    tmp_path, monkeypatch
):
    cli_report = _run_backtest_cli(
        tmp_path, monkeypatch, [_cli_record() for _ in range(12)]
    )
    service = MLReleasePromotionService(repo_root=tmp_path)

    gate = service._build_predictor_promotion_gate(
        service._load_predictor_backtest_report(cli_report["report_path"]),
        has_predictor_artifact=True,
    )

    assert gate["metrics"]["dataset_quality_status"] == STATUS_PASSED
    assert not [reason for reason in gate["reasons"] if "Dataset quality" in reason]
    assert gate["passed"] is True


def test_dataset_quality_passes_on_clean_recent_holdout():
    report = _assess([_quality_sample() for _ in range(12)])

    assert report.status == STATUS_PASSED
    assert _failed_check_names(report) == set()
    assert report.metrics.clean_basis_ratio == 1.0


def test_dataset_quality_fails_when_holdout_is_too_shallow():
    report = _assess([_quality_sample() for _ in range(4)], required=10)

    assert report.status == STATUS_FAILED
    assert "sample_depth" in _failed_check_names(report)


def test_dataset_quality_fails_on_contaminated_base_amount_basis():
    samples = [_quality_sample() for _ in range(18)]
    samples += [_quality_sample(basis=BASIS_DERIVED_YEGA) for _ in range(2)]

    report = _assess(samples)

    assert report.status == STATUS_FAILED
    assert "base_amount_basis_purity" in _failed_check_names(report)
    assert report.metrics.clean_basis_ratio == 0.9


def test_dataset_quality_fails_on_percent_scale_bid_rate():
    samples = [_quality_sample() for _ in range(11)]
    samples.append(_quality_sample(bid_rate=87.5))

    report = _assess(samples)

    assert report.status == STATUS_FAILED
    assert "bid_rate_scale" in _failed_check_names(report)


def test_dataset_quality_scale_check_counts_rows_instead_of_rounded_ratios():
    """큰 홀드아웃에서 percent-scale 한 건이 반올림에 묻혀 통과하지 않아야 한다."""
    samples = [_quality_sample() for _ in range(20_000)]
    samples.append(_quality_sample(bid_rate=87.5))

    report = _assess(samples)

    assert report.metrics.normalized_bid_rate_ratio == 1.0
    assert report.metrics.unnormalized_bid_rate_count == 1
    assert report.status == STATUS_FAILED
    assert "bid_rate_scale" in _failed_check_names(report)


def test_dataset_quality_warns_on_stale_holdout():
    report = _assess(
        [_quality_sample(age_days=MAX_DATASET_AGE_DAYS + 30) for _ in range(12)]
    )

    assert report.status == STATUS_WARNING
    assert _failed_check_names(report) == {"freshness"}


def test_dataset_quality_warns_when_freshness_cannot_be_audited():
    """타임스탬프가 없으면 조용히 통과시키지 않고 판정 불가를 남긴다."""
    samples = [
        DatasetQualitySample(
            base_amount_basis=BASIS_CLEAN, bid_rate=0.88, observed_at=None
        )
        for _ in range(12)
    ]

    report = _assess(samples)

    assert report.status == STATUS_WARNING
    assert report.metrics.dataset_age_days is None
    assert _failed_check_names(report) == {"freshness"}


def test_dataset_quality_warns_on_low_bid_rate_share():
    samples = [_quality_sample() for _ in range(9)]
    samples += [_quality_sample(bid_rate=0.5) for _ in range(3)]

    report = _assess(samples)

    assert report.status == STATUS_WARNING
    assert "usable_bid_rate_share" in _failed_check_names(report)


def test_dataset_quality_statuses_match_the_release_gate_vocabulary():
    """게이트가 순위를 매기는 어휘와 값이 어긋나면 판정이 조용히 무시된다."""
    assert {STATUS_FAILED, STATUS_WARNING, STATUS_PASSED} == set(
        _MLReleaseBase.DATASET_QUALITY_ORDER
    )
