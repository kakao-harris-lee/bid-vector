"""Telegram command helpers for operator strategy edits.

Command orchestration lives here. The declarative
field routing table (``FIELD_SPECS``), the leaf string parsers, and the outbound
message rendering were split into sibling modules and are re-exported below so
the public import contract (tests import ``FIELD_SPECS`` / ``FIELD_SPEC_BY_ALIAS``
/ ``TelegramStrategyCommandProcessor`` from here) stays intact:

- ``telegram_strategy_fields``: ``FieldSpec`` table + parse/apply/validate helpers
- ``telegram_strategy_parsing``: stateless token/number/bool/list parsers
- ``telegram_strategy_render``: status text, help, and inline-keyboard markup
- ``telegram_strategy_pending``: pending-edit ``analytics`` row load/record boundary
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, ClassVar

from sqlalchemy.orm import Session

from app.core.single_user import ensure_operator_strategy
from app.schemas import analytics_events as event_models
from app.services.notifications import telegram_strategy_pending as pending_store
from app.services.notifications import telegram_strategy_fields as fields
from app.services.notifications import telegram_strategy_parsing as parsing
from app.services.notifications import telegram_strategy_render as render
from app.services.notifications.telegram_strategy_fields import (
    FIELD_SPEC_BY_ALIAS,
    FIELD_SPECS,
    FieldApplier,
    FieldParser,
    FieldSpec,
)
from app.services.notifications.telegram_strategy_render import TelegramStrategyReply
from app.services.preview_snapshot import PreviewSnapshotService

__all__ = [
    "FIELD_SPECS",
    "FIELD_SPEC_BY_ALIAS",
    "FieldApplier",
    "FieldParser",
    "FieldSpec",
    "PendingStrategyEdit",
    "TelegramStrategyCommandProcessor",
    "TelegramStrategyReply",
]


@dataclass
class PendingStrategyEdit:
    """In-memory step state for one Telegram chat."""

    field_key: str
    stage: str
    updates: dict[str, Any] | None = None


class TelegramStrategyCommandProcessor:
    """Handle small Telegram text commands that edit watch strategy settings."""

    COMMANDS = {"/strategy", "/strategy_help", "/strategy_set", "/strategy_clear"}
    CALLBACK_PREFIX = "strategy-edit"
    APPLY_CALLBACK = "apply"
    CANCEL_CALLBACK = "cancel"
    # 영속 계약은 telegram_strategy_pending 이 소유한다(클래스 표면만 유지).
    PENDING_EVENT_TYPE = pending_store.PENDING_EVENT_TYPE
    PENDING_EVENT_FETCH_LIMIT = pending_store.PENDING_EVENT_FETCH_LIMIT
    PENDING_EDITS: ClassVar[dict[str, PendingStrategyEdit]] = {}

    EDIT_FIELDS = fields.EDIT_FIELDS
    CLEAR_GROUPS = fields.CLEAR_GROUPS

    def process_text(self, db: Session, text: str, *, chat_id: int | str | None = None) -> TelegramStrategyReply | None:
        """Return a Telegram reply when the text is a supported strategy command."""
        tokens = parsing.split_tokens(text)
        chat_key = self._chat_key(chat_id)

        command = parsing.normalize_command(tokens[0]) if tokens else ""
        if command not in self.COMMANDS:
            if chat_key and self._get_pending_edit(db, chat_key) is not None and text.strip():
                return self._handle_step_value(db, chat_key, text.strip())
            return None

        if chat_key:
            self._clear_pending_edit(db, chat_key)

        if command in {"/strategy", "/strategy_help"}:
            return TelegramStrategyReply(
                self._build_strategy_status(db, include_help=True),
                reply_markup=self._build_strategy_edit_markup(),
            )
        if command == "/strategy_set":
            return TelegramStrategyReply(self._handle_set(db, tokens[1:]))
        if command == "/strategy_clear":
            return TelegramStrategyReply(self._handle_clear(db, tokens[1:]))
        return None

    def process_callback(
        self,
        db: Session,
        callback_data: str,
        *,
        chat_id: int | str | None = None,
    ) -> TelegramStrategyReply | None:
        """Handle strategy edit inline button callbacks."""
        action = self.parse_callback_data(callback_data)
        if action is None:
            return None

        chat_key = self._chat_key(chat_id)
        if chat_key is None:
            return TelegramStrategyReply("채팅 정보를 확인할 수 없어 전략 편집을 시작할 수 없습니다.")

        if action == self.CANCEL_CALLBACK:
            self._clear_pending_edit(db, chat_key)
            return TelegramStrategyReply(
                "전략 수정이 취소되었습니다.",
                reply_markup=self._build_strategy_edit_markup(),
            )

        if action == self.APPLY_CALLBACK:
            return self._apply_pending_edit(db, chat_key)

        if action not in self.EDIT_FIELDS:
            return TelegramStrategyReply("지원하지 않는 전략 수정 항목입니다.", reply_markup=self._build_strategy_edit_markup())

        self._store_pending_edit(db, chat_key, PendingStrategyEdit(field_key=action, stage="awaiting_value"))
        field = self.EDIT_FIELDS[action]
        return TelegramStrategyReply(
            "\n".join([
                f"{field['label']} 새 값을 입력하세요.",
                field["help"],
                f"현재 값: {self._current_value_for(db, action)}",
                f"예시: {field['example']}",
            ]),
            reply_markup=self._build_cancel_markup(),
        )

    def parse_callback_data(self, callback_data: str) -> str | None:
        """Parse strategy edit callback payloads without touching bid-decision callbacks."""
        try:
            prefix, action = callback_data.split(":", maxsplit=1)
        except ValueError:
            return None
        if prefix != self.CALLBACK_PREFIX:
            return None
        return action

    def _handle_set(self, db: Session, args: list[str]) -> str:
        """Apply key=value updates to the stored strategy."""
        if not args:
            return self._build_help("수정할 key=value 항목이 없습니다.")

        strategy = ensure_operator_strategy(db)
        parsed_updates: dict[str, Any] = {}
        unknown_keys: list[str] = []

        for arg in args:
            if "=" not in arg:
                unknown_keys.append(arg)
                continue
            raw_key, raw_value = arg.split("=", maxsplit=1)
            normalized_key = raw_key.strip().lower().replace("-", "_")
            raw_value = raw_value.strip()

            try:
                resolved = self._resolve_assignment(normalized_key, raw_value)
            except ValueError as exc:
                return self._build_help(str(exc))
            if resolved is None:
                unknown_keys.append(raw_key)
            else:
                target_field, value = resolved
                parsed_updates[target_field] = value

        if unknown_keys:
            return self._build_help(f"지원하지 않는 항목: {', '.join(unknown_keys)}")
        if not parsed_updates:
            return self._build_help("적용 가능한 전략 항목이 없습니다.")

        error = self._validate_updates(strategy, parsed_updates)
        if error:
            return self._build_help(error)

        self._apply_updates(strategy, parsed_updates)
        self._persist_strategy_edit(db, strategy)
        return f"{self._build_strategy_status(db, include_help=False)}\n\n전략이 업데이트되었습니다."

    def _handle_clear(self, db: Session, args: list[str]) -> str:
        """Clear selected strategy fields or reset all watch rules."""
        if not args:
            return self._build_help("초기화할 항목을 지정하세요. 예: /strategy_clear categories budget")

        strategy = ensure_operator_strategy(db)
        fields_to_clear: set[str] = set()
        unknown_keys: list[str] = []
        for arg in args:
            normalized_key = arg.strip().lower().replace("-", "_")
            if normalized_key in self.CLEAR_GROUPS:
                fields_to_clear.update(self.CLEAR_GROUPS[normalized_key])
            else:
                unknown_keys.append(arg)

        if unknown_keys:
            return self._build_help(f"지원하지 않는 초기화 항목: {', '.join(unknown_keys)}")
        if not fields_to_clear:
            return self._build_help("초기화할 전략 항목이 없습니다.")

        self._apply_updates(strategy, {field: fields.default_value_for(field) for field in fields_to_clear})
        self._persist_strategy_edit(db, strategy)
        return f"{self._build_strategy_status(db, include_help=False)}\n\n전략 항목을 초기화했습니다."

    def _persist_strategy_edit(self, db: Session, strategy) -> None:
        """전략 편집 커밋 + 해당 운영자의 스냅샷 재계산 디스패치.

        텔레그램 set/clear/버튼은 웹 PUT 과 같은 행을 쓰므로 같은 갱신 트리거가
        필요하다(설계 §6.3): 기존 스냅샷 키만 단일비행 가드 하에 재계산한다.
        """
        db.commit()
        db.refresh(strategy)
        PreviewSnapshotService().dispatch_for_strategy_write(
            db, operator_id=int(strategy.user_id)
        )

    def _handle_step_value(self, db: Session, chat_key: str, raw_value: str) -> TelegramStrategyReply:
        """Validate a step-flow value and ask for final confirmation."""
        pending = self._get_pending_edit(db, chat_key)
        if pending is None:
            return TelegramStrategyReply("진행 중인 전략 수정이 없습니다.", reply_markup=self._build_strategy_edit_markup())

        if pending.stage != "awaiting_value":
            field = self.EDIT_FIELDS[pending.field_key]
            return TelegramStrategyReply(
                f"{field['label']} 변경을 먼저 적용하거나 취소하세요.",
                reply_markup=self._build_apply_cancel_markup(),
            )

        strategy = ensure_operator_strategy(db)
        try:
            updates = self._parse_step_updates(pending.field_key, raw_value)
        except ValueError as exc:
            return self._build_step_error_reply(db, pending.field_key, str(exc))

        error = self._validate_updates(strategy, updates)
        if error:
            return self._build_step_error_reply(db, pending.field_key, error)

        pending.stage = "awaiting_confirm"
        pending.updates = updates
        self._store_pending_edit(db, chat_key, pending)
        field = self.EDIT_FIELDS[pending.field_key]
        return TelegramStrategyReply(
            "\n".join([
                "적용 전 확인",
                f"항목: {field['label']}",
                f"현재 값: {self._current_value_for(db, pending.field_key)}",
                f"새 값: {render.format_updates(pending.field_key, updates)}",
                "적용 또는 취소를 선택하세요.",
            ]),
            reply_markup=self._build_apply_cancel_markup(),
        )

    def _apply_pending_edit(self, db: Session, chat_key: str) -> TelegramStrategyReply:
        """Apply a previously validated strategy edit."""
        pending = self._get_pending_edit(db, chat_key)
        if pending is None or pending.stage != "awaiting_confirm" or pending.updates is None:
            return TelegramStrategyReply(
                "적용할 전략 변경이 없습니다. /strategy에서 수정할 항목을 먼저 선택하세요.",
                reply_markup=self._build_strategy_edit_markup(),
            )

        strategy = ensure_operator_strategy(db)
        error = self._validate_updates(strategy, pending.updates)
        if error:
            self._clear_pending_edit(db, chat_key)
            return TelegramStrategyReply(
                self._build_help(error),
                reply_markup=self._build_strategy_edit_markup(),
            )

        self._apply_updates(strategy, pending.updates)
        self._persist_strategy_edit(db, strategy)
        self._clear_pending_edit(db, chat_key)
        return TelegramStrategyReply(
            f"{self._build_strategy_status(db, include_help=False)}\n\n전략이 업데이트되었습니다.",
            reply_markup=self._build_strategy_edit_markup(),
        )

    def _parse_step_updates(self, field_key: str, raw_value: str) -> dict[str, Any]:
        """Parse one button-selected strategy field into ORM update values."""
        if field_key == "categories":
            return {"focus_categories": parsing.parse_list(raw_value)}
        if field_key == "regions":
            return {"focus_regions": parsing.parse_list(raw_value)}
        if field_key == "keywords":
            return {"required_keywords": parsing.parse_list(raw_value)}
        if field_key == "budget":
            return self._parse_budget_step(raw_value)
        if field_key == "thresholds":
            updates = self._parse_assignment_updates(parsing.split_tokens(raw_value))
            allowed_fields = {
                "minimum_match_score",
                "minimum_probability_score",
                "bid_now_threshold",
                "review_threshold",
            }
            updates = {key: value for key, value in updates.items() if key in allowed_fields}
            if not updates:
                raise ValueError("임계치 값이 없습니다.")
            return updates
        if field_key == "notification":
            return {"notify_only_high_priority": parsing.parse_notification_scope(raw_value)}
        if field_key == "limit":
            return {
                "max_recommended_candidates": parsing.parse_number(
                    raw_value,
                    field_name="max_recommended_candidates",
                )
            }
        raise ValueError("지원하지 않는 전략 수정 항목입니다.")

    def _resolve_assignment(self, normalized_key: str, raw_value: str) -> tuple[str, Any] | None:
        """Resolve one normalized key=value into (target_field, parsed_value) via FIELD_SPECS.

        Returns ``None`` when the key is not a supported alias. Parser failures
        surface as ``ValueError`` so callers keep their own error handling.
        """
        spec = FIELD_SPEC_BY_ALIAS.get(normalized_key)
        if spec is None:
            return None
        return spec.target_field, spec.parser(self, raw_value, normalized_key)

    def _parse_assignment_updates(self, args: list[str]) -> dict[str, Any]:
        """Parse key=value snippets using the same aliases as /strategy_set."""
        parsed_updates: dict[str, Any] = {}
        unknown_keys: list[str] = []
        for arg in args:
            if "=" not in arg:
                unknown_keys.append(arg)
                continue
            raw_key, raw_value = arg.split("=", maxsplit=1)
            normalized_key = raw_key.strip().lower().replace("-", "_")
            raw_value = raw_value.strip()
            resolved = self._resolve_assignment(normalized_key, raw_value)
            if resolved is None:
                unknown_keys.append(raw_key)
            else:
                target_field, value = resolved
                parsed_updates[target_field] = value

        if unknown_keys:
            raise ValueError(f"지원하지 않는 항목: {', '.join(unknown_keys)}")
        return parsed_updates

    def _parse_budget_step(self, raw_value: str) -> dict[str, float]:
        """Parse button-flow budget input."""
        if "=" in raw_value:
            updates = self._parse_assignment_updates(parsing.split_tokens(raw_value))
            budget_updates = {
                key: value
                for key, value in updates.items()
                if key in {"min_budget_estimate", "max_budget_estimate"}
            }
            if budget_updates:
                return budget_updates
            raise ValueError("예산 값이 없습니다.")

        normalized = raw_value.replace("~", " ").replace("-", " ")
        values = [value for value in re.split(r"\s+", normalized.strip()) if value]
        if len(values) != 2:
            raise ValueError("예산은 최소/최대 두 숫자로 입력해야 합니다.")
        return {
            "min_budget_estimate": float(parsing.parse_number(values[0], field_name="min_budget")),
            "max_budget_estimate": float(parsing.parse_number(values[1], field_name="max_budget")),
        }

    def _build_step_error_reply(self, db: Session, field_key: str, error_message: str) -> TelegramStrategyReply:
        """Report validation errors without mutating the stored strategy."""
        field = self.EDIT_FIELDS[field_key]
        return render.build_step_error_reply(
            example=field["example"],
            current_value=self._current_value_for(db, field_key),
            error_message=error_message,
            cancel_markup=self._build_cancel_markup(),
        )

    def _build_strategy_status(self, db: Session, *, include_help: bool) -> str:
        """Build a concise strategy summary suitable for Telegram."""
        return render.build_strategy_status(ensure_operator_strategy(db), include_help=include_help)

    def _build_help(self, error_message: str | None) -> str:
        """Build command help, optionally prefixed with an error."""
        return render.build_help(error_message)

    def _build_strategy_edit_markup(self) -> dict[str, object]:
        """Build the /strategy inline edit buttons."""
        return render.build_strategy_edit_markup(self._build_callback_data)

    def _build_apply_cancel_markup(self) -> dict[str, object]:
        """Build confirmation buttons for a parsed step edit."""
        return render.build_apply_cancel_markup(
            self._build_callback_data, self.APPLY_CALLBACK, self.CANCEL_CALLBACK
        )

    def _build_cancel_markup(self) -> dict[str, object]:
        """Build a single cancel button for value entry prompts."""
        return render.build_cancel_markup(self._build_callback_data, self.CANCEL_CALLBACK)

    def _build_callback_data(self, action: str) -> str:
        """Build compact callback data within Telegram's 64-byte limit."""
        return f"{self.CALLBACK_PREFIX}:{action}"

    def _validate_updates(self, strategy, updates: dict[str, Any]) -> str | None:
        """Validate score ranges and cross-field threshold ordering."""
        return fields.validate_updates(strategy, updates)

    def _apply_updates(self, strategy, updates: dict[str, Any]) -> None:
        """Persist parsed update values onto the ORM object."""
        fields.apply_updates(strategy, updates)

    def _current_value_for(self, db: Session, field_key: str) -> str:
        """Format the current value for one button-edit field."""
        return render.current_value_for(ensure_operator_strategy(db), field_key)

    def _chat_key(self, chat_id: int | str | None) -> str | None:
        """Normalize Telegram chat ids for pending edit storage."""
        if chat_id is None:
            return None
        return str(chat_id)

    def _get_pending_edit(self, db: Session, chat_key: str) -> PendingStrategyEdit | None:
        """Load the latest pending edit state, preferring DB state across API workers."""
        payload = pending_store.load_latest_pending_edit(db, chat_key=chat_key)
        if payload is None:
            return self.PENDING_EDITS.get(chat_key)
        if not payload.active:
            self.PENDING_EDITS.pop(chat_key, None)
            return None
        pending = PendingStrategyEdit(
            field_key=str(payload.field_key or ""),
            stage=str(payload.stage or ""),
            updates=payload.updates,
        )
        self.PENDING_EDITS[chat_key] = pending
        return pending

    def _store_pending_edit(self, db: Session, chat_key: str, pending: PendingStrategyEdit) -> None:
        """Persist a staged edit so webhook updates can be handled by any API worker."""
        self.PENDING_EDITS[chat_key] = pending
        pending_store.record_pending_edit(
            db,
            event_models.TelegramStrategyPendingEditActivated(
                chat_id=chat_key,
                field_key=pending.field_key,
                stage=pending.stage,
                updates=pending.updates or {},
            ),
        )

    def _clear_pending_edit(self, db: Session, chat_key: str) -> None:
        """Clear any staged edit for a chat."""
        had_pending = self.PENDING_EDITS.pop(chat_key, None) is not None
        latest = self._get_pending_edit(db, chat_key)
        if had_pending or latest is not None:
            self.PENDING_EDITS.pop(chat_key, None)
            pending_store.record_pending_edit(
                db, event_models.TelegramStrategyPendingEditCleared(chat_id=chat_key)
            )
