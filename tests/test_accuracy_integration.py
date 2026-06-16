"""Tests for the consolidated 추천 vs 실제 정확도 리포트 (AccuracyIntegrationService).

These exercise the post-aggregation only: the prediction<->tender join and the
settled-only handling are inherited from ``PredictionFeedbackService.build_feedback``
(which we deliberately reuse, not re-implement). An unsettled (no winning_amount)
result yields a null error rate there, so it is excluded from every error-rate
aggregate the report builds -- the leakage / settled-only guard asserted below.
"""

from datetime import UTC, datetime, timedelta

from app.core.single_user import ensure_operator_account
from app.models.models import (
    BidDecisionRecord,
    PricePrediction,
    Project,
    TenderResult,
)
from app.services.accuracy_integration import AccuracyIntegrationService

# A fixed "now" anchor; announced_at offsets are computed relative to this so
# tender results sit inside the 90-day window the report queries.
_NOW = datetime.now(UTC)


def _make_project(test_db, *, title, category):
    project = Project(
        title=title,
        description="accuracy report fixture",
        requirements="n/a",
        budget_estimate=1_000_000.0,
        category=category,
    )
    test_db.add(project)
    test_db.flush()
    return project


def _seed_case(
    test_db,
    operator_id,
    *,
    title,
    category,
    winning_amount,
    recommended_amount,
    predicted_price,
    announced_offset_days,
):
    """Seed one settled tender result + a recommendation + a prediction."""
    project = _make_project(test_db, title=title, category=category)
    announced_at = (
        None
        if announced_offset_days is None
        else _NOW - timedelta(days=announced_offset_days)
    )
    test_db.add(
        TenderResult(
            project_id=project.id,
            winning_company="winner-co",
            winning_amount=winning_amount,
            winning_rate=0.0,
            result_status="completed",
            announced_at=announced_at,
        )
    )
    test_db.add(
        BidDecisionRecord(
            project_id=project.id,
            operator_id=operator_id,
            action="bid_now",
            decision_status="planned",
            recommended_amount=recommended_amount,
            reasoning="fixture",
        )
    )
    test_db.add(
        PricePrediction(
            user_id=operator_id,
            project_id=project.id,
            predicted_price=predicted_price,
            confidence_score=0.5,
        )
    )
    test_db.flush()
    return project


def _seed_unsettled(test_db, operator_id, *, title, category):
    """Seed a project with a recommendation but NO winning_amount (unsettled).

    ``build_feedback`` yields a null recommendation_error_rate for this row
    (winning_amount<=0), so it is excluded from every error-rate aggregate the
    report builds -- the leakage / settled-only guard.
    """
    project = _make_project(test_db, title=title, category=category)
    test_db.add(
        TenderResult(
            project_id=project.id,
            winning_company="",
            winning_amount=0.0,
            winning_rate=0.0,
            result_status="pending",
            announced_at=_NOW - timedelta(days=3),
        )
    )
    test_db.add(
        BidDecisionRecord(
            project_id=project.id,
            operator_id=operator_id,
            action="bid_now",
            decision_status="planned",
            recommended_amount=999_999.0,
            reasoning="unsettled fixture",
        )
    )
    test_db.flush()
    return project


def _build_report(test_db, **kwargs):
    return AccuracyIntegrationService().build_accuracy_report(test_db, **kwargs)


