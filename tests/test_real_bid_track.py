"""Tests for the 실투찰(real-bid) 트랙 API + RealBidTrackService.

Covers: register via project_id and via notice_number(차수 접미사 제거), the
"둘 중 하나 필수" validator, 404 on unknown notice, recent-first listing scoped
to submitted records, recommendation-preservation (submitted_bid_amount does NOT
overwrite recommended_amount), award status surfacing, and the
tracked-pending-award query used by the notification pipeline.
"""

from __future__ import annotations

from app.core.single_user import ensure_operator_account
from app.core.time import utc_now
from app.models.models import BidDecisionRecord, Project, TenderResult, User  # noqa: F401
from app.services.real_bid_track import RealBidNotFoundError, RealBidTrackService

REAL_BIDS_PATH = "/api/v1/operations/real-bids"


def _seed_project(
    test_db,
    *,
    title: str = "실투찰 대상 공고",
    notice_number: str = "R26BK01627948",
    budget: float = 100_000_000.0,
) -> Project:
    project = Project(
        title=title,
        description="real bid track",
        budget_estimate=budget,
        category="construction",
        notice_number=notice_number,
        status="open",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


# --------------------------------------------------------------------------- #
# API — register
# --------------------------------------------------------------------------- #
def test_register_real_bid_by_project_id(client, test_db):
    project = _seed_project(test_db)
    response = client.post(
        REAL_BIDS_PATH,
        json={"project_id": project.id, "bid_amount": 77_529_785, "floor_rate": 0.87745},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project.id
    assert body["submitted_bid_amount"] == 77_529_785
    assert body["submitted_floor_rate"] == 0.87745
    assert body["decision_status"] == "submitted"
    assert body["award_public"] is False
    assert body["award_notified_at"] is None

    # A BidDecisionRecord was persisted with the submitted fields.
    record = test_db.query(BidDecisionRecord).filter_by(project_id=project.id).one()
    assert record.submitted_bid_amount == 77_529_785
    assert record.submitted_at is not None


def test_register_real_bid_by_notice_number_strips_suffix(client, test_db):
    project = _seed_project(test_db, notice_number="R26BK01628093")
    response = client.post(
        REAL_BIDS_PATH,
        json={"notice_number": "R26BK01628093-000", "bid_amount": 48_475_268},
    )
    assert response.status_code == 200
    assert response.json()["project_id"] == project.id


def test_register_real_bid_requires_project_or_notice(client, test_db):
    response = client.post(REAL_BIDS_PATH, json={"bid_amount": 1_000_000})
    assert response.status_code == 422


def test_register_real_bid_unknown_notice_returns_404(client, test_db):
    response = client.post(
        REAL_BIDS_PATH,
        json={"notice_number": "R26BK09999999", "bid_amount": 1_000_000},
    )
    assert response.status_code == 404


def test_register_real_bid_rejects_non_positive_amount(client, test_db):
    project = _seed_project(test_db)
    response = client.post(
        REAL_BIDS_PATH, json={"project_id": project.id, "bid_amount": 0}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# API — list
# --------------------------------------------------------------------------- #
def test_list_real_bids_recent_first_and_scoped(client, test_db):
    p1 = _seed_project(test_db, notice_number="R26BK00000001")
    p2 = _seed_project(test_db, notice_number="R26BK00000002")
    client.post(REAL_BIDS_PATH, json={"project_id": p1.id, "bid_amount": 10_000_000})
    client.post(REAL_BIDS_PATH, json={"project_id": p2.id, "bid_amount": 20_000_000})

    body = client.get(REAL_BIDS_PATH).json()
    assert body["count"] == 2
    # Most recently submitted first (p2 registered last).
    assert body["records"][0]["project_id"] == p2.id
    assert {r["project_id"] for r in body["records"]} == {p1.id, p2.id}


def test_list_real_bids_excludes_unsubmitted_decision_records(client, test_db):
    """A plain decision record (no submitted bid) must not appear in the list."""
    project = _seed_project(test_db)
    operator = ensure_operator_account(test_db)
    test_db.add(
        BidDecisionRecord(
            project_id=project.id,
            operator_id=operator.id,
            decision_status="planned",
            recommended_amount=50_000_000,
        )
    )
    test_db.commit()

    body = client.get(REAL_BIDS_PATH).json()
    assert body["count"] == 0


def test_list_real_bids_surfaces_award_when_public(client, test_db):
    project = _seed_project(test_db)
    client.post(REAL_BIDS_PATH, json={"project_id": project.id, "bid_amount": 77_000_000})
    test_db.add(
        TenderResult(
            project_id=project.id,
            winning_company="낙찰건설",
            winning_amount=78_000_000,
            winning_rate=0.78,
            result_status="opened",
        )
    )
    test_db.commit()

    record = client.get(REAL_BIDS_PATH).json()["records"][0]
    assert record["award_public"] is True
    assert record["winning_company"] == "낙찰건설"
    assert record["winning_amount"] == 78_000_000


# --------------------------------------------------------------------------- #
# Service — recommendation preservation + pending query
# --------------------------------------------------------------------------- #
def test_record_real_bid_preserves_existing_recommendation(test_db):
    project = _seed_project(test_db)
    operator = ensure_operator_account(test_db)
    prior = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        decision_status="planned",
        recommended_amount=90_000_000,
    )
    test_db.add(prior)
    test_db.commit()

    service = RealBidTrackService()
    record = service.record_real_bid(
        test_db, operator=operator, project=project, bid_amount=88_000_000
    )

    # Same record reused; recommendation kept, actual bid stored separately.
    assert record.id == prior.id
    assert record.recommended_amount == 90_000_000
    assert record.submitted_bid_amount == 88_000_000
    assert record.decision_status == "submitted"


def test_resolve_project_raises_when_missing(test_db):
    service = RealBidTrackService()
    try:
        service.resolve_project(test_db, project_id=None, notice_number="nope")
    except RealBidNotFoundError:
        pass
    else:  # pragma: no cover - explicit failure signal
        raise AssertionError("expected RealBidNotFoundError")


def test_tracked_pending_award_excludes_notified_and_unsubmitted(test_db):
    project = _seed_project(test_db)
    operator = ensure_operator_account(test_db)
    service = RealBidTrackService()

    # (1) submitted + not notified -> included.
    service.record_real_bid(
        test_db, operator=operator, project=project, bid_amount=77_000_000
    )
    # (2) submitted + already notified -> excluded.
    p2 = _seed_project(test_db, notice_number="R26BK00000009")
    notified = service.record_real_bid(
        test_db, operator=operator, project=p2, bid_amount=55_000_000
    )
    notified.award_notified_at = utc_now()
    # (3) unsubmitted decision record -> excluded.
    p3 = _seed_project(test_db, notice_number="R26BK00000010")
    test_db.add(
        BidDecisionRecord(
            project_id=p3.id, operator_id=operator.id, decision_status="planned"
        )
    )
    test_db.commit()

    pending = service.tracked_pending_award(test_db, operator=operator)
    project_ids = {r.project_id for r in pending}
    assert project_ids == {project.id}
