"""Telegram command helpers for operator strategy edits."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shlex
from typing import Any, ClassVar

from sqlalchemy.orm import Session

from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ensure_operator_account,
    ensure_operator_strategy,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import Analytics
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
    get_strategy_category_priority_overrides,
)


@dataclass
class TelegramStrategyReply:
    """Outbound Telegram strategy response plus optional inline keyboard."""

    message: str
    reply_markup: dict[str, object] | None = None


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
    PENDING_EVENT_TYPE = "telegram.strategy.pending_edit"
    PENDING_EVENT_FETCH_LIMIT = 100
    PENDING_EDITS: ClassVar[dict[str, PendingStrategyEdit]] = {}

    EDIT_FIELDS = {
        "categories": {
            "label": "업종",
            "example": "software,security",
            "help": "쉼표로 구분해 관심 업종을 입력하세요. 비우려면 clear를 입력하세요.",
        },
        "regions": {
            "label": "지역",
            "example": "서울특별시,경기도",
            "help": "쉼표로 구분해 관심 지역을 입력하세요. 비우려면 clear를 입력하세요.",
        },
        "keywords": {
            "label": "키워드",
            "example": "AI,데이터",
            "help": "쉼표로 구분해 필수 키워드를 입력하세요. 비우려면 clear를 입력하세요.",
        },
        "budget": {
            "label": "예산",
            "example": "90000000 180000000",
            "help": "최소/최대 예산을 공백으로 입력하세요. 또는 min_budget=90000000 max_budget=180000000 형식을 사용할 수 있습니다.",
        },
        "thresholds": {
            "label": "임계치",
            "example": "match=0.65 probability=0.60 bid_now=0.75 review=0.50",
            "help": "0~1 사이 값으로 적합/확률/즉시투찰/검토 임계치를 입력하세요.",
        },
        "notification": {
            "label": "알림 범위",
            "example": "high_priority 또는 all",
            "help": "고우선순위만 받으려면 high_priority, 모든 후보를 받으려면 all을 입력하세요.",
        },
        "limit": {
            "label": "후보 수",
            "example": "10",
            "help": "전략 모니터링에서 추천할 최대 후보 수를 1~100 사이 숫자로 입력하세요.",
        },
    }

    LIST_FIELD_ALIASES = {
        "categories": "focus_categories",
        "category": "focus_categories",
        "focus_categories": "focus_categories",
        "regions": "focus_regions",
        "region": "focus_regions",
        "focus_regions": "focus_regions",
        "exclude_regions": "exclude_regions",
        "required_keywords": "required_keywords",
        "keywords": "required_keywords",
        "keyword": "required_keywords",
        "exclude_keywords": "exclude_keywords",
    }
    NUMERIC_FIELD_ALIASES = {
        "min_budget": "min_budget_estimate",
        "min_budget_estimate": "min_budget_estimate",
        "max_budget": "max_budget_estimate",
        "max_budget_estimate": "max_budget_estimate",
        "match": "minimum_match_score",
        "min_match": "minimum_match_score",
        "minimum_match_score": "minimum_match_score",
        "probability": "minimum_probability_score",
        "min_probability": "minimum_probability_score",
        "minimum_probability_score": "minimum_probability_score",
        "bid_now": "bid_now_threshold",
        "bid_now_threshold": "bid_now_threshold",
        "review": "review_threshold",
        "review_threshold": "review_threshold",
        "workload_penalty": "auto_workload_penalty_multiplier",
        "auto_workload_penalty_multiplier": "auto_workload_penalty_multiplier",
        "limit": "max_recommended_candidates",
        "max_candidates": "max_recommended_candidates",
        "max_recommended_candidates": "max_recommended_candidates",
    }
    BOOL_FIELD_ALIASES = {
        "high_priority": "notify_only_high_priority",
        "high_priority_only": "notify_only_high_priority",
        "notify_only_high_priority": "notify_only_high_priority",
    }
    CLEAR_GROUPS = {
        "categories": ("focus_categories",),
        "category": ("focus_categories",),
        "regions": ("focus_regions",),
        "region": ("focus_regions",),
        "exclude_regions": ("exclude_regions",),
        "keywords": ("required_keywords",),
        "keyword": ("required_keywords",),
        "exclude_keywords": ("exclude_keywords",),
        "budget": ("min_budget_estimate", "max_budget_estimate"),
        "thresholds": ("minimum_match_score", "minimum_probability_score", "bid_now_threshold", "review_threshold"),
        "priority_overrides": ("category_priority_overrides",),
        "high_priority": ("notify_only_high_priority",),
        "limit": ("max_recommended_candidates",),
        "all": (
            "focus_categories",
            "focus_regions",
            "exclude_regions",
            "required_keywords",
            "exclude_keywords",
            "min_budget_estimate",
            "max_budget_estimate",
            "minimum_match_score",
            "minimum_probability_score",
            "bid_now_threshold",
            "review_threshold",
            "auto_workload_penalty_multiplier",
            "category_priority_overrides",
            "notify_only_high_priority",
            "max_recommended_candidates",
        ),
    }

    def process_text(self, db: Session, text: str, *, chat_id: int | str | None = None) -> TelegramStrategyReply | None:
        """Return a Telegram reply when the text is a supported strategy command."""
        tokens = self._split_tokens(text)
        chat_key = self._chat_key(chat_id)

        command = self._normalize_command(tokens[0]) if tokens else ""
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
                if normalized_key in self.LIST_FIELD_ALIASES:
                    parsed_updates[self.LIST_FIELD_ALIASES[normalized_key]] = self._parse_list(raw_value)
                elif normalized_key in self.NUMERIC_FIELD_ALIASES:
                    parsed_updates[self.NUMERIC_FIELD_ALIASES[normalized_key]] = self._parse_number(
                        raw_value,
                        field_name=normalized_key,
                    )
                elif normalized_key in self.BOOL_FIELD_ALIASES:
                    parsed_updates[self.BOOL_FIELD_ALIASES[normalized_key]] = self._parse_bool(
                        raw_value,
                        field_name=normalized_key,
                    )
                elif normalized_key in {"category_priority_overrides", "priority_overrides"}:
                    parsed_updates["category_priority_overrides"] = self._parse_priority_overrides(raw_value)
                else:
                    unknown_keys.append(raw_key)
            except ValueError as exc:
                return self._build_help(str(exc))

        if unknown_keys:
            return self._build_help(f"지원하지 않는 항목: {', '.join(unknown_keys)}")
        if not parsed_updates:
            return self._build_help("적용 가능한 전략 항목이 없습니다.")

        error = self._validate_updates(strategy, parsed_updates)
        if error:
            return self._build_help(error)

        self._apply_updates(strategy, parsed_updates)
        db.commit()
        db.refresh(strategy)
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

        self._apply_updates(strategy, {field: self._default_value_for(field) for field in fields_to_clear})
        db.commit()
        db.refresh(strategy)
        return f"{self._build_strategy_status(db, include_help=False)}\n\n전략 항목을 초기화했습니다."

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
                f"새 값: {self._format_updates(pending.field_key, updates)}",
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
        db.commit()
        db.refresh(strategy)
        self._clear_pending_edit(db, chat_key)
        return TelegramStrategyReply(
            f"{self._build_strategy_status(db, include_help=False)}\n\n전략이 업데이트되었습니다.",
            reply_markup=self._build_strategy_edit_markup(),
        )

    def _parse_step_updates(self, field_key: str, raw_value: str) -> dict[str, Any]:
        """Parse one button-selected strategy field into ORM update values."""
        if field_key == "categories":
            return {"focus_categories": self._parse_list(raw_value)}
        if field_key == "regions":
            return {"focus_regions": self._parse_list(raw_value)}
        if field_key == "keywords":
            return {"required_keywords": self._parse_list(raw_value)}
        if field_key == "budget":
            return self._parse_budget_step(raw_value)
        if field_key == "thresholds":
            updates = self._parse_assignment_updates(self._split_tokens(raw_value))
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
            return {"notify_only_high_priority": self._parse_notification_scope(raw_value)}
        if field_key == "limit":
            return {
                "max_recommended_candidates": self._parse_number(
                    raw_value,
                    field_name="max_recommended_candidates",
                )
            }
        raise ValueError("지원하지 않는 전략 수정 항목입니다.")

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
            if normalized_key in self.LIST_FIELD_ALIASES:
                parsed_updates[self.LIST_FIELD_ALIASES[normalized_key]] = self._parse_list(raw_value)
            elif normalized_key in self.NUMERIC_FIELD_ALIASES:
                parsed_updates[self.NUMERIC_FIELD_ALIASES[normalized_key]] = self._parse_number(
                    raw_value,
                    field_name=normalized_key,
                )
            elif normalized_key in self.BOOL_FIELD_ALIASES:
                parsed_updates[self.BOOL_FIELD_ALIASES[normalized_key]] = self._parse_bool(
                    raw_value,
                    field_name=normalized_key,
                )
            elif normalized_key in {"category_priority_overrides", "priority_overrides"}:
                parsed_updates["category_priority_overrides"] = self._parse_priority_overrides(raw_value)
            else:
                unknown_keys.append(raw_key)

        if unknown_keys:
            raise ValueError(f"지원하지 않는 항목: {', '.join(unknown_keys)}")
        return parsed_updates

    def _parse_budget_step(self, raw_value: str) -> dict[str, float]:
        """Parse button-flow budget input."""
        if "=" in raw_value:
            updates = self._parse_assignment_updates(self._split_tokens(raw_value))
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
            "min_budget_estimate": float(self._parse_number(values[0], field_name="min_budget")),
            "max_budget_estimate": float(self._parse_number(values[1], field_name="max_budget")),
        }

    def _parse_notification_scope(self, raw_value: str) -> bool:
        """Parse notification range labels into notify_only_high_priority."""
        normalized = raw_value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"high", "high_priority", "high_priority_only", "only", "고우선순위", "고우선순위만"}:
            return True
        if normalized in {"all", "all_candidates", "전체", "전체후보", "모두"}:
            return False
        return self._parse_bool(raw_value, field_name="notification")

    def _build_step_error_reply(self, db: Session, field_key: str, error_message: str) -> TelegramStrategyReply:
        """Report validation errors without mutating the stored strategy."""
        field = self.EDIT_FIELDS[field_key]
        return TelegramStrategyReply(
            "\n".join([
                f"처리 실패: {error_message}",
                f"현재 값: {self._current_value_for(db, field_key)}",
                f"올바른 예시: {field['example']}",
            ]),
            reply_markup=self._build_cancel_markup(),
        )

    def _build_strategy_status(self, db: Session, *, include_help: bool) -> str:
        """Build a concise strategy summary suitable for Telegram."""
        strategy = ensure_operator_strategy(db)
        lines = [
            "[ 입찰 전략 ]",
            f"관심 업종: {self._format_list(split_multi_value_text(strategy.focus_categories))}",
            f"관심 지역: {self._format_list(split_multi_value_text(strategy.focus_regions))}",
            f"제외 지역: {self._format_list(split_multi_value_text(strategy.exclude_regions))}",
            f"필수 키워드: {self._format_list(split_multi_value_text(strategy.required_keywords))}",
            f"제외 키워드: {self._format_list(split_multi_value_text(strategy.exclude_keywords))}",
            f"예산 범위: {float(strategy.min_budget_estimate or 0.0):,.0f} ~ {float(strategy.max_budget_estimate or 0.0):,.0f}",
            (
                "임계치: "
                f"적합 {float(strategy.minimum_match_score or 0.0):.2f}, "
                f"확률 {float(strategy.minimum_probability_score or 0.0):.2f}, "
                f"즉시투찰 {float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD):.2f}, "
                f"검토 {float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD):.2f}"
            ),
            f"고우선순위만 알림: {'예' if bool(strategy.notify_only_high_priority) else '아니오'}",
            f"최대 후보 수: {int(strategy.max_recommended_candidates or 10)}",
        ]
        overrides = get_strategy_category_priority_overrides(strategy)
        if overrides:
            lines.append(
                "카테고리 보정: "
                + ", ".join(f"{category}={value:+.2f}" for category, value in sorted(overrides.items()))
            )
        if include_help:
            lines.extend(["", self._build_help(None)])
        return "\n".join(lines)

    def _build_help(self, error_message: str | None) -> str:
        """Build command help, optionally prefixed with an error."""
        lines: list[str] = []
        if error_message:
            lines.append(f"처리 실패: {error_message}")
            lines.append("")
        lines.extend([
            "사용법:",
            "/strategy",
            "/strategy_set categories=software,security regions=서울 keywords=AI,데이터 min_budget=90000000 max_budget=180000000 match=0.65 probability=0.60 bid_now=0.75 review=0.50 high_priority=true limit=10",
            "/strategy_clear categories regions keywords budget thresholds",
        ])
        return "\n".join(lines)

    def _build_strategy_edit_markup(self) -> dict[str, object]:
        """Build the /strategy inline edit buttons."""
        return {
            "inline_keyboard": [
                [
                    {"text": "업종", "callback_data": self._build_callback_data("categories")},
                    {"text": "지역", "callback_data": self._build_callback_data("regions")},
                    {"text": "키워드", "callback_data": self._build_callback_data("keywords")},
                ],
                [
                    {"text": "예산", "callback_data": self._build_callback_data("budget")},
                    {"text": "임계치", "callback_data": self._build_callback_data("thresholds")},
                ],
                [
                    {"text": "알림 범위", "callback_data": self._build_callback_data("notification")},
                    {"text": "후보 수", "callback_data": self._build_callback_data("limit")},
                ],
            ],
        }

    def _build_apply_cancel_markup(self) -> dict[str, object]:
        """Build confirmation buttons for a parsed step edit."""
        return {
            "inline_keyboard": [[
                {"text": "적용", "callback_data": self._build_callback_data(self.APPLY_CALLBACK)},
                {"text": "취소", "callback_data": self._build_callback_data(self.CANCEL_CALLBACK)},
            ]],
        }

    def _build_cancel_markup(self) -> dict[str, object]:
        """Build a single cancel button for value entry prompts."""
        return {
            "inline_keyboard": [[
                {"text": "취소", "callback_data": self._build_callback_data(self.CANCEL_CALLBACK)},
            ]],
        }

    def _build_callback_data(self, action: str) -> str:
        """Build compact callback data within Telegram's 64-byte limit."""
        return f"{self.CALLBACK_PREFIX}:{action}"

    def _split_tokens(self, text: str) -> list[str]:
        """Split command text while allowing quoted values."""
        try:
            return shlex.split((text or "").strip())
        except ValueError:
            return (text or "").strip().split()

    def _normalize_command(self, token: str) -> str:
        """Normalize Telegram slash commands that may include a bot username suffix."""
        command = token.strip().lower()
        if "@" in command:
            command = command.split("@", maxsplit=1)[0]
        return command

    def _parse_list(self, value: str) -> list[str]:
        """Parse comma or semicolon separated values."""
        if value.strip().lower() in {"", "-", "none", "null", "clear"}:
            return []
        normalized = value.replace(";", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]

    def _parse_number(self, value: str, *, field_name: str) -> float | int:
        """Parse a positive numeric field."""
        try:
            parsed = float(value.replace(",", ""))
        except ValueError:
            raise ValueError(f"{field_name} 값은 숫자여야 합니다.") from None

        if field_name in {"limit", "max_candidates", "max_recommended_candidates"}:
            return max(1, min(int(parsed), 100))
        return parsed

    def _parse_bool(self, value: str, *, field_name: str) -> bool:
        """Parse a compact boolean option."""
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on", "예", "네"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", "아니오", "아니요"}:
            return False
        raise ValueError(f"{field_name} 값은 true 또는 false여야 합니다.")

    def _parse_priority_overrides(self, value: str) -> dict[str, float]:
        """Parse category priority adjustments like software:+0.1,hardware:-0.2."""
        if value.strip().lower() in {"", "-", "none", "null", "clear"}:
            return {}

        overrides: dict[str, float] = {}
        for item in value.replace(";", ",").split(","):
            if not item.strip():
                continue
            if ":" in item:
                category, raw_score = item.split(":", maxsplit=1)
            elif "=" in item:
                category, raw_score = item.split("=", maxsplit=1)
            else:
                raise ValueError("category_priority_overrides는 category:+0.1 형식이어야 합니다.")
            category = category.strip()
            if not category:
                continue
            try:
                overrides[category] = float(raw_score.strip())
            except ValueError:
                raise ValueError(f"{category} 보정값은 숫자여야 합니다.") from None
        return overrides

    def _validate_updates(self, strategy, updates: dict[str, Any]) -> str | None:
        """Validate score ranges and cross-field threshold ordering."""
        numeric_ranges = {
            "min_budget_estimate": (0.0, None),
            "max_budget_estimate": (0.0, None),
            "minimum_match_score": (0.0, 1.0),
            "minimum_probability_score": (0.0, 1.0),
            "bid_now_threshold": (0.0, 1.0),
            "review_threshold": (0.0, 1.0),
            "auto_workload_penalty_multiplier": (0.0, 2.0),
            "max_recommended_candidates": (1, 100),
        }
        for field_name, (min_value, max_value) in numeric_ranges.items():
            if field_name not in updates:
                continue
            value = float(updates[field_name])
            if value < min_value:
                return f"{field_name} 값은 {min_value} 이상이어야 합니다."
            if max_value is not None and value > max_value:
                return f"{field_name} 값은 {max_value} 이하여야 합니다."

        next_bid_now = float(updates.get(
            "bid_now_threshold",
            strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
        ))
        next_review = float(updates.get(
            "review_threshold",
            strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD,
        ))
        if next_review > next_bid_now:
            return "review_threshold는 bid_now_threshold보다 클 수 없습니다."

        next_min_budget = float(updates.get("min_budget_estimate", strategy.min_budget_estimate or 0.0))
        next_max_budget = float(updates.get("max_budget_estimate", strategy.max_budget_estimate or 0.0))
        if next_min_budget > 0 and next_max_budget > 0 and next_min_budget > next_max_budget:
            return "min_budget은 max_budget보다 클 수 없습니다."
        return None

    def _apply_updates(self, strategy, updates: dict[str, Any]) -> None:
        """Persist parsed update values onto the ORM object."""
        for field_name, value in updates.items():
            if field_name in self.LIST_FIELD_ALIASES.values():
                setattr(strategy, field_name, join_multi_value_text(value))
            elif field_name == "category_priority_overrides":
                strategy.category_priority_overrides = dump_category_priority_overrides(value)
            elif field_name == "auto_workload_penalty_multiplier":
                strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(float(value))
            elif field_name == "max_recommended_candidates":
                strategy.max_recommended_candidates = max(1, min(int(value), 100))
            else:
                setattr(strategy, field_name, value)

    def _default_value_for(self, field_name: str) -> Any:
        """Return the default value used when a Telegram clear command resets a field."""
        defaults: dict[str, Any] = {
            "focus_categories": [],
            "focus_regions": [],
            "exclude_regions": [],
            "required_keywords": [],
            "exclude_keywords": [],
            "min_budget_estimate": 0.0,
            "max_budget_estimate": 0.0,
            "minimum_match_score": 0.6,
            "minimum_probability_score": 0.55,
            "bid_now_threshold": DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
            "review_threshold": DEFAULT_OPERATOR_REVIEW_THRESHOLD,
            "auto_workload_penalty_multiplier": 1.0,
            "category_priority_overrides": {},
            "notify_only_high_priority": True,
            "max_recommended_candidates": 10,
        }
        return defaults[field_name]

    def _current_value_for(self, db: Session, field_key: str) -> str:
        """Format the current value for one button-edit field."""
        strategy = ensure_operator_strategy(db)
        if field_key == "categories":
            return self._format_list(split_multi_value_text(strategy.focus_categories))
        if field_key == "regions":
            return self._format_list(split_multi_value_text(strategy.focus_regions))
        if field_key == "keywords":
            return self._format_list(split_multi_value_text(strategy.required_keywords))
        if field_key == "budget":
            return f"{float(strategy.min_budget_estimate or 0.0):,.0f} ~ {float(strategy.max_budget_estimate or 0.0):,.0f}"
        if field_key == "thresholds":
            return (
                f"적합 {float(strategy.minimum_match_score or 0.0):.2f}, "
                f"확률 {float(strategy.minimum_probability_score or 0.0):.2f}, "
                f"즉시투찰 {float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD):.2f}, "
                f"검토 {float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD):.2f}"
            )
        if field_key == "notification":
            return "고우선순위만" if bool(strategy.notify_only_high_priority) else "전체 후보"
        if field_key == "limit":
            return str(int(strategy.max_recommended_candidates or 10))
        return "확인 불가"

    def _format_updates(self, field_key: str, updates: dict[str, Any]) -> str:
        """Format staged updates for confirmation."""
        if field_key in {"categories", "regions", "keywords"}:
            value = next(iter(updates.values()))
            return self._format_list(value)
        if field_key == "budget":
            min_budget = updates.get("min_budget_estimate")
            max_budget = updates.get("max_budget_estimate")
            parts = []
            if min_budget is not None:
                parts.append(f"최소 {float(min_budget):,.0f}")
            if max_budget is not None:
                parts.append(f"최대 {float(max_budget):,.0f}")
            return ", ".join(parts)
        if field_key == "thresholds":
            labels = {
                "minimum_match_score": "적합",
                "minimum_probability_score": "확률",
                "bid_now_threshold": "즉시투찰",
                "review_threshold": "검토",
            }
            return ", ".join(f"{labels[key]} {float(value):.2f}" for key, value in updates.items())
        if field_key == "notification":
            return "고우선순위만" if updates.get("notify_only_high_priority") else "전체 후보"
        if field_key == "limit":
            return str(int(updates["max_recommended_candidates"]))
        return str(updates)

    def _chat_key(self, chat_id: int | str | None) -> str | None:
        """Normalize Telegram chat ids for pending edit storage."""
        if chat_id is None:
            return None
        return str(chat_id)

    def _get_pending_edit(self, db: Session, chat_key: str) -> PendingStrategyEdit | None:
        """Load the latest pending edit state, preferring DB state across API workers."""
        events = (
            db.query(Analytics)
            .filter(Analytics.event_type == self.PENDING_EVENT_TYPE)
            .order_by(Analytics.timestamp.desc(), Analytics.id.desc())
            .limit(self.PENDING_EVENT_FETCH_LIMIT)
            .all()
        )
        for event in events:
            payload = self._load_pending_payload(event.event_data)
            if payload.get("chat_id") != chat_key:
                continue
            if not payload.get("active"):
                self.PENDING_EDITS.pop(chat_key, None)
                return None
            pending = PendingStrategyEdit(
                field_key=str(payload.get("field_key") or ""),
                stage=str(payload.get("stage") or ""),
                updates=payload.get("updates") if isinstance(payload.get("updates"), dict) else None,
            )
            self.PENDING_EDITS[chat_key] = pending
            return pending
        return self.PENDING_EDITS.get(chat_key)

    def _store_pending_edit(self, db: Session, chat_key: str, pending: PendingStrategyEdit) -> None:
        """Persist a staged edit so webhook updates can be handled by any API worker."""
        self.PENDING_EDITS[chat_key] = pending
        self._record_pending_edit_event(
            db,
            {
                "chat_id": chat_key,
                "active": True,
                "field_key": pending.field_key,
                "stage": pending.stage,
                "updates": pending.updates or {},
            },
        )

    def _clear_pending_edit(self, db: Session, chat_key: str) -> None:
        """Clear any staged edit for a chat."""
        had_pending = self.PENDING_EDITS.pop(chat_key, None) is not None
        latest = self._get_pending_edit(db, chat_key)
        if had_pending or latest is not None:
            self.PENDING_EDITS.pop(chat_key, None)
            self._record_pending_edit_event(db, {"chat_id": chat_key, "active": False})

    def _record_pending_edit_event(self, db: Session, payload: dict[str, Any]) -> None:
        """Append internal pending-edit telemetry without exposing it as user analytics."""
        operator = ensure_operator_account(db)
        db.add(
            Analytics(
                user_id=operator.id,
                event_type=self.PENDING_EVENT_TYPE,
                event_data=json.dumps(payload, ensure_ascii=False),
            )
        )
        db.commit()

    def _load_pending_payload(self, event_data: str | None) -> dict[str, Any]:
        """Safely parse pending edit event payloads."""
        try:
            payload = json.loads(event_data or "{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _format_list(self, values: list[str]) -> str:
        """Format a list for a compact Telegram status message."""
        return ", ".join(values) if values else "없음"
