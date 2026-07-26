"""Shared status/action constant sets for the dashboard summary domain."""

from __future__ import annotations

from app.core.constants import ACTIVE_DECISION_STATUSES

_OPPORTUNITY_STATUSES = {"planned", "reviewing", "submitted", "skipped"}
_ACTIVE_OPPORTUNITY_STATUSES = ACTIVE_DECISION_STATUSES
_BID_STATUSES = {"submitted", "reviewed", "accepted", "rejected"}
_PAPER_ACTION_STATUS = {"bid_now": "planned", "review": "reviewing", "skip": "skipped"}
_DEFAULT_PAPER_OPPORTUNITY_ACTIONS = {"bid_now", "review"}
# Terminal 개찰 상태에서만 상호 불일치를 확정 패찰(lost)로 라벨한다. 그 외에는
# 운영자가 실제 투찰했더라도 미확정(unknown)으로 남겨 라벨 오염을 막는다.
_TERMINAL_RESULT_STATUSES = {"awarded", "closed"}
