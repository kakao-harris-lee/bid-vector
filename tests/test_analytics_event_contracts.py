"""``Analytics.event_data`` payload 계약 + 직렬화/복원 단일 경로 테스트.

방어적 DTO 규율 Phase 4.2. 여기서 고정하는 것:

1. **생산 계약**(``extra="forbid"``)과 **복원 계약**(``extra="ignore"`` + 전부 옵셔널)의
   비대칭 — 오타 키는 즉시 거부되고, 과거 행은 계속 읽힌다.
2. **산출 불변** — 저장되는 JSON 이 종전 ``json.dumps`` 산출과 키 집합·키 순서·값이
   같다(공백만 다르다). 이 회로는 감사 기록이므로 문자열이 조용히 달라지면 안 된다.
3. **degrade 정책** — legacy repr 행은 복구하고, 해석 불가 행은 경고와 함께 부재로
   내린다(빈 모델로 지어내지 않는다).
4. **레지스트리 drift 가드** — 생산자가 쓰는 event_type 이 계약 없이 남지 않는다.
"""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from app.core.constants import (
    BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE,
    COLLECT_G2_EVIDENCE_EVENT_TYPE,
    G2_CANDIDATE_RECHECK_EVENT_TYPE,
    INTERNAL_TELEMETRY_EVENT_TYPES,
    PROJECT_VIEW_EVENT_TYPE,
    RECOMMENDATION_FEEDBACK_EVENT_TYPE,
    TELEGRAM_DELIVERY_EVENT_TYPE,
    TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
    TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
)
from app.schemas.analytics_events import (
    PERSISTED_EVENT_MODEL_BY_TYPE,
    AnalyticsEventEnvelope,
    BidReportEmailDeliveryEvent,
    PersistedBidReportEmailDeliveryEvent,
    PersistedProjectViewEvent,
    PersistedRecommendationFeedbackEvent,
    PersistedTelegramDeliveryEvent,
    PersistedTelegramDeliverySuppressedEvent,
    PersistedTelegramStrategyPendingEditEvent,
    TelegramDeliveryEvent,
    TelegramDeliverySuppressedEvent,
    TelegramStrategyPendingEditActivated,
    TelegramStrategyPendingEditCleared,
)
from app.schemas.g2_evidence import (
    PersistedG2CandidateRecheckSummary,
    PersistedG2CollectEvidenceSummary,
)
from app.services.analytics_event_payload import (
    dump_analytics_event,
    load_analytics_event,
    load_analytics_event_as,
)

# 종전 manager.py 가 ``json.dumps`` 로 저장한 18키 payload (키 순서 포함).
LEGACY_DELIVERY_PAYLOAD: dict[str, object] = {
    "operator_id": 7,
    "notification_id": 11,
    "project_id": 4242,
    "source": "bid_decision",
    "sent": True,
    "status": "sent",
    "detail": "Telegram delivery succeeded.",
    "telegram_message_id": 908,
    "channel_type": "telegram",
    "channel_id": 3,
    "route_key": "telegram:legacy-configured-chat",
    "target_label": "chat ********0346",
    "channel_source": "operator_notification_channels",
    "channel_active": True,
    "dry_run_only": False,
    "route_send_allowed": True,
    "telegram_configured": True,
    "can_send": True,
}


def _delivery_event() -> TelegramDeliveryEvent:
    return TelegramDeliveryEvent(**LEGACY_DELIVERY_PAYLOAD)


# --- 산출 불변 (키 집합 · 키 순서 · 값) -------------------------------------------


def test_delivery_event_serialization_matches_the_legacy_payload():
    """저장 문자열은 종전 json.dumps 산출과 파싱 동치이고 키 순서까지 같다."""
    stored = dump_analytics_event(_delivery_event())

    assert json.loads(stored) == LEGACY_DELIVERY_PAYLOAD
    assert list(json.loads(stored)) == list(LEGACY_DELIVERY_PAYLOAD)
    # 종전과 같이 non-ASCII 는 이스케이프하지 않는다(ensure_ascii=False 동치).
    assert "\\u" not in dump_analytics_event(
        _delivery_event().model_copy(update={"detail": "한글 사유"})
    )


