"""Tests for the Telegram decision-alert fatigue gate (일일 상한 · 재알림 쿨다운)."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.constants import (
    INTERNAL_TELEMETRY_EVENT_TYPES,
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
)
from app.core.security import get_password_hash
from app.core.time import kst_day_bounds_utc
from app.models.models import (
    Analytics,
    BidDecisionRecord,
    CompanyProfile,
    Notification,
    OperatorStrategy,
    Project,
    User,
)
from app.services.notifications.fatigue import (
    FATIGUE_ALLOWED,
    FATIGUE_SUPPRESSED_DAILY_CAP,
    FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN,
    FatigueSignals,
    NotificationFatigueLimits,
    evaluate_notification_fatigue,
)
from app.services.notifications.fatigue_gate import NotificationFatigueGate
from app.services.notifications.manager import OperatorNotificationService
from app.services.notifications.telegram import TelegramNotificationService

NOW = datetime(2026, 7, 25, 4, 0, 0, tzinfo=UTC)
DISABLED = NotificationFatigueLimits()


def _evaluate(limits, *, daily_sent_count=0, last_project_sent_at=None, now=NOW):
    return evaluate_notification_fatigue(
        limits=limits,
        signals=FatigueSignals(
            daily_sent_count=daily_sent_count,
            last_project_sent_at=last_project_sent_at,
        ),
        now=now,
    )


# --- pure core -------------------------------------------------------------


@pytest.mark.parametrize(
    ("daily_sent_count", "hours_since_last_send"),
    [(0, None), (99, 0.0), (5, 0.5)],
)
def test_disabled_limits_always_allow_delivery(daily_sent_count, hours_since_last_send):
    """0/0 is the shipped default and must never withhold an alert."""
    last_sent = (
        None if hours_since_last_send is None else NOW - timedelta(hours=hours_since_last_send)
    )
    decision = _evaluate(
        DISABLED,
        daily_sent_count=daily_sent_count,
        last_project_sent_at=last_sent,
    )

    assert decision.allowed is True
    assert decision.reason == FATIGUE_ALLOWED


@pytest.mark.parametrize(
    ("daily_cap", "daily_sent_count", "expected_allowed"),
    [
        (3, 0, True),
        (3, 2, True),   # 상한 도달 직전
        (3, 3, False),  # 상한 도달
        (3, 7, False),  # 이미 초과된 상태
        (1, 0, True),
        (1, 1, False),
    ],
)
def test_daily_cap_boundary(daily_cap, daily_sent_count, expected_allowed):
    limits = NotificationFatigueLimits(daily_cap=daily_cap)

    decision = _evaluate(limits, daily_sent_count=daily_sent_count)

    assert decision.allowed is expected_allowed
    assert decision.reason == (
        FATIGUE_ALLOWED if expected_allowed else FATIGUE_SUPPRESSED_DAILY_CAP
    )
    assert decision.daily_sent_count == daily_sent_count
    assert decision.daily_cap == daily_cap


@pytest.mark.parametrize(
    ("cooldown_hours", "hours_since_last_send", "expected_allowed"),
    [
        (24.0, None, True),   # 같은 공고 발송 이력 없음
        (24.0, 0.0, False),   # 방금 보냄
        (24.0, 23.99, False), # 쿨다운 경과 직전
        (24.0, 24.0, True),   # 경계는 허용
        (24.0, 30.0, True),   # 경과 후
        (0.5, 0.4, False),
        (0.5, 0.6, True),
    ],
)
def test_renotify_cooldown_boundary(cooldown_hours, hours_since_last_send, expected_allowed):
    limits = NotificationFatigueLimits(renotify_cooldown_hours=cooldown_hours)
    last_sent = (
        None if hours_since_last_send is None else NOW - timedelta(hours=hours_since_last_send)
    )

    decision = _evaluate(limits, last_project_sent_at=last_sent)

    assert decision.allowed is expected_allowed
    assert decision.reason == (
        FATIGUE_ALLOWED if expected_allowed else FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN
    )


def test_cooldown_reason_wins_when_both_limits_are_violated():
    """The more specific reason (same notice repeated) is reported first."""
    limits = NotificationFatigueLimits(daily_cap=1, renotify_cooldown_hours=24.0)

    decision = _evaluate(
        limits,
        daily_sent_count=5,
        last_project_sent_at=NOW - timedelta(hours=1),
    )

    assert decision.allowed is False
    assert decision.reason == FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN
    assert decision.hours_since_project_send == pytest.approx(1.0)


def test_naive_last_sent_timestamp_is_treated_as_utc():
    """SQLite hands back naive datetimes; they must not skew the cooldown."""
    limits = NotificationFatigueLimits(renotify_cooldown_hours=2.0)

    decision = _evaluate(
        limits,
        last_project_sent_at=(NOW - timedelta(hours=1)).replace(tzinfo=None),
    )

    assert decision.allowed is False
    assert decision.hours_since_project_send == pytest.approx(1.0)


def test_limits_from_settings_reads_declared_keys(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_DAILY_CAP", 8)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS", 12.5)

    limits = NotificationFatigueLimits.from_settings(settings)

    assert limits == NotificationFatigueLimits(daily_cap=8, renotify_cooldown_hours=12.5)
    assert limits.is_active is True


def test_default_settings_keep_the_gate_inactive():
    """Live behavior is unchanged until an operator opts in through .env."""
    assert settings.TELEGRAM_DECISION_DAILY_CAP == 0
    assert settings.TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS == 0.0
    assert NotificationFatigueLimits.from_settings(settings).is_active is False


def test_suppression_payload_carries_the_numbers_that_justified_it():
    limits = NotificationFatigueLimits(daily_cap=2)

    payload = _evaluate(limits, daily_sent_count=2).as_event_payload()

    assert payload["allowed"] is False
    assert payload["reason"] == FATIGUE_SUPPRESSED_DAILY_CAP
    assert payload["daily_sent_count"] == 2
    assert payload["daily_cap"] == 2


# --- KST day boundary ------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected_start"),
    [
        # 00:30 KST on 7/26 -> the KST day started at 15:00 UTC on 7/25.
        (datetime(2026, 7, 25, 15, 30, tzinfo=UTC), datetime(2026, 7, 25, 15, 0, tzinfo=UTC)),
        # 23:59 KST on 7/25 -> still the KST day that started 7/24 15:00 UTC.
        (datetime(2026, 7, 25, 14, 59, tzinfo=UTC), datetime(2026, 7, 24, 15, 0, tzinfo=UTC)),
        # Exactly KST midnight belongs to the new day.
        (datetime(2026, 7, 25, 15, 0, tzinfo=UTC), datetime(2026, 7, 25, 15, 0, tzinfo=UTC)),
    ],
)
def test_kst_day_bounds_utc(moment, expected_start):
    start, end = kst_day_bounds_utc(moment)

    assert start == expected_start
    assert end == expected_start + timedelta(days=1)


# --- delivery-history gate (Analytics 카운트 소스) --------------------------


def _create_operator(test_db, *, username: str = "operator") -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        full_name=username,
        company=f"{username} Co",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    test_db.add(
        CompanyProfile(
            user_id=user.id,
            business_type="software",
            license_codes="",
            region_codes="",
            annual_revenue=0.0,
            capacity_score=0.0,
            total_awards=0,
        )
    )
    test_db.add(
        OperatorStrategy(
            user_id=user.id,
            focus_categories="",
            bid_now_threshold=0.7,
            review_threshold=0.45,
        )
    )
    test_db.commit()
    return user


def _create_project(test_db, *, title: str = "Fatigue test notice") -> Project:
    project = Project(
        title=title,
        description="notification fatigue",
        requirements="n/a",
        budget_estimate=50_000_000.0,
        category="software",
        deadline=datetime.now(UTC) + timedelta(hours=12),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)
    return project


def _seed_delivery_event(
    test_db,
    *,
    operator_id: int,
    timestamp: datetime,
    project_id: int | None = 1,
    sent: bool = True,
    source: str = "bid_decision",
    event_type: str = TELEGRAM_DELIVERY_EVENT_TYPE,
    include_project_id: bool = True,
) -> Analytics:
    payload: dict[str, object] = {
        "operator_id": int(operator_id),
        "notification_id": 1,
        "source": source,
        "sent": sent,
        "status": "sent" if sent else "telegram_channel_dry_run",
    }
    if include_project_id:
        payload["project_id"] = project_id
    event = Analytics(
        user_id=operator_id,
        event_type=event_type,
        event_data=json.dumps(payload, ensure_ascii=False),
        timestamp=timestamp,
    )
    test_db.add(event)
    test_db.commit()
    return event


def _seed_decision_record(test_db, *, operator_id: int, project: Project) -> BidDecisionRecord:
    record = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator_id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        initial_action="bid_now",
        initial_decision_status="planned",
        recommended_amount=45_000_000.0,
        probability_score=0.95,
        matched_score=0.9,
        priority_score=0.95,
        urgency_score=0.4,
        competitiveness_score=0.5,
        budget_capture_score=0.5,
        expected_margin_score=0.5,
        execution_complexity_score=0.4,
        current_active_bids=0,
        max_active_bids=3,
        current_workload_score=0.2,
        workload_source="provided",
        score_breakdown="{}",
        reasoning="fatigue test",
    )
    test_db.add(record)
    test_db.commit()
    test_db.refresh(record)
    return record


def test_gate_counts_only_alerts_that_reached_the_operator_today(test_db):
    """Dry-run, non-decision, other-operator and other-day rows must not consume the cap."""
    operator = _create_operator(test_db)
    other = _create_operator(test_db, username="synthetic-other")
    now = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
    day_start, _ = kst_day_bounds_utc(now)

    _seed_delivery_event(test_db, operator_id=operator.id, timestamp=day_start + timedelta(minutes=1))
    _seed_delivery_event(test_db, operator_id=operator.id, timestamp=now - timedelta(minutes=5))
    _seed_delivery_event(
        test_db, operator_id=operator.id, timestamp=day_start - timedelta(minutes=1)
    )  # 전날(KST)
    _seed_delivery_event(test_db, operator_id=operator.id, timestamp=now, sent=False)  # dry-run
    _seed_delivery_event(
        test_db, operator_id=operator.id, timestamp=now, source="bid_submission"
    )  # 투찰 완료 안내
    _seed_delivery_event(test_db, operator_id=other.id, timestamp=now)  # 다른 운영자

    gate = NotificationFatigueGate(
        limits=NotificationFatigueLimits(daily_cap=3),
        now_provider=lambda: now,
    )
    decision = gate.evaluate(test_db, operator_id=operator.id, project_id=1)

    assert decision.daily_sent_count == 2
    assert decision.allowed is True


def test_gate_suppresses_when_the_daily_cap_is_reached(test_db):
    operator = _create_operator(test_db)
    now = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
    for minutes in (10, 20):
        _seed_delivery_event(
            test_db, operator_id=operator.id, timestamp=now - timedelta(minutes=minutes)
        )

    gate = NotificationFatigueGate(
        limits=NotificationFatigueLimits(daily_cap=2),
        now_provider=lambda: now,
    )
    decision = gate.evaluate(test_db, operator_id=operator.id, project_id=1)

    assert decision.allowed is False
    assert decision.reason == FATIGUE_SUPPRESSED_DAILY_CAP


def test_gate_matches_the_cooldown_on_the_same_notice_only(test_db):
    operator = _create_operator(test_db)
    now = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
    _seed_delivery_event(
        test_db,
        operator_id=operator.id,
        timestamp=now - timedelta(hours=2),
        project_id=4242,
    )

    gate = NotificationFatigueGate(
        limits=NotificationFatigueLimits(renotify_cooldown_hours=24.0),
        now_provider=lambda: now,
    )
    same_notice = gate.evaluate(test_db, operator_id=operator.id, project_id=4242)
    other_notice = gate.evaluate(test_db, operator_id=operator.id, project_id=9999)

    assert same_notice.allowed is False
    assert same_notice.reason == FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN
    assert same_notice.hours_since_project_send == pytest.approx(2.0, abs=0.01)
    assert other_notice.allowed is True


def test_gate_ignores_legacy_rows_without_a_notice_id(test_db):
    """Rows written before project_id existed simply never match the cooldown."""
    operator = _create_operator(test_db)
    now = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
    _seed_delivery_event(
        test_db,
        operator_id=operator.id,
        timestamp=now - timedelta(hours=1),
        include_project_id=False,
    )

    gate = NotificationFatigueGate(
        limits=NotificationFatigueLimits(renotify_cooldown_hours=24.0),
        now_provider=lambda: now,
    )
    decision = gate.evaluate(test_db, operator_id=operator.id, project_id=4242)

    assert decision.allowed is True


def test_gate_is_inert_when_both_limits_are_disabled(test_db):
    operator = _create_operator(test_db)
    now = datetime(2026, 7, 25, 4, 0, tzinfo=UTC)
    for minutes in range(5):
        _seed_delivery_event(
            test_db, operator_id=operator.id, timestamp=now - timedelta(minutes=minutes)
        )

    gate = NotificationFatigueGate(limits=DISABLED, now_provider=lambda: now)
    decision = gate.evaluate(test_db, operator_id=operator.id, project_id=1)

    assert decision.allowed is True
    assert decision.daily_sent_count == 0


# --- wiring ----------------------------------------------------------------


def _configure_telegram(monkeypatch, deliveries: list[dict]) -> None:
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")

    def fake_send(self, message: str, reply_markup=None, chat_id=None):
        deliveries.append({"message": message, "reply_markup": reply_markup})
        return {"sent": True, "status": "sent", "detail": "ok"}

    monkeypatch.setattr(TelegramNotificationService, "send_message", fake_send)


def _notify(test_db, *, operator: User, project: Project) -> Notification:
    record = _seed_decision_record(test_db, operator_id=operator.id, project=project)
    return OperatorNotificationService().create_bid_decision_notification(
        test_db,
        operator_id=operator.id,
        project=project,
        decision_record=record,
    )


def _suppression_events(test_db) -> list[dict]:
    events = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE)
        .order_by(Analytics.id)
        .all()
    )
    return [json.loads(event.event_data) for event in events]


def test_daily_cap_withholds_the_second_alert_but_keeps_the_web_notification(
    test_db,
    monkeypatch,
):
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch, deliveries)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_DAILY_CAP", 1)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS", 0.0)

    operator = _create_operator(test_db)
    first = _notify(test_db, operator=operator, project=_create_project(test_db, title="첫 공고"))
    second = _notify(test_db, operator=operator, project=_create_project(test_db, title="둘째 공고"))

    assert len(deliveries) == 1, "일일 상한 도달 후에는 텔레그램 발송이 없어야 한다"
    assert first.id != second.id
    assert second.is_read is False, "웹 알림은 억제와 무관하게 그대로 생성된다"

    suppressions = _suppression_events(test_db)
    assert len(suppressions) == 1
    assert suppressions[0]["reason"] == FATIGUE_SUPPRESSED_DAILY_CAP
    assert suppressions[0]["daily_cap"] == 1
    assert suppressions[0]["sent"] is False


def test_renotify_cooldown_withholds_a_repeat_alert_for_the_same_notice(test_db, monkeypatch):
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch, deliveries)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_DAILY_CAP", 0)
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_RENOTIFY_COOLDOWN_HOURS", 24.0)

    operator = _create_operator(test_db)
    project = _create_project(test_db)
    _notify(test_db, operator=operator, project=project)
    _notify(test_db, operator=operator, project=project)
    _notify(test_db, operator=operator, project=_create_project(test_db, title="다른 공고"))

    assert len(deliveries) == 2, "같은 공고 재알림만 억제되고 다른 공고는 발송된다"
    suppressions = _suppression_events(test_db)
    assert len(suppressions) == 1
    assert suppressions[0]["reason"] == FATIGUE_SUPPRESSED_RENOTIFY_COOLDOWN
    assert suppressions[0]["project_id"] == project.id


def test_default_settings_deliver_every_qualifying_alert(test_db, monkeypatch):
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch, deliveries)

    operator = _create_operator(test_db)
    project = _create_project(test_db)
    for _ in range(3):
        _notify(test_db, operator=operator, project=project)

    assert len(deliveries) == 3
    assert _suppression_events(test_db) == []


def test_test_environment_skip_does_not_consume_the_daily_cap(test_db, monkeypatch):
    """ENVIRONMENT=test still short-circuits the real send, and nothing was delivered."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "test-bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "1594710346")
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "TELEGRAM_DECISION_DAILY_CAP", 1)

    operator = _create_operator(test_db)
    _notify(test_db, operator=operator, project=_create_project(test_db, title="첫 공고"))
    _notify(test_db, operator=operator, project=_create_project(test_db, title="둘째 공고"))

    statuses = [
        json.loads(event.event_data)["status"]
        for event in test_db.query(Analytics)
        .filter(Analytics.event_type == TELEGRAM_DELIVERY_EVENT_TYPE)
        .order_by(Analytics.id)
        .all()
    ]
    assert statuses == ["skipped_test_environment", "skipped_test_environment"]
    assert _suppression_events(test_db) == []


def test_delivery_telemetry_carries_the_notice_id(test_db, monkeypatch):
    deliveries: list[dict] = []
    _configure_telegram(monkeypatch, deliveries)

    operator = _create_operator(test_db)
    project = _create_project(test_db)
    _notify(test_db, operator=operator, project=project)

    event = (
        test_db.query(Analytics)
        .filter(Analytics.event_type == TELEGRAM_DELIVERY_EVENT_TYPE)
        .one()
    )
    payload = json.loads(event.event_data)
    assert payload["project_id"] == project.id
    assert payload["source"] == "bid_decision"


def test_suppression_events_stay_out_of_operator_activity_counts():
    """A suppression is internal telemetry, not operator activity."""
    assert TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE in INTERNAL_TELEMETRY_EVENT_TYPES
    assert TELEGRAM_DELIVERY_EVENT_TYPE in INTERNAL_TELEMETRY_EVENT_TYPES