def test_accuracy_report_distribution_category_and_trend(test_db):
    """End-to-end aggregation across >=2 categories and >=2 weeks."""
    operator = ensure_operator_account(test_db)

    # software: two settled results in distinct weeks.
    #   - exact 0.03 error (1_030_000 vs 1_000_000) -> must land in 1-3% bin
    #   - 0.07 error (1_070_000 vs 1_000_000) -> 5-10% bin
    _seed_case(
        test_db,
        operator.id,
        title="SW exact-3pct",
        category="software",
        winning_amount=1_000_000.0,
        recommended_amount=1_030_000.0,
        predicted_price=1_100_000.0,  # 0.10 error -> recommendation improves
        announced_offset_days=2,
    )
    _seed_case(
        test_db,
        operator.id,
        title="SW 7pct",
        category="software",
        winning_amount=1_000_000.0,
        recommended_amount=1_070_000.0,
        predicted_price=1_080_000.0,
        announced_offset_days=10,  # different week
    )
    # services: one tight (0.005 -> <1% bin), one mid (0.04 -> 3-5% bin)
    _seed_case(
        test_db,
        operator.id,
        title="SV tight",
        category="services",
        winning_amount=2_000_000.0,
        recommended_amount=2_010_000.0,  # 0.005 error
        predicted_price=2_050_000.0,
        announced_offset_days=4,
    )
    _seed_case(
        test_db,
        operator.id,
        title="SV mid",
        category="services",
        winning_amount=2_000_000.0,
        recommended_amount=2_080_000.0,  # 0.04 error
        predicted_price=2_090_000.0,
        announced_offset_days=18,  # third week back
    )
    # uncategorized: one item with null category and null announced_at
    # (must be skipped from trend but counted in distribution/summary).
    _seed_case(
        test_db,
        operator.id,
        title="No category",
        category=None,
        winning_amount=5_000_000.0,
        recommended_amount=5_120_000.0,  # 0.024 error -> 1-3% bin
        predicted_price=5_200_000.0,
        announced_offset_days=None,  # null announced_at
    )
    # leakage guard: unsettled project must NOT appear anywhere.
    _seed_unsettled(test_db, operator.id, title="Unsettled SW", category="software")
    test_db.commit()

    report = _build_report(test_db, days=90, limit=500, trend_bucket_days=7)

    summary = report["summary"]
    # 5 settled recommendations, unsettled excluded.
    assert summary["recommendation_sample_count"] == 5
    # within thresholds: errors are {0.03, 0.07, 0.005, 0.04, 0.024}
    #   <=1%: 0.005                       -> 1
    #   <=3%: 0.005, 0.024, 0.03          -> 3
    #   <=5%: 0.005, 0.024, 0.03, 0.04    -> 4
    assert summary["within_1pct_count"] == 1
    assert summary["within_3pct_count"] == 3
    assert summary["within_5pct_count"] == 4
    assert summary["within_1pct_rate"] == round(1 / 5, 4)
    assert summary["within_3pct_rate"] == round(3 / 5, 4)
    assert summary["within_5pct_rate"] == round(4 / 5, 4)

    # error_distribution bins sum to the recommendation sample count.
    distribution = report["error_distribution"]
    assert sum(b["count"] for b in distribution) == summary["recommendation_sample_count"]
    by_label = {b["bin_label"]: b for b in distribution}
    # 0.005 -> <1%
    assert by_label["<1%"]["count"] == 1
    # 0.024 and exactly-0.03 both land in 1-3% (boundary: lower < rate <= upper)
    assert by_label["1-3%"]["count"] == 2
    # 0.04 -> 3-5%
    assert by_label["3-5%"]["count"] == 1
    # 0.07 -> 5-10%
    assert by_label["5-10%"]["count"] == 1
    assert by_label["≥10%"]["count"] == 0
    # bin rate is count/sample_count
    assert by_label["<1%"]["rate"] == round(1 / 5, 4)

    # per_category grouping + averages.
    per_category = {row["category"]: row for row in report["per_category"]}
    assert per_category["software"]["sample_count"] == 2
    assert per_category["services"]["sample_count"] == 2
    assert "(미분류)" in per_category  # null category bucketed
    assert per_category["(미분류)"]["sample_count"] == 1
    # software avg error = (0.03 + 0.07) / 2 = 0.05
    assert per_category["software"]["average_recommendation_error_rate"] == round(0.05, 4)
    # software within_3pct_rate: only 0.03 qualifies -> 1/2
    assert per_category["software"]["within_3pct_rate"] == round(1 / 2, 4)
    # sorted by sample_count desc
    counts = [row["sample_count"] for row in report["per_category"]]
    assert counts == sorted(counts, reverse=True)

    # time_trend: chronological, null announced_at skipped (4 dated items -> trend
    # sample sum is 4, not 5).
    trend = report["time_trend"]
    assert sum(bucket["sample_count"] for bucket in trend) == 4
    starts = [bucket["period_start"] for bucket in trend]
    assert starts == sorted(starts)
    assert len(trend) >= 2  # spans >= 2 weekly buckets

    # items pass-through carries category enrichment.
    assert any(item.get("category") == "software" for item in report["items"])
    # leakage / settled-only guard: the unsettled project (winning_amount<=0)
    # carries a null recommendation_error_rate and so is excluded from every
    # error-rate aggregate (distribution sum == 5 == settled count, asserted
    # above). It may surface only as a drill-down row with null error.
    unsettled_items = [
        item for item in report["items"] if item["project_title"] == "Unsettled SW"
    ]
    assert all(item["recommendation_error_rate"] is None for item in unsettled_items)

    # honest notes present.
    assert any("정산 완료" in note for note in report["notes"])
    assert any("단일 운영자" in note for note in report["notes"])