def test_suppression_event_serialization_matches_the_legacy_payload():
    """보류 레코드도 종전 ``{..., **decision.as_event_payload()}`` 키 순서를 유지한다."""
    legacy = {
        "operator_id": 7,
        "notification_id": 11,
        "project_id": 4242,
        "source": "bid_decision",
        "sent": False,
        "status": "suppressed_daily_cap",
        "allowed": False,
        "reason": "suppressed_daily_cap",
        "detail": "cap reached",
        "daily_sent_count": 3,
        "daily_cap": 3,
        "hours_since_project_send": None,
        "renotify_cooldown_hours": 0.0,
    }
    stored = dump_analytics_event(TelegramDeliverySuppressedEvent(**legacy))

    assert json.loads(stored) == legacy
    assert list(json.loads(stored)) == list(legacy)


def test_email_delivery_event_serialization_matches_the_legacy_payload():
    """9키 순서 골든 + ``recorded_at`` 은 datetime 직렬화(Z)가 아니라 isoformat 문자열."""
    legacy = {
        "operator_id": 1,
        "project_id": 2,
        "decision_record_id": 3,
        "dry_run": True,
        "sent": False,
        "delivery_status": "dry_run_rendered",
        "masked_recipient": "o***@local.bid-vector",
        "has_draft_attachment": True,
        "recorded_at": "2026-07-25T04:00:00+00:00",
    }
    stored = dump_analytics_event(BidReportEmailDeliveryEvent(**legacy))

    assert json.loads(stored) == legacy
    assert list(json.loads(stored)) == list(legacy)
    assert json.loads(stored)["recorded_at"] == "2026-07-25T04:00:00+00:00"


def test_pending_edit_variants_keep_their_own_key_sets():
    """해제 행은 2키 그대로다 — 한 모델로 합쳐 null 키를 덧붙이지 않는다."""
    activated = json.loads(
        dump_analytics_event(
            TelegramStrategyPendingEditActivated(
                chat_id="1594710346",
                field_key="min_priority_score",
                stage="awaiting_value",
                updates={"min_priority_score": 0.7},
            )
        )
    )
    cleared = json.loads(
        dump_analytics_event(TelegramStrategyPendingEditCleared(chat_id="1594710346"))
    )

    assert activated == {
        "chat_id": "1594710346",
        "active": True,
        "field_key": "min_priority_score",
        "stage": "awaiting_value",
        "updates": {"min_priority_score": 0.7},
    }
    assert cleared == {"chat_id": "1594710346", "active": False}


def test_envelope_preserves_arbitrary_client_payloads():
    """열린 텔레메트리 싱크는 키를 그대로 보존한다(순서 포함)."""
    payload = {"project_id": 4321, "verdict": "useful", "한글": "값"}

    stored = dump_analytics_event(AnalyticsEventEnvelope.model_validate(payload))

    assert json.loads(stored) == payload
    assert list(json.loads(stored)) == list(payload)


# --- 생산 계약: 오타 키/누락 필드 거부 (sad) ---------------------------------------


def test_production_delivery_event_rejects_unknown_keys():
    """오타 키가 조용히 무시되면 기록되지 않은 사실이 감사 기록에 남는다."""
    with pytest.raises(ValidationError):
        TelegramDeliveryEvent(**{**LEGACY_DELIVERY_PAYLOAD, "chanel_type": "telegram"})


def test_production_delivery_event_requires_the_decision_inputs():
    """피로도 게이트가 읽는 ``source``/``sent`` 는 생산 경로에서 필수다."""
    incomplete = dict(LEGACY_DELIVERY_PAYLOAD)
    incomplete.pop("source")

    with pytest.raises(ValidationError):
        TelegramDeliveryEvent(**incomplete)


# --- 복원 계약: 과거 행 관용 (happy) ---------------------------------------------


def test_persisted_delivery_event_reads_a_row_without_project_id():
    """``project_id`` 가 생기기 전 행도 읽히고, 미기록은 0 이 아니라 None 이다."""
    legacy_row = json.dumps(
        {
            "operator_id": 7,
            "notification_id": 11,
            "source": "bid_decision",
            "sent": True,
            "status": "sent",
        }
    )

    restored = load_analytics_event_as(
        legacy_row,
        model=PersistedTelegramDeliveryEvent,
        event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
    )

    assert restored is not None
    assert restored.sent is True
    assert restored.source == "bid_decision"
    assert restored.project_id is None
    # 기록되지 않은 불리언을 False 로 지어내지 않는다(오독 방지).
    assert restored.can_send is None
    assert restored.route_send_allowed is None


