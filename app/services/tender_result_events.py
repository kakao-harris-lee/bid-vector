"""Append-only tender result observation staging."""

from __future__ import annotations

import hashlib
from typing import TypeAlias

from pydantic import RootModel
from sqlalchemy.orm import Session

from app.models.models import TenderResult, TenderResultEvent

EventScalar: TypeAlias = str | int | float | bool | None
EventPayloadMap: TypeAlias = dict[str, EventScalar]


class TenderResultEventPayload(RootModel[EventPayloadMap]):
    """Validated payload accepted by the append-only event boundary."""


def stage_tender_result_event(
    db: Session,
    *,
    tender_result: TenderResult,
    project_id: int | None,
    payload: EventPayloadMap,
    observed_at,
    event_type: str = "koneps_observation",
) -> None:
    validated = TenderResultEventPayload.model_validate(payload)
    serialized = validated.model_dump_json()
    event_key = hashlib.sha256(
        f"{event_type}:{project_id}:{serialized}".encode("utf-8")
    ).hexdigest()
    exists = (
        db.query(TenderResultEvent.id)
        .filter(TenderResultEvent.event_key == event_key)
        .first()
    )
    if exists is not None:
        return
    db.add(
        TenderResultEvent(
            tender_result_id=int(tender_result.id),
            project_id=project_id,
            event_key=event_key,
            event_type=event_type,
            payload_json=validated.root,
            observed_at=observed_at,
        )
    )