def test_accuracy_report_empty_window_is_honest_zeros(test_db):
    """No settled data -> zeros, empty lists, no crash."""
    ensure_operator_account(test_db)
    test_db.commit()

    report = _build_report(test_db, days=90, limit=500)

    summary = report["summary"]
    assert summary["recommendation_sample_count"] == 0
    assert summary["within_1pct_count"] == 0
    assert summary["within_3pct_count"] == 0
    assert summary["within_5pct_count"] == 0
    assert summary["within_1pct_rate"] is None
    assert summary["average_recommendation_error_rate"] is None

    # distribution still lists all bins, each zero with null rate.
    assert len(report["error_distribution"]) == 5
    assert all(b["count"] == 0 for b in report["error_distribution"])
    assert all(b["rate"] is None for b in report["error_distribution"])

    assert report["per_category"] == []
    assert report["time_trend"] == []
    assert report["items"] == []
    assert report["notes"]


def _seed_n_settled(test_db, operator_id, *, count, category="software"):
    """Seed ``count`` settled cases on distinct days (all within the window)."""
    for index in range(count):
        _seed_case(
            test_db,
            operator_id,
            title=f"Trunc {index}",
            category=category,
            winning_amount=1_000_000.0,
            recommended_amount=1_020_000.0,  # 0.02 error
            predicted_price=1_050_000.0,
            announced_offset_days=index + 1,
        )


def test_accuracy_report_truncation_flagged_when_limit_hit(test_db):
    """More matched+settled items than ``limit`` -> honest truncation signal."""
    operator = ensure_operator_account(test_db)
    _seed_n_settled(test_db, operator.id, count=4)  # 4 matched, cap at 2
    test_db.commit()

    report = _build_report(test_db, days=90, limit=2)

    summary = report["summary"]
    assert summary["truncated"] is True
    assert summary["matched_sample_count"] == 2
    assert summary["limit"] == 2
    # only the capped subset is aggregated/returned.
    assert summary["recommendation_sample_count"] == 2
    assert len(report["items"]) == 2
    # truncation note appended only when truncated.
    assert any("상한(2건)에 도달" in note for note in report["notes"])


def test_accuracy_report_no_truncation_under_limit(test_db):
    """Matched count below ``limit`` -> truncated False, no truncation note."""
    operator = ensure_operator_account(test_db)
    _seed_n_settled(test_db, operator.id, count=3)
    test_db.commit()

    report = _build_report(test_db, days=90, limit=500)

    summary = report["summary"]
    assert summary["truncated"] is False
    assert summary["matched_sample_count"] == 3
    assert summary["limit"] == 500
    assert summary["recommendation_sample_count"] == 3
    # no truncation note when under the cap.
    assert not any("상한" in note and "도달" in note for note in report["notes"])


def test_accuracy_report_endpoint(client, test_db):
    """The /accuracy-report endpoint returns the report shape."""
    operator = ensure_operator_account(test_db)
    _seed_case(
        test_db,
        operator.id,
        title="Endpoint SW",
        category="software",
        winning_amount=1_000_000.0,
        recommended_amount=1_020_000.0,  # 0.02 error
        predicted_price=1_050_000.0,
        announced_offset_days=5,
    )
    test_db.commit()

    response = client.get("/api/v1/analytics/accuracy-report", params={"days": 90})
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["recommendation_sample_count"] == 1
    assert {"error_distribution", "per_category", "time_trend", "items", "notes"} <= body.keys()
    # category enrichment surfaces on the drill-down item.
    assert body["items"][0]["category"] == "software"
