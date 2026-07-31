"""Celery task payload DTO — 브로커 경계에서 즉시 검증되는 방어적 계약.

발신 측은 payload 를 ``model_dump(mode="json")`` 으로 직렬화하지만, 수신 task 가
원시 ``dict`` 로 받아 ``payload.get(...)`` 으로 읽으면 브로커 왕복에서 타입 계약이
사라진다. 오타 키는 조용히 무시되고, 잘못된 타입은 손수 강제변환기(``int(x or 100)``)
에 흡수되어 **잘못된 값으로 작업이 그대로 진행**된다.

그래서 각 task 는 본문 첫 문장에서 payload 를 이 모듈의 DTO 로 승격하고, 이후 로직은
모델 필드만 사용한다. 여기 DTO 는 모두 ``StrictModel``(``extra="forbid"``) 기반이라
미지 필드를 즉시 거부한다 — 발신처와 필드가 어긋나면 조용히 흐르지 않고 실패한다.

배포 주의: ``extra="forbid"`` 는 **구버전 발신 메시지**(브로커에 남은 in-flight
payload)도 거부할 수 있으므로 api/worker/beat 를 함께 재시작한다.

이 모듈은 ``app/schemas/schemas.py`` re-export barrel 에 넣지 않는다. HTTP 공개 표면이
아니라 내부 task 경계 계약이므로 사용처에서 직접 import 한다.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, List, Optional, Union

from pydantic import Field, field_validator, model_validator

from app.core.constants import PaperBidAction, PriceScenario
from app.schemas._base import StrictModel
from app.schemas.crawl import CrawlRequest
from app.schemas.paper_bidding import ForwardPaperBiddingRunRequest
from app.schemas.prediction import PricePredictionTrainingRequest

__all__ = [
    "CrawlTaskRequest",
    "ForwardPaperBiddingTaskRequest",
    "HistoricalBacktestTaskRequest",
    "PricePredictionTrainingTaskRequest",
    "ScsbidReserveDetailBackfillRequest",
    "ScsbidReserveDetailNotice",
    "SyntheticOperatorBacktestTaskRequest",
    "TelegramInlineKeyboardButton",
    "TelegramInlineKeyboardMarkup",
    "TelegramNotificationTaskRequest",
]

# 값 집합은 한 곳에만 선언한다(§4.5-1). 시나리오(``PriceScenario``)와 정산 액션
# (``PaperBidAction``)은 HTTP 요청 모델(``app/schemas/paper_bidding.py``)과 같은 도메인
# 값이므로 ``app/core/constants.py`` 의 단일 출처를 그대로 쓴다.

# 발신 측이 실제로 쓰는 settle_actions 표현: 액션 리스트 · 콤마 문자열(beat 설정) ·
# 레거시 bool(저장된 experiment params) · 미지정.
SettleActionsInput = Union[bool, str, List[str], None]

_HISTORICAL_DEFAULT_SETTLE_ACTIONS: tuple[PaperBidAction, ...] = ("bid_now", "review")


def _split_comma_separated(value: SettleActionsInput) -> SettleActionsInput:
    """``"bid_now,review"`` 형태의 발신(beat 설정 문자열)만 리스트로 정규화한다.

    문자열이 아닌 입력은 그대로 흘려 pydantic 이 판정하게 둔다(여기서 타입을 삼키면
    검증 경계가 다시 느슨해진다).
    """
    if not isinstance(value, str):
        return value
    return [item.strip() for item in value.split(",") if item.strip()]


class TelegramInlineKeyboardButton(StrictModel):
    """Telegram inline 버튼 — ``callback_data`` 또는 ``url`` 중 정확히 하나를 갖는다."""

    text: str
    callback_data: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def _require_exactly_one_action(self) -> "TelegramInlineKeyboardButton":
        """액션 없는/중복 버튼을 거부한다.

        Bot API 는 액션 없는 버튼을 400 으로 되돌리고 둘을 함께 주면 ``url`` 만 살린다.
        경계에서 걸러야 운영자가 누른 버튼이 조용히 무동작으로 끝나지 않는다.
        """
        if (self.callback_data is None) == (self.url is None):
            raise ValueError("callback_data 와 url 중 정확히 하나가 필요합니다")
        return self


class TelegramInlineKeyboardMarkup(StrictModel):
    """Bot API ``reply_markup`` 의 inline keyboard 형태."""

    inline_keyboard: List[List[TelegramInlineKeyboardButton]] = Field(
        default_factory=list
    )

    def to_bot_api_payload(self) -> dict[str, list[list[dict[str, str]]]]:
        """Bot API 로 보낼 wire 형태(미설정 버튼 필드는 제외)."""
        return self.model_dump(mode="json", exclude_none=True)


class TelegramNotificationTaskRequest(StrictModel):
    """``jobs.send_telegram_notification`` payload.

    ``title`` + ``message`` 가 함께 오면 표준 알림 문구로 조립하고, ``message`` 만 오면
    그 문자열을 그대로 보낸다(기존 task 동작).
    """

    title: Optional[str] = None
    message: Optional[str] = None
    url: Optional[str] = None
    chat_id: Optional[str] = None
    reply_markup: Optional[TelegramInlineKeyboardMarkup] = None

    def build_text(
        self, build_message: Callable[[str, str, Optional[str]], str]
    ) -> str:
        """보낼 본문. ``title`` + ``message`` 가 함께 오면 표준 문구로 조립한다.

        문구 조립은 알림 서비스가 소유하므로 ``build_message`` 를 주입받는다(§4.7).
        """
        if self.title is not None and self.message is not None:
            return build_message(self.title, self.message, self.url)
        return self.message or ""

    def bot_api_reply_markup(self) -> Optional[dict[str, list[list[dict[str, str]]]]]:
        """Bot API 로 넘길 ``reply_markup`` (없으면 ``None``)."""
        return self.reply_markup.to_bot_api_payload() if self.reply_markup else None


class CrawlTaskRequest(StrictModel, CrawlRequest):
    """``jobs.collect_koneps_notices`` payload.

    필드 계약은 HTTP 요청 모델 ``CrawlRequest`` 를 그대로 재사용하고(제약 단일 출처)
    task 경계에서만 미지 필드를 거부한다. 발신처는 ``enqueue_koneps_notice_collection``
    (같은 모델 dump)과 KONEPS/scsbid beat 스케줄뿐이라 필드가 완전히 대칭이다.
    """


class PricePredictionTrainingTaskRequest(StrictModel, PricePredictionTrainingRequest):
    """``jobs.train_price_predictor`` payload.

    필드 계약은 HTTP 요청 모델 ``PricePredictionTrainingRequest`` 재사용. 발신처는
    ``POST /ml/training/price-predictor`` 하나이며 같은 모델을 dump 한다.
    """


class ScsbidReserveDetailNotice(StrictModel):
    """예비가격 상세 backfill 대상 공고 한 건."""

    notice_number: str = Field(min_length=1)
    category: str = ""

    @field_validator("notice_number", "category", mode="before")
    @classmethod
    def _normalize_text(cls, value: Optional[str]) -> Optional[str]:
        """앞뒤 공백 제거 · ``None`` 카테고리는 빈 문자열(기존 정규화와 동일).

        문자열이 아닌 입력은 그대로 흘려 pydantic 이 거부하게 둔다.
        """
        if value is None:
            return ""
        return value.strip() if isinstance(value, str) else value


class ScsbidReserveDetailBackfillRequest(StrictModel):
    """``jobs.backfill_scsbid_reserve_detail`` payload (self-chain 이어받기 포함)."""

    notices: List[ScsbidReserveDetailNotice] = Field(default_factory=list)

    def deduped(self) -> list[ScsbidReserveDetailNotice]:
        """(notice_number, category) 순서 보존 dedupe — 한 청크 내 재조회를 막는다."""
        seen: set[tuple[str, str]] = set()
        unique: list[ScsbidReserveDetailNotice] = []
        for notice in self.notices:
            key = (notice.notice_number, notice.category)
            if key in seen:
                continue
            seen.add(key)
            unique.append(notice)
        return unique


class SyntheticOperatorBacktestTaskRequest(StrictModel):
    """``jobs.run_synthetic_operator_backtest`` payload.

    두 발신 경로를 함께 담는다.

    * ad-hoc (``POST /synthetic/backtests/run-async``): 윈도우/필터만.
    * Experiment Lab (``create_run``): 저장된 params + ``experiment_id``/``run_id``
      (+ sample-gap 출처 컨텍스트). ``cutoff_hours`` 는 저장된 params 의 레거시 별칭.
    """

    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=1000)
    scenario: str = "base"
    slugs: Optional[List[str]] = None
    cutoff_hours_before_deadline: Optional[int] = Field(default=None, ge=0, le=168)
    cutoff_hours: Optional[int] = Field(default=None, ge=0)
    # 상한 없음(의도): 발신 ``SyntheticExperimentParams.history_limit`` 도 상한이 없어
    # 이미 저장된 800 같은 값이 종전 coercer 를 통과했다. 수신을 발신보다 좁게 만들면
    # 산출이 바뀌므로(=검증 실패로 실행 자체가 사라짐) ge 만 유지한다.
    history_limit: Optional[int] = Field(default=None, ge=1)
    # 레거시 발신 형태 보존: bool 은 "기본 액션 사용"을 뜻하고 리스트는 명시 선택이다.
    settle_actions: Union[bool, List[PaperBidAction], None] = None
    experiment_id: Optional[int] = None
    run_id: Optional[int] = None
    source_sample_gap_candidate: Optional[dict[str, Any]] = None

    @field_validator("settle_actions", mode="before")
    @classmethod
    def _normalize_settle_actions(
        cls, value: SettleActionsInput
    ) -> SettleActionsInput:
        return _split_comma_separated(value)

    @property
    def is_experiment_run(self) -> bool:
        """Experiment Lab 실행 여부 — run/result 라이프사이클을 영속화하는 경로."""
        return self.run_id is not None

    def resolved_cutoff_hours(self) -> Optional[int]:
        """마감 전 컷오프. 별칭(``cutoff_hours``)은 정식 필드가 없을 때만 쓴다.

        experiment 러너의 ``_first_present`` 와 같은 우선순위라, 어떤 발신 경로로 와도
        별칭이 조용히 무시되지 않는다.
        """
        if self.cutoff_hours_before_deadline is not None:
            return self.cutoff_hours_before_deadline
        return self.cutoff_hours

    def resolved_settle_actions(self) -> Optional[tuple[PaperBidAction, ...]]:
        """ad-hoc 경로 인자. bool/미지정은 액션 필터 없음(``None``)으로 흘린다."""
        if isinstance(self.settle_actions, list):
            return tuple(self.settle_actions)
        return None


class ForwardPaperBiddingTaskRequest(StrictModel, ForwardPaperBiddingRunRequest):
    """``jobs.run_forward_paper_bidding`` payload.

    필드 계약은 HTTP 요청 모델 ``ForwardPaperBiddingRunRequest`` 를 그대로 재사용하고
    (제약 단일 출처), 스케줄 발신의 기본 ``strategy_version`` 만 덮어쓴다.
    """

    strategy_version: str = "scheduled-forward-paper"


class HistoricalBacktestTaskRequest(StrictModel):
    """``jobs.run_historical_backtest`` payload.

    HTTP 요청 모델(``PaperBiddingRunRequest``)을 상속하지 않는다: 이 task 는 윈도우를
    ``lookback_days`` 로 계산하므로 ``start_at``/``end_at`` 을 받으면 **조용히 무시**되는
    필드가 생긴다. 그래서 task 가 실제로 소비하는 필드만 선언한다.
    """

    category: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=5000)
    scenario: PriceScenario = "base"
    strategy_version: str = "scheduled-historical-backtest"
    model_version: str = "current"
    cutoff_hours_before_deadline: int = Field(default=2, ge=0, le=168)
    history_limit: int = Field(default=80, ge=1, le=500)
    settle_actions: List[PaperBidAction] = Field(
        default_factory=lambda: list(_HISTORICAL_DEFAULT_SETTLE_ACTIONS)
    )
    persist: bool = True
    lookback_days: Optional[int] = Field(default=None, ge=1)

    @field_validator("settle_actions", mode="before")
    @classmethod
    def _normalize_settle_actions(
        cls, value: SettleActionsInput
    ) -> SettleActionsInput:
        """beat 은 콤마 문자열로 보낸다. 명시적 ``None`` 은 기본 액션으로 되돌린다."""
        if value is None:
            return list(_HISTORICAL_DEFAULT_SETTLE_ACTIONS)
        return _split_comma_separated(value)