def test_persisted_delivery_event_ignores_keys_added_after_the_row_was_written():
    """미지 키 하나가 목록/집계 전체를 500 으로 만들면 안 된다."""
    row = json.dumps({**LEGACY_DELIVERY_PAYLOAD, "future_key": "later"})

    restored = load_analytics_event_as(
        row,
        model=PersistedTelegramDeliveryEvent,
        event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
    )

    assert restored is not None
    assert restored.status == "sent"


def test_persisted_delivery_event_round_trips_the_production_payload():
    """쓰기 계약과 읽기 계약이 같은 키 집합을 본다(왕복 동치)."""
    restored = load_analytics_event_as(
        dump_analytics_event(_delivery_event()),
        model=PersistedTelegramDeliveryEvent,
        event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
    )

    assert restored is not None
    assert restored.model_dump() == LEGACY_DELIVERY_PAYLOAD


# --- degrade 정책 (sad) ----------------------------------------------------------


def test_loader_recovers_legacy_python_repr_rows():
    """JSON 이전 시절의 ``str(dict)`` 행은 ast 폴백으로 계속 복구된다."""
    legacy_repr = str({"project_id": 99, "verdict": "not_useful"})
    assert "'" in legacy_repr  # sanity: repr 는 단일 인용부호

    restored = load_analytics_event_as(
        legacy_repr,
        model=PersistedRecommendationFeedbackEvent,
        event_type=RECOMMENDATION_FEEDBACK_EVENT_TYPE,
    )

    assert restored is not None
    assert restored.project_id == 99
    assert restored.verdict == "not_useful"


@pytest.mark.parametrize("raw", [None, "", "   ", "not json at all", "[1, 2, 3]", "42"])
def test_loader_treats_unusable_payloads_as_absent(raw):
    """빈 값·비 JSON·비 매핑은 모두 부재다(빈 모델로 지어내지 않는다)."""
    assert (
        load_analytics_event_as(
            raw,
            model=PersistedTelegramDeliveryEvent,
            event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
        )
        is None
    )


def test_loader_warns_and_degrades_on_a_schema_violating_row(caplog):
    """계약을 어긴 행은 조용히 사라지지 않는다 — 경고 후 부재로 내린다."""
    corrupted = json.dumps({"operator_id": 7, "sent": True, "project_id": "abc"})

    with caplog.at_level(logging.WARNING):
        restored = load_analytics_event_as(
            corrupted,
            model=PersistedTelegramDeliveryEvent,
            event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
        )

    assert restored is None
    assert "analytics event_data 해석 실패" in caplog.text
    assert TELEGRAM_DELIVERY_EVENT_TYPE in caplog.text
    # payload 원문(마스킹된 target 을 포함할 수 있다)은 로그에 남기지 않는다.
    assert corrupted not in caplog.text


# --- 레지스트리 (룩업 디스패치 + drift 가드) ---------------------------------------


def test_registry_covers_every_declared_event_type():
    """생산자가 쓰는 event_type 은 계약 없이 남지 않는다."""
    assert set(PERSISTED_EVENT_MODEL_BY_TYPE) == {
        TELEGRAM_DELIVERY_EVENT_TYPE,
        TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
        TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
        BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE,
        PROJECT_VIEW_EVENT_TYPE,
        RECOMMENDATION_FEEDBACK_EVENT_TYPE,
        G2_CANDIDATE_RECHECK_EVENT_TYPE,
        COLLECT_G2_EVIDENCE_EVENT_TYPE,
    }
    # 내부 텔레메트리로 선언된 타입은 전부 복원 계약을 가져야 한다.
    assert INTERNAL_TELEMETRY_EVENT_TYPES <= set(PERSISTED_EVENT_MODEL_BY_TYPE)


