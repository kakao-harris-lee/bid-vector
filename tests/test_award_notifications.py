"""Tests for AwardResultNotificationService + the notify_award_results task.

Covers the award-result Telegram pipeline: the DB-only public-award gate, the
one-shot factual alarm, award_notified_at idempotency, the send-failure retry
path (no marking), canonical-operator scoping (synthetic excluded), and the
Celery task wrapper. Reserve-detail fetch + Telegram send are injected so no
live KONEPS/Telegram call happens.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.single_user import ensure_operator_account
from app.core.time import ensure_utc
from app.models.models import (  # noqa: F401 — registers table metadata
    BidDecisionRecord,
    CompanyProfile,
    OperatorStrategy,
    Project,
    TenderResult,
    User,
)
from app.services.award_notifications import AwardResultNotificationService
from app.services.award_verification import VERDICT_ELIGIBLE_WINNABLE
from app.services.real_bid_track import RealBidTrackService

NOTICE = "R26BK01627948"
FIXED_NOW = datetime(2026, 7, 17, 3, 0, tzinfo=timezone.utc)


class _RecordingSender:
    def __init__(self):
        self.messages: list[str] = []

    def send_message(self, message: str) -> dict[str, object]:
        self.messages.append(message)
        return {"sent": True, "status": "sent"}


class _BoomSender:
    def send_message(self, message: str) -> dict[str, object]:
        raise RuntimeError("Telegram API down")


def _settled_fetch(notice, category):
    return {
        "reserve_prices": [99_000_000, 101_000_000],
        "selected_numbers": [2, 13],
        "planned_price": 100_000_000,
        "base_amount": 100_000_000,
        "reserve_detail_total_count": 15,
    }


def _empty_fetch(notice, category):
    return {}


def _seed_project(test_db, *, notice: str = NOTICE) -> Project:
    project = Project(
        title="낙찰 알림 대상",
        description="award notify",
        budget_estimate=100_000_000.0,
        category="construction",
        notice_number=notice,
        status="awarded",
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _add_winner(test_db, project, *, company="가상건설", amount=89_000_000.0, rate=0.89):
    test_db.add(
        TenderResult(
            project_id=project.id,
            winning_company=company,
            winning_amount=amount,
            winning_rate=rate,
            result_status="opened",
        )
    )
    test_db.commit()


def _track_bid(test_db, operator, project, *, bid=88_000_000.0, floor_rate=0.87745):
    return RealBidTrackService().record_real_bid(
        test_db, operator=operator, project=project, bid_amount=bid, floor_rate=floor_rate
    )


# --------------------------------------------------------------------------- #
def test_notify_skips_when_no_pending(test_db):
    ensure_operator_account(test_db)
    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_empty_fetch
    )
    assert outcome["status"] == "skipped"
    assert outcome["notified_count"] == 0
    assert outcome["pending_count"] == 0


def test_notify_skips_when_award_not_public(test_db):
    """Tracked bid exists but no winner in TenderResult -> no notification, no marking."""
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    sender = _RecordingSender()

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_settled_fetch
    )
    assert outcome["status"] == "skipped"
    assert outcome["pending_count"] == 1
    assert sender.messages == []
    test_db.refresh(record)
    assert record.award_notified_at is None


def test_notify_sends_and_marks_when_public(test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project)
    sender = _RecordingSender()

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    assert outcome["status"] == "sent"
    assert outcome["notified_count"] == 1
    assert len(sender.messages) == 1
    message = sender.messages[0]
    assert NOTICE in message
    assert VERDICT_ELIGIBLE_WINNABLE in message

    test_db.refresh(record)
    # SQLite drops tzinfo (Postgres timezone=True preserves it) -> normalize.
    assert ensure_utc(record.award_notified_at) == FIXED_NOW


def test_notify_is_idempotent(test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    _track_bid(test_db, operator, project)
    _add_winner(test_db, project)
    service = AwardResultNotificationService()

    first = service.collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    assert first["status"] == "sent"

    second_sender = _RecordingSender()
    second = service.collect_and_notify(
        test_db, sender=second_sender, fetch_detail=_settled_fetch
    )
    assert second["status"] == "skipped"
    assert second["pending_count"] == 0
    assert second_sender.messages == []


def test_notify_send_failure_does_not_mark(test_db):
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project)

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=_BoomSender(), fetch_detail=_settled_fetch
    )
    assert outcome["status"] == "error"
    assert "Telegram API down" in outcome["error"]
    test_db.refresh(record)
    # Not marked -> next cycle retries.
    assert record.award_notified_at is None


def test_notify_uses_tender_result_when_reserve_detail_empty(test_db):
    """Winner present but reserve fetch empty still notifies (UNDETERMINED verdict)."""
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    _track_bid(test_db, operator, project, floor_rate=None)
    _add_winner(test_db, project)
    sender = _RecordingSender()

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_empty_fetch, now=FIXED_NOW
    )
    assert outcome["status"] == "sent"
    assert outcome["notified_count"] == 1
    assert NOTICE in sender.messages[0]


def test_notify_scopes_to_canonical_operator(test_db):
    """A synthetic operator's tracked bid must not be picked up (canonical only)."""
    ensure_operator_account(test_db)
    synthetic = User(
        username="synthetic-alpha",
        email="alpha@synthetic.test.local",
        full_name="테스트 alpha",
        company="회사 alpha",
        hashed_password="x",
        is_active=True,
    )
    test_db.add(synthetic)
    test_db.commit()
    test_db.refresh(synthetic)

    project = _seed_project(test_db)
    _track_bid(test_db, synthetic, project)
    _add_winner(test_db, project)
    sender = _RecordingSender()

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_settled_fetch
    )
    assert outcome["status"] == "skipped"
    assert outcome["pending_count"] == 0
    assert sender.messages == []


