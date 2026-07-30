"""``app.schemas.task_payloads`` celery payload DTO 계약 테스트.

각 task 는 브로커 payload 를 이 DTO 로 승격한 뒤 모델 필드만 사용한다. 그래서 여기서는
**정상 payload 의 값 보존**과 **거부 경로**(미지 필드 · 타입/범위 위반)를 함께 고정한다.
정규화(콤마 문자열 · 레거시 bool · 공백/중복 공고)는 손수 강제변환기 대신 DTO 가 소유하는
규칙이므로 값 테이블로 검증한다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import annotated_types
import pytest
from pydantic import BaseModel, ValidationError

# run-async 엔드포인트의 요청 모델(=synthetic task 의 ad-hoc 발신 계약)은 라우터 모듈에
# 선언되어 있다. 제약 대조에 필요하므로 여기서만 별칭으로 가져온다.
from app.api.synthetic import SyntheticBacktestRunRequest as _SyntheticBacktestRunRequest
from app.schemas.crawl import CrawlRequest
from app.schemas.paper_bidding import (
    ForwardPaperBiddingRunRequest,
    PaperBiddingRunRequest,
)
from app.schemas.prediction import PricePredictionTrainingRequest
from app.schemas.synthetic import SyntheticExperimentParams
from app.schemas.task_payloads import (
    CrawlTaskRequest,
    ForwardPaperBiddingTaskRequest,
    HistoricalBacktestTaskRequest,
    PricePredictionTrainingTaskRequest,
    ScsbidReserveDetailBackfillRequest,
    SyntheticOperatorBacktestTaskRequest,
    TelegramInlineKeyboardButton,
    TelegramInlineKeyboardMarkup,
    TelegramNotificationTaskRequest,
)


def _join_titled(title: str, message: str, url: str | None) -> str:
    """``TelegramNotificationService.build_message`` 자리의 주입 가짜."""
    return f"[{title}]{message}{url or ''}"


class TestTelegramNotificationTaskRequest:
    def test_titled_message_uses_injected_builder(self) -> None:
        request = TelegramNotificationTaskRequest.model_validate(
            {"title": "제목", "message": "본문", "url": "https://x"}
        )
        assert request.build_text(_join_titled) == "[제목]본문https://x"

    def test_message_only_is_sent_verbatim(self) -> None:
        request = TelegramNotificationTaskRequest.model_validate({"message": "본문"})
        assert request.build_text(_join_titled) == "본문"

    def test_empty_payload_sends_empty_text(self) -> None:
        request = TelegramNotificationTaskRequest.model_validate({})
        assert request.build_text(_join_titled) == ""
        assert request.bot_api_reply_markup() is None

    def test_reply_markup_round_trips_to_bot_api_shape(self) -> None:
        markup = {"inline_keyboard": [[{"text": "✅ 투찰", "callback_data": "bd:1:submit"}]]}
        request = TelegramNotificationTaskRequest.model_validate(
            {"message": "본문", "reply_markup": markup}
        )
        # ``callback_data`` 만 있는 버튼은 ``url`` 키 없이 그대로 나간다.
        assert request.bot_api_reply_markup() == markup

    def test_malformed_reply_markup_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TelegramNotificationTaskRequest.model_validate(
                {"message": "본문", "reply_markup": {"inline_keyboard": "not-a-grid"}}
            )

    def test_unknown_button_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            TelegramInlineKeyboardMarkup.model_validate(
                {"inline_keyboard": [[{"text": "x", "callbackData": "typo"}]]}
            )
        assert "callbackData" in str(excinfo.value)

    def test_url_button_is_accepted(self) -> None:
        button = TelegramInlineKeyboardButton.model_validate(
            {"text": "상세보기", "url": "https://x"}
        )
        assert button.url == "https://x"

    def test_button_without_an_action_is_rejected(self) -> None:
        """액션 없는 버튼은 Bot API 400 이고, 눌러도 무동작이라 경계에서 거부한다."""
        with pytest.raises(ValidationError) as excinfo:
            TelegramInlineKeyboardButton.model_validate({"text": "무동작"})
        assert "callback_data" in str(excinfo.value)

    def test_button_with_both_actions_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TelegramInlineKeyboardButton.model_validate(
                {"text": "둘다", "callback_data": "bd:1:submit", "url": "https://x"}
            )

    def test_unknown_top_level_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            TelegramNotificationTaskRequest.model_validate({"mesage": "typo"})
        assert "mesage" in str(excinfo.value)


class TestScsbidReserveDetailBackfillRequest:
    def test_strips_and_defaults_category(self) -> None:
        request = ScsbidReserveDetailBackfillRequest.model_validate(
            {"notices": [{"notice_number": " N-1 "}, {"notice_number": "N-2", "category": None}]}
        )
        assert [(n.notice_number, n.category) for n in request.notices] == [
            ("N-1", ""),
            ("N-2", ""),
        ]

    def test_dedupe_preserves_first_seen_order(self) -> None:
        request = ScsbidReserveDetailBackfillRequest.model_validate(
            {
                "notices": [
                    {"notice_number": "DUP", "category": "construction"},
                    {"notice_number": "DUP", "category": "construction"},
                    {"notice_number": "DUP", "category": "service"},
                    {"notice_number": "OTHER", "category": ""},
                ]
            }
        )
        assert [(n.notice_number, n.category) for n in request.deduped()] == [
            ("DUP", "construction"),
            ("DUP", "service"),
            ("OTHER", ""),
        ]

    def test_empty_notices_default(self) -> None:
        assert ScsbidReserveDetailBackfillRequest.model_validate({}).notices == []

    def test_blank_notice_number_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ScsbidReserveDetailBackfillRequest.model_validate(
                {"notices": [{"notice_number": "   "}]}
            )
        assert "notice_number" in str(excinfo.value)

    def test_missing_notice_number_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ScsbidReserveDetailBackfillRequest.model_validate(
                {"notices": [{"category": "service"}]}
            )

    def test_unknown_notice_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ScsbidReserveDetailBackfillRequest.model_validate(
                {"notices": [{"notice_number": "N-1", "noticeNumber": "typo"}]}
            )
        assert "noticeNumber" in str(excinfo.value)


class TestSyntheticOperatorBacktestTaskRequest:
    def test_ad_hoc_payload_keeps_window_and_filters(self) -> None:
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": None,
                "category": "construction",
                "limit": 200,
                "scenario": "base",
                "slugs": ["cn-small-gangwon"],
                "cutoff_hours_before_deadline": 4,
                "history_limit": 60,
                "settle_actions": ["bid_now", "review"],
            }
        )
        assert request.start_at == datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert request.is_experiment_run is False
        assert request.resolved_settle_actions() == ("bid_now", "review")

    def test_experiment_payload_is_detected_by_run_id(self) -> None:
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"experiment_id": 3, "run_id": 9, "slugs": None, "settle_actions": False}
        )
        assert request.is_experiment_run is True
        # 레거시 bool 은 액션 필터 없음으로 흘린다(ad-hoc 경로 기존 동작).
        assert request.resolved_settle_actions() is None

    def test_history_limit_has_no_upper_bound(self) -> None:
        """발신 ``SyntheticExperimentParams.history_limit`` 에 상한이 없어 수신도 없다.

        상한을 두면 이미 저장된 큰 값(예: 800)이 검증 실패로 실행 자체를 잃는다.
        """
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"history_limit": 800}
        )
        assert request.history_limit == 800

    def test_zero_cutoff_hours_is_preserved(self) -> None:
        """0시간 컷오프는 유효한 선택 — 종전 falsy 흡수와 달리 그대로 흐른다."""
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"cutoff_hours_before_deadline": 0}
        )
        assert request.cutoff_hours_before_deadline == 0
        assert request.resolved_cutoff_hours() == 0

    def test_cutoff_hours_alias_is_used_only_as_a_fallback(self) -> None:
        """저장된 params 의 레거시 별칭이 조용히 무시되지 않는다."""
        alias_only = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"cutoff_hours": 6}
        )
        both = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"cutoff_hours": 6, "cutoff_hours_before_deadline": 2}
        )
        neither = SyntheticOperatorBacktestTaskRequest.model_validate({})

        assert alias_only.resolved_cutoff_hours() == 6
        assert both.resolved_cutoff_hours() == 2
        assert neither.resolved_cutoff_hours() is None

    def test_comma_string_settle_actions_are_split(self) -> None:
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {"settle_actions": "bid_now, review"}
        )
        assert request.settle_actions == ["bid_now", "review"]

    def test_absent_settle_actions_stay_unfiltered(self) -> None:
        request = SyntheticOperatorBacktestTaskRequest.model_validate({})
        assert request.settle_actions is None
        assert request.resolved_settle_actions() is None

    def test_zero_limit_is_rejected_instead_of_defaulting(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyntheticOperatorBacktestTaskRequest.model_validate({"limit": 0})
        assert "limit" in str(excinfo.value)

    def test_unknown_settle_action_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SyntheticOperatorBacktestTaskRequest.model_validate(
                {"settle_actions": ["bid_naw"]}
            )

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            SyntheticOperatorBacktestTaskRequest.model_validate({"limt": 5})
        assert "limt" in str(excinfo.value)


class TestForwardPaperBiddingTaskRequest:
    def test_reuses_http_request_field_contract(self) -> None:
        """필드 계약은 HTTP 요청 모델에서 상속한다(제약 단일 출처)."""
        assert set(ForwardPaperBiddingRunRequest.model_fields) == set(
            ForwardPaperBiddingTaskRequest.model_fields
        )

    def test_schedule_default_strategy_version(self) -> None:
        request = ForwardPaperBiddingTaskRequest.model_validate({})
        assert request.strategy_version == "scheduled-forward-paper"
        assert (request.limit, request.history_limit, request.persist) == (100, 80, True)

    def test_limit_ceiling_is_enforced(self) -> None:
        with pytest.raises(ValidationError):
            ForwardPaperBiddingTaskRequest.model_validate({"limit": 5000})

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ForwardPaperBiddingTaskRequest.model_validate({"persist_run": True})
        assert "persist_run" in str(excinfo.value)


class TestHistoricalBacktestTaskRequest:
    def test_beat_payload_shape(self) -> None:
        request = HistoricalBacktestTaskRequest.model_validate(
            {
                "category": None,
                "limit": 100,
                "scenario": "base",
                "lookback_days": 30,
                "history_limit": 80,
                "cutoff_hours_before_deadline": 2,
                "settle_actions": "bid_now,review",
                "strategy_version": "scheduled-historical-backtest",
                "model_version": "current",
                "persist": True,
            }
        )
        assert request.settle_actions == ["bid_now", "review"]
        assert request.lookback_days == 30

    def test_defaults_match_the_legacy_coercers(self) -> None:
        request = HistoricalBacktestTaskRequest.model_validate({})
        assert request.limit == 100
        assert request.scenario == "base"
        assert request.strategy_version == "scheduled-historical-backtest"
        assert request.model_version == "current"
        assert request.cutoff_hours_before_deadline == 2
        assert request.history_limit == 80
        assert request.settle_actions == ["bid_now", "review"]
        assert request.persist is True
        assert request.lookback_days is None

    def test_explicit_none_settle_actions_falls_back_to_defaults(self) -> None:
        request = HistoricalBacktestTaskRequest.model_validate({"settle_actions": None})
        assert request.settle_actions == ["bid_now", "review"]

    def test_zero_cutoff_hours_is_preserved(self) -> None:
        """의도된 변경: 종전 ``int(x or 2)`` 는 0 을 2 로 흡수했으나 이제 0 을 지킨다."""
        request = HistoricalBacktestTaskRequest.model_validate(
            {"cutoff_hours_before_deadline": 0}
        )
        assert request.cutoff_hours_before_deadline == 0

    def test_unknown_scenario_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HistoricalBacktestTaskRequest.model_validate({"scenario": "moonshot"})

    def test_zero_lookback_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            HistoricalBacktestTaskRequest.model_validate({"lookback_days": 0})

    def test_start_at_is_not_a_silently_ignored_field(self) -> None:
        """윈도우는 ``lookback_days`` 로 계산하므로 ``start_at`` 은 받지 않는다."""
        with pytest.raises(ValidationError) as excinfo:
            HistoricalBacktestTaskRequest.model_validate(
                {"start_at": "2025-01-01T00:00:00Z"}
            )
        assert "start_at" in str(excinfo.value)


class TestStrictReuseSubclasses:
    """HTTP 요청 모델을 그대로 재사용하면서 task 경계에서만 미지 필드를 거부한다."""

    def test_crawl_task_request_reuses_the_field_contract(self) -> None:
        assert set(CrawlTaskRequest.model_fields) == set(CrawlRequest.model_fields)
        assert CrawlTaskRequest.model_config["extra"] == "forbid"
        assert CrawlTaskRequest.model_validate(
            {"source": "scsbid-openapi", "max_items": 25}
        ).max_items == 25

    def test_crawl_task_request_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            CrawlTaskRequest.model_validate({"soruce": "typo"})
        assert "soruce" in str(excinfo.value)

    def test_training_task_request_reuses_the_field_contract(self) -> None:
        assert set(PricePredictionTrainingTaskRequest.model_fields) == set(
            PricePredictionTrainingRequest.model_fields
        )
        assert PricePredictionTrainingTaskRequest.model_config["extra"] == "forbid"

    def test_training_task_request_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            PricePredictionTrainingTaskRequest.model_validate({"relase_tag": "typo"})
        assert "relase_tag" in str(excinfo.value)

    def test_training_task_request_keeps_only_sent_keys_on_dump(self) -> None:
        """ML 서비스는 dict 계약이라 발신자가 보낸 키만 넘겨 서비스 기본값을 지킨다."""
        request = PricePredictionTrainingTaskRequest.model_validate(
            {"create_manifest": False, "publish_remote": False}
        )
        assert request.model_dump(mode="json", exclude_unset=True) == {
            "create_manifest": False,
            "publish_remote": False,
        }


def _bounds(model: type[BaseModel], field_name: str) -> tuple[float | None, float | None]:
    """필드의 (하한, 상한) — 선언되지 않은 쪽은 ``None``."""
    lower: float | None = None
    upper: float | None = None
    for constraint in model.model_fields[field_name].metadata:
        if isinstance(constraint, annotated_types.Ge):
            lower = float(constraint.ge)  # type: ignore[arg-type]
        elif isinstance(constraint, annotated_types.Gt):
            lower = float(constraint.gt) + 1  # type: ignore[arg-type]
        elif isinstance(constraint, annotated_types.Le):
            upper = float(constraint.le)  # type: ignore[arg-type]
        elif isinstance(constraint, annotated_types.Lt):
            upper = float(constraint.lt) - 1  # type: ignore[arg-type]
    return lower, upper


class TestSenderReceiverConstraintDrift:
    """수신 DTO 제약이 **발신 모델보다 좁아지지 않는지** 고정한다.

    좁으면 발신 측이 만들 수 있는 값(이미 저장된 params 포함)이 수신에서
    ``ValidationError`` 로 죽어 작업 자체가 사라진다 — 검증 도입이 산출을 바꾸는 회귀다.
    반대 방향(수신이 더 넓음)은 허용한다: 검증은 발신 계약을 조이는 게 아니라 브로커
    왕복에서 잃은 타입을 되돌리는 것이기 때문이다.
    """

    PAIRS = [
        pytest.param(
            SyntheticExperimentParams,
            SyntheticOperatorBacktestTaskRequest,
            id="experiment_params->synthetic_task",
        ),
        pytest.param(
            _SyntheticBacktestRunRequest,
            SyntheticOperatorBacktestTaskRequest,
            id="synthetic_http_request->synthetic_task",
        ),
        pytest.param(
            CrawlRequest, CrawlTaskRequest, id="crawl_request->crawl_task"
        ),
        pytest.param(
            PricePredictionTrainingRequest,
            PricePredictionTrainingTaskRequest,
            id="training_request->training_task",
        ),
        pytest.param(
            ForwardPaperBiddingRunRequest,
            ForwardPaperBiddingTaskRequest,
            id="forward_request->forward_task",
        ),
        pytest.param(
            PaperBiddingRunRequest,
            HistoricalBacktestTaskRequest,
            id="paper_bidding_request->historical_task",
        ),
    ]

    @pytest.mark.parametrize(("sender", "receiver"), PAIRS)
    def test_receiver_constraints_are_not_narrower(
        self, sender: type[BaseModel], receiver: type[BaseModel]
    ) -> None:
        shared = set(sender.model_fields) & set(receiver.model_fields)
        assert shared, "공유 필드가 없으면 대조할 계약이 없다"

        narrower: list[str] = []
        for field_name in sorted(shared):
            sender_low, sender_high = _bounds(sender, field_name)
            receiver_low, receiver_high = _bounds(receiver, field_name)
            if receiver_low is not None and (
                sender_low is None or receiver_low > sender_low
            ):
                narrower.append(f"{field_name}: 하한 {sender_low} -> {receiver_low}")
            if receiver_high is not None and (
                sender_high is None or receiver_high < sender_high
            ):
                narrower.append(f"{field_name}: 상한 {sender_high} -> {receiver_high}")

        assert not narrower, (
            f"{receiver.__name__} 제약이 {sender.__name__} 보다 좁습니다: {narrower}. "
            "발신이 만들 수 있는 값을 수신이 거부하면 그 작업은 조용히 사라집니다."
        )

    @pytest.mark.parametrize(("sender", "receiver"), PAIRS)
    def test_receiver_declares_every_shared_sender_field(
        self, sender: type[BaseModel], receiver: type[BaseModel]
    ) -> None:
        """발신 필드가 수신 DTO 에 없으면 ``extra="forbid"`` 가 그 payload 를 거부한다.

        historical task 는 의도적으로 ``start_at``/``end_at`` 을 받지 않으므로(윈도우는
        ``lookback_days`` 로 계산) 그 두 필드만 예외로 둔다.
        """
        allowed_gaps = {"start_at", "end_at"}
        missing = set(sender.model_fields) - set(receiver.model_fields) - allowed_gaps
        assert not missing, f"{receiver.__name__} 에 없는 발신 필드: {sorted(missing)}"