@pytest.mark.parametrize(
    ("event_type", "model"),
    [
        (TELEGRAM_DELIVERY_EVENT_TYPE, PersistedTelegramDeliveryEvent),
        (
            TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE,
            PersistedTelegramDeliverySuppressedEvent,
        ),
        (
            TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE,
            PersistedTelegramStrategyPendingEditEvent,
        ),
        (BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE, PersistedBidReportEmailDeliveryEvent),
        (PROJECT_VIEW_EVENT_TYPE, PersistedProjectViewEvent),
        (RECOMMENDATION_FEEDBACK_EVENT_TYPE, PersistedRecommendationFeedbackEvent),
        (G2_CANDIDATE_RECHECK_EVENT_TYPE, PersistedG2CandidateRecheckSummary),
        (COLLECT_G2_EVIDENCE_EVENT_TYPE, PersistedG2CollectEvidenceSummary),
    ],
)
def test_registry_dispatches_to_the_declared_model(event_type, model):
    """event_type → 모델 선택은 if-ladder 가 아니라 룩업이다(§4.5-2)."""
    restored = load_analytics_event(
        json.dumps({"project_id": 1}), event_type=event_type
    )

    assert isinstance(restored, model)


def test_unknown_event_type_is_explicitly_absent():
    """미등록 타입은 임의 dict 로 흘리지 않고 부재로 만든다."""
    assert (
        load_analytics_event(
            json.dumps({"anything": 1}), event_type="unregistered.type"
        )
        is None
    )


def test_event_type_wire_values_are_pinned():
    """event_type 은 저장된 행과의 **wire 계약**이다 — 이름 정리로 바꿀 수 없다.

    상수를 상수로만 비교하면(레지스트리 키 == 상수) 값이 통째로 바뀌어도 통과한다.
    기존 행이 조회에서 사라지는 사고를 막으려면 리터럴 자체를 고정해야 한다.
    """
    assert TELEGRAM_DELIVERY_EVENT_TYPE == "telegram.delivery"
    assert TELEGRAM_DELIVERY_SUPPRESSED_EVENT_TYPE == "telegram.delivery.suppressed"
    assert TELEGRAM_STRATEGY_PENDING_EDIT_EVENT_TYPE == "telegram.strategy.pending_edit"
    assert BID_REPORT_EMAIL_DELIVERY_EVENT_TYPE == "email.bid_report.delivery"
    assert PROJECT_VIEW_EVENT_TYPE == "project_view"
    assert RECOMMENDATION_FEEDBACK_EVENT_TYPE == "recommendation_feedback"
    assert G2_CANDIDATE_RECHECK_EVENT_TYPE == "g2_candidate_recheck"
    assert COLLECT_G2_EVIDENCE_EVENT_TYPE == "collect_g2_evidence"


@pytest.mark.parametrize(
    "model", sorted(PERSISTED_EVENT_MODEL_BY_TYPE.values(), key=lambda m: m.__name__)
)
def test_persisted_models_are_tolerant_by_construction(model):
    """복원 모델의 불변식: 인자 없이 만들 수 있고(전 필드 옵셔널) 미지 키를 무시한다.

    새 이벤트 타입을 등록할 때 생산 모델을 실수로 레지스트리에 넣으면, 과거 행 하나가
    목록/집계 API 를 500 으로 만든다. 그 사고를 등록 시점에 잡는다.
    """
    assert model.model_config.get("extra") == "ignore"

    empty = model()  # 전 필드 옵셔널 — 필수 필드가 있으면 여기서 실패한다.

    assert all(value is None for value in empty.model_dump().values())
    assert (
        model.model_validate({"totally": "unknown"}).model_dump() == empty.model_dump()
    )


def test_absent_payload_degrades_quietly_but_corrupted_text_warns(caplog):
    """부재(빈 값)와 손상(디코딩 실패)은 다르게 다룬다 — 후자만 경고를 남긴다."""
    with caplog.at_level(logging.WARNING):
        assert (
            load_analytics_event_as(
                "",
                model=PersistedTelegramDeliveryEvent,
                event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
            )
            is None
        )
    assert caplog.text == ""

    with caplog.at_level(logging.WARNING):
        assert (
            load_analytics_event_as(
                "{not json at all",
                model=PersistedTelegramDeliveryEvent,
                event_type=TELEGRAM_DELIVERY_EVENT_TYPE,
            )
            is None
        )
    assert "reason=decode" in caplog.text