# --------------------------------------------------------------------------- #
# Celery task wrapper
# --------------------------------------------------------------------------- #
def _patch_session(monkeypatch, test_db):
    import app.tasks.jobs as jobs_mod

    class _NoCloseSession:
        def __init__(self, db):
            self._db = db

        def __getattr__(self, name):
            return getattr(self._db, name)

        def close(self):
            pass

    monkeypatch.setattr(jobs_mod, "SessionLocal", lambda: _NoCloseSession(test_db))


# --------------------------------------------------------------------------- #
# Award outcome (win/loss) persistence
# --------------------------------------------------------------------------- #
def _set_operator_company(test_db, name: str) -> User:
    operator = ensure_operator_account(test_db)
    operator.company = name
    test_db.commit()
    test_db.refresh(operator)
    return operator


def test_outcome_records_won_when_operator_is_winner(test_db):
    _set_operator_company(test_db, "가상건설")
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project, company="가상건설")
    sender = _RecordingSender()

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    assert outcome["status"] == "sent"

    test_db.refresh(record)
    assert record.award_outcome == "won"
    assert ensure_utc(record.award_outcome_at) == FIXED_NOW
    # 낙찰 tag surfaced in the alarm.
    assert "낙찰" in sender.messages[0]


def test_outcome_records_lost_when_winner_is_another_company(test_db):
    _set_operator_company(test_db, "가상건설")
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    # Winner is a different 상호, even though it could share the same 투찰가.
    _add_winner(test_db, project, company="경쟁건설", amount=88_000_000.0)
    sender = _RecordingSender()

    AwardResultNotificationService().collect_and_notify(
        test_db, sender=sender, fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    test_db.refresh(record)
    assert record.award_outcome == "lost"
    assert "패찰" in sender.messages[0]


def test_outcome_is_none_when_operator_company_unknown(test_db):
    # Canonical operator has no 상호 -> no basis to judge (§2) -> stays NULL,
    # even though the alarm still sends the factual verdict.
    ensure_operator_account(test_db)  # company == ""
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project, company="가상건설")

    AwardResultNotificationService().collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    test_db.refresh(record)
    assert record.award_outcome is None
    assert record.award_outcome_at is None


def test_outcome_stays_null_when_award_not_public(test_db):
    _set_operator_company(test_db, "가상건설")
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)  # no TenderResult winner

    AwardResultNotificationService().collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_settled_fetch
    )
    test_db.refresh(record)
    assert record.award_outcome is None


def test_outcome_persists_even_when_send_fails(test_db):
    # Ideal structure: the 과거 입찰 기록 (win/loss) is recorded when the winner is
    # public, independent of Telegram delivery. award_notified_at stays NULL so
    # the alarm retries next cycle, but the outcome is durable.
    _set_operator_company(test_db, "가상건설")
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project, company="가상건설")

    outcome = AwardResultNotificationService().collect_and_notify(
        test_db, sender=_BoomSender(), fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    assert outcome["status"] == "error"
    test_db.refresh(record)
    assert record.award_outcome == "won"
    assert record.award_notified_at is None  # send failed -> retry marker unset


def test_outcome_is_idempotent_not_overwritten(test_db):
    _set_operator_company(test_db, "가상건설")
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    record = _track_bid(test_db, operator, project)
    _add_winner(test_db, project, company="가상건설")
    service = AwardResultNotificationService()

    service.collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_settled_fetch, now=FIXED_NOW
    )
    test_db.refresh(record)
    first_at = record.award_outcome_at

    # Force a re-eval by clearing the send marker; the outcome timestamp must not
    # change (verdict recorded once).
    record.award_notified_at = None
    test_db.commit()
    later = datetime(2026, 7, 18, 3, 0, tzinfo=timezone.utc)
    service.collect_and_notify(
        test_db, sender=_RecordingSender(), fetch_detail=_settled_fetch, now=later
    )
    test_db.refresh(record)
    assert record.award_outcome == "won"
    assert ensure_utc(record.award_outcome_at) == ensure_utc(first_at)


def test_notify_award_results_task_skips_without_public_award(test_db, monkeypatch):
    """The task runs the real service; a pending-but-not-public bid stays skipped.

    No TenderResult winner -> the DB-only gate short-circuits before any live
    KONEPS/Telegram call, so the task is safe to exercise end-to-end offline.
    """
    from app.tasks.jobs import notify_award_results

    _patch_session(monkeypatch, test_db)
    operator = ensure_operator_account(test_db)
    project = _seed_project(test_db)
    _track_bid(test_db, operator, project)

    result = notify_award_results(limit=10)
    assert result["status"] == "skipped"
    assert result["pending_count"] == 1
