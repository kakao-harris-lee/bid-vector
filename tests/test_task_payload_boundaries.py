"""celery task 경계 검증 — 발신/수신 대칭 + happy/sad.

브로커 왕복은 ``발신 model_dump(mode="json") -> dict -> 수신 model_validate`` 다. 수신
task 가 복원하지 않으면 타입 계약이 사라지고, 손수 강제변환기(``int(x or 100)``)가 잘못된
값을 흡수해 **작업이 조용히 잘못 진행**된다. 그래서 task 마다

1. 정상 payload 가 검증된 모델로 승격되어 협력자에게 전달되는지,
2. 필수 필드 누락/타입·범위 위반 payload 가 ``ValidationError`` 로 차단되고 **협력자가
   호출되지 않는지**(= 조용히 진행되지 않음),
3. 실제 발신처(API sender · beat 스케줄 · self-chain 이어받기)의 payload 가 수신 DTO 로
   그대로 복원되는지(필드 대칭)

를 고정한다. ``ENVIRONMENT=test`` 이므로 Telegram/외부 호출은 발생하지 않는다.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

import app.tasks.backtest_jobs as backtest_jobs
import app.tasks.jobs as jobs
from app.core import database as database_mod
from app.core.config import settings
from app.schemas.crawl import CrawlRequest
from app.schemas.prediction import PricePredictionTrainingRequest
from app.schemas.task_payloads import (
    CrawlTaskRequest,
    ForwardPaperBiddingTaskRequest,
    HistoricalBacktestTaskRequest,
    PricePredictionTrainingTaskRequest,
    ScsbidReserveDetailBackfillRequest,
    ScsbidReserveDetailNotice,
    SyntheticOperatorBacktestTaskRequest,
)
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.synthetic_experiment.constants import SYNTHETIC_EXPERIMENT_PRESETS
from app.services.synthetic_experiment.serialization import _parse_dt
from app.tasks.celery_app import (
    build_historical_backtest_beat_schedule,
    build_koneps_collection_beat_schedule,
    build_paper_bidding_forward_beat_schedule,
    build_price_predictor_training_beat_schedule,
    build_scsbid_collection_beat_schedule,
)


class _DummyDB:
    """Session stand-in so db-owning task shells run without a database."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Handle:
    """``apply_async`` 반환 대체 — 상태 조회에 쓰이는 id 만 갖는다."""

    def __init__(self, task_id: str = "task-1") -> None:
        self.id = task_id


def _capture_apply_async(store: dict):
    def _fake(*args, **kwargs):
        store.update(kwargs)
        return _Handle()

    return _fake


def _patch_task_session(monkeypatch, session_factory) -> None:
    """``task_session()`` 기본 팩토리의 단일 patch 표면(D1 세션 seam).

    task shell 은 ``with task_session() as db`` 로 세션을 잡고 기본 팩토리를 호출
    시점에 해석하므로, 여기 하나만 바꿔도 shell 전체에 도달한다.
    """
    monkeypatch.setattr(database_mod, "SessionLocal", session_factory)


# ---------------------------------------------------------------------------
# jobs.collect_koneps_notices
# ---------------------------------------------------------------------------
class TestCollectKonepsNoticesBoundary:
    def test_valid_payload_is_promoted_to_crawl_request(self, monkeypatch):
        captured: dict = {}

        def _spy(self_arg, *, request, crawl_job_id, **_injected):
            captured.update(request=request, crawl_job_id=crawl_job_id)
            return {"ok": True}

        monkeypatch.setattr(jobs, "run_koneps_collection_job", _spy)

        out = jobs.collect_koneps_notices.run(
            request_payload={"source": "scsbid-openapi", "max_items": 25},
            crawl_job_id=3,
        )

        assert out == {"ok": True}
        assert captured["request"] == CrawlTaskRequest(
            source="scsbid-openapi", max_items=25
        )
        # 승격된 모델은 body 가 기대하는 HTTP 계약(CrawlRequest)을 그대로 만족한다.
        assert isinstance(captured["request"], CrawlRequest)

    def test_out_of_range_payload_is_rejected_before_the_body_runs(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            jobs,
            "run_koneps_collection_job",
            lambda *a, **k: calls.append("ran") or {},
        )

        with pytest.raises(ValidationError):
            jobs.collect_koneps_notices.run(request_payload={"max_items": 9_999})

        assert calls == []

    def test_sender_dump_round_trips_into_the_task_dto(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            jobs.collect_koneps_notices, "apply_async", _capture_apply_async(captured)
        )
        request = CrawlRequest(
            source="scsbid-openapi",
            categories=["construction", "service"],
            lookback_days=3,
            execution_mode="live",
        )

        jobs.enqueue_koneps_notice_collection(request=request, crawl_job_id=None)

        payload = captured["kwargs"]["request_payload"]
        assert CrawlRequest.model_validate(payload) == request
        # 수신 DTO 로도 그대로 복원된다(미지 필드 거부 경계 통과).
        assert CrawlTaskRequest.model_validate(payload).model_dump() == request.model_dump()

    @pytest.mark.parametrize(
        ("enable_setting", "build_schedule"),
        [
            pytest.param(
                "KONEPS_COLLECTION_SCHEDULE_ENABLED",
                build_koneps_collection_beat_schedule,
                id="koneps",
            ),
            pytest.param(
                "KONEPS_SCSBID_COLLECTION_SCHEDULE_ENABLED",
                build_scsbid_collection_beat_schedule,
                id="scsbid",
            ),
        ],
    )
    def test_beat_schedule_payloads_round_trip(
        self, monkeypatch, enable_setting: str, build_schedule
    ):
        """두 수집 beat 발신 payload 가 수신 DTO 로 복원되는지(필드 대칭)."""
        monkeypatch.setattr(settings, enable_setting, True)

        entries = build_schedule()
        assert entries, "스케줄이 비어 있으면 대조할 payload 가 없다"

        for entry in entries.values():
            payload = entry["kwargs"]["request_payload"]
            request = CrawlTaskRequest.model_validate(payload)
            assert request.source
            assert request.execution_mode in {"mock", "live", "auto"}


# ---------------------------------------------------------------------------
# jobs.send_telegram_notification
# ---------------------------------------------------------------------------
class _FakeTelegramService:
    """Bot API 대신 호출 인자를 기록한다(발신 없음)."""

    def __init__(self) -> None:
        self.sent: dict | None = None

    def build_message(self, title: str, message: str, url: str | None = None) -> str:
        return f"[{title}] {message}" + (f" {url}" if url else "")

    def send_message(self, message, reply_markup=None, chat_id=None) -> dict:
        self.sent = {
            "message": message,
            "reply_markup": reply_markup,
            "chat_id": chat_id,
        }
        return {"sent": False, "status": "skipped_test_environment", "detail": "ok"}


class TestSendTelegramNotificationBoundary:
    """이 task 는 저장소 내 발신처가 없다(운영자 수동/외부 호출 경로).

    그래서 payload 검증이 유일한 방어선이고, 여기서 그 계약을 고정한다.
    """

    def _patch_service(self, monkeypatch) -> _FakeTelegramService:
        fake = _FakeTelegramService()
        monkeypatch.setattr(jobs, "TelegramNotificationService", lambda: fake)
        return fake

    def test_titled_payload_builds_message_and_forwards_markup(self, monkeypatch):
        fake = self._patch_service(monkeypatch)
        markup = {"inline_keyboard": [[{"text": "✅", "callback_data": "bd:1:submit"}]]}

        result = jobs.send_telegram_notification.run(
            title="낙찰결과", message="본문", url="https://x", chat_id="42",
            reply_markup=markup,
        )

        assert result["status"] == "skipped_test_environment"
        assert fake.sent == {
            "message": "[낙찰결과] 본문 https://x",
            "reply_markup": markup,
            "chat_id": "42",
        }

    def test_message_only_payload_is_sent_verbatim(self, monkeypatch):
        fake = self._patch_service(monkeypatch)

        jobs.send_telegram_notification.run(message="본문만")

        assert fake.sent["message"] == "본문만"
        assert fake.sent["reply_markup"] is None

    def test_malformed_reply_markup_never_reaches_the_bot_api(self, monkeypatch):
        fake = self._patch_service(monkeypatch)

        with pytest.raises(ValidationError):
            jobs.send_telegram_notification.run(
                message="본문", reply_markup={"inline_keyboard": [["not-a-button"]]}
            )

        assert fake.sent is None


# ---------------------------------------------------------------------------
# jobs.backfill_scsbid_reserve_detail
# ---------------------------------------------------------------------------
class TestReserveDetailBackfillBoundary:
    def test_valid_notices_are_promoted_and_deduped_by_the_dto(self, monkeypatch):
        captured: dict = {}

        def _spy(request, *, enqueue_continuation):
            captured["request"] = request
            return {"ok": True}

        monkeypatch.setattr(jobs, "run_scsbid_reserve_detail_backfill_job", _spy)

        jobs.backfill_scsbid_reserve_detail.run(
            notices=[
                {"notice_number": " N-1 ", "category": "construction"},
                {"notice_number": "N-1", "category": "construction"},
            ]
        )

        assert captured["request"].deduped() == [
            ScsbidReserveDetailNotice(notice_number="N-1", category="construction")
        ]

    def test_blank_notice_number_is_rejected_before_any_http(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            jobs,
            "run_scsbid_reserve_detail_backfill_job",
            lambda *a, **k: calls.append("ran") or {},
        )

        with pytest.raises(ValidationError):
            jobs.backfill_scsbid_reserve_detail.run(notices=[{"notice_number": ""}])

        assert calls == []

    def test_crawl_side_enqueue_payload_round_trips(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            jobs.backfill_scsbid_reserve_detail,
            "apply_async",
            _capture_apply_async(captured),
        )

        enqueued = jobs._enqueue_deferred_reserve_detail_backfill(
            [
                {"notice_number": " N-1 ", "category": "construction"},
                {"notice_number": "N-1", "category": "construction"},
                {"notice_number": "", "category": "service"},
                "not-a-dict",
            ]
        )

        assert enqueued == 1
        payload = captured["kwargs"]
        # 발신 payload 는 수신 DTO 로 그대로 복원된다(정규화 결과까지 대칭).
        assert ScsbidReserveDetailBackfillRequest.model_validate(
            payload
        ) == ScsbidReserveDetailBackfillRequest(
            notices=[
                ScsbidReserveDetailNotice(
                    notice_number="N-1", category="construction"
                )
            ]
        )

    def test_self_chain_continuation_payload_round_trips(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            jobs.backfill_scsbid_reserve_detail,
            "apply_async",
            _capture_apply_async(captured),
        )
        rest = ScsbidReserveDetailBackfillRequest(
            notices=[
                ScsbidReserveDetailNotice(notice_number="C-3", category="service"),
                ScsbidReserveDetailNotice(notice_number="C-4", category=""),
            ]
        )

        assert jobs._enqueue_reserve_detail_continuation(rest) is True
        assert (
            ScsbidReserveDetailBackfillRequest.model_validate(captured["kwargs"]) == rest
        )

    def test_empty_continuation_is_not_enqueued(self):
        empty = ScsbidReserveDetailBackfillRequest(notices=[])
        assert jobs._enqueue_reserve_detail_continuation(empty) is False


# ---------------------------------------------------------------------------
# jobs.train_price_predictor
# ---------------------------------------------------------------------------
class _FakeTrainingService:
    def __init__(self) -> None:
        self.request_payload: dict | None = None

    def train_price_predictor(self, db, request_payload=None) -> dict:
        self.request_payload = request_payload
        return {"status": "completed"}


class TestTrainPricePredictorBoundary:
    def test_validated_payload_is_handed_to_the_ml_service_as_a_dump(self, monkeypatch):
        dummy = _DummyDB()
        fake = _FakeTrainingService()
        _patch_task_session(monkeypatch, lambda: dummy)
        monkeypatch.setattr(jobs, "PricePredictionTrainingService", lambda: fake)

        out = jobs.train_price_predictor.run(
            request_payload={"release_tag": "r-1", "limit": 10, "publish_remote": False}
        )

        assert out == {"status": "completed"}
        # 발신자가 보낸 키만 전달된다 — 나머지 옵션 기본값은 ML 서비스가 소유한다.
        assert fake.request_payload == {
            "release_tag": "r-1",
            "limit": 10,
            "publish_remote": False,
        }
        assert dummy.closed is True

    def test_partial_beat_payload_keeps_service_defaults_authoritative(
        self, monkeypatch
    ):
        """주간 학습 beat 는 candidate-only 부분 payload 를 보낸다.

        검증이 기본값을 채워 넣어버리면 ``_training_run_options`` 의 서비스 기본값
        (limit=500 등)이 task 쪽 복제본으로 대체된다. 그래서 보낸 키만 넘긴다.
        """
        monkeypatch.setattr(
            settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_ENABLED", True
        )
        monkeypatch.setattr(
            settings, "PRICE_PREDICTOR_TRAINING_SCHEDULE_CATEGORIES", "construction"
        )
        fake = _FakeTrainingService()
        _patch_task_session(monkeypatch, lambda: _DummyDB())
        monkeypatch.setattr(jobs, "PricePredictionTrainingService", lambda: fake)

        entries = build_price_predictor_training_beat_schedule()
        payloads = [
            entry["kwargs"]["request_payload"] for entry in entries.values()
        ]
        assert {"create_manifest": False, "publish_remote": False} in payloads

        for payload in payloads:
            # 발신 payload 는 수신 DTO 로 복원되고(대칭),
            assert PricePredictionTrainingTaskRequest.model_validate(payload)
            # task 를 통과해도 키가 늘지 않는다.
            jobs.train_price_predictor.run(request_payload=payload)
            assert fake.request_payload == payload

    def test_out_of_range_limit_is_rejected_without_opening_a_session(self, monkeypatch):
        opened: list[str] = []
        fake = _FakeTrainingService()
        _patch_task_session(
            monkeypatch, lambda: opened.append("opened") or _DummyDB()
        )
        monkeypatch.setattr(jobs, "PricePredictionTrainingService", lambda: fake)

        with pytest.raises(ValidationError):
            jobs.train_price_predictor.run(request_payload={"limit": 0})

        assert opened == []
        assert fake.request_payload is None

    def test_api_sender_payload_round_trips(self, monkeypatch):
        from app.api.ml import enqueue_price_predictor_training_job

        captured: dict = {}

        def _fake_enqueue(task, *, kwargs, queue):
            captured.update(kwargs=kwargs, queue=queue)
            return _Handle()

        monkeypatch.setattr(jobs, "_enqueue_ml_task", _fake_enqueue)
        request = PricePredictionTrainingRequest(
            release_tag="r-2", category="software", limit=250
        )

        enqueue_price_predictor_training_job(request)

        payload = captured["kwargs"]["request_payload"]
        assert PricePredictionTrainingRequest.model_validate(payload) == request


# ---------------------------------------------------------------------------
# jobs.run_synthetic_operator_backtest
# ---------------------------------------------------------------------------
class _FakeSyntheticBacktestService:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    def run_for_all(self, db, **kwargs) -> dict:
        self.kwargs = kwargs
        return {"operators": []}


class TestSyntheticBacktestBoundary:
    def test_valid_payload_is_promoted(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            jobs,
            "run_synthetic_operator_backtest_job",
            lambda request: captured.update(request=request) or {"ok": True},
        )

        jobs.run_synthetic_operator_backtest.run(
            payload={"limit": 20, "settle_actions": ["bid_now"]}
        )

        assert captured["request"] == SyntheticOperatorBacktestTaskRequest(
            limit=20, settle_actions=["bid_now"]
        )

    def test_unknown_key_is_rejected_before_the_body_runs(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            jobs,
            "run_synthetic_operator_backtest_job",
            lambda request: calls.append("ran") or {},
        )

        with pytest.raises(ValidationError):
            jobs.run_synthetic_operator_backtest.run(payload={"limt": 20})

        assert calls == []

    def test_ad_hoc_body_forwards_dto_fields_without_coercers(self, monkeypatch):
        import app.services.synthetic_backtest as synthetic_backtest

        dummy = _DummyDB()
        fake = _FakeSyntheticBacktestService()
        monkeypatch.setattr(
            synthetic_backtest, "SyntheticBacktestService", lambda: fake
        )

        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2025-12-31T23:59:59Z",
                "category": "construction",
                "limit": 200,
                "scenario": "base",
                "slugs": ["cn-small-gangwon"],
                "cutoff_hours_before_deadline": 4,
                "history_limit": 60,
                "settle_actions": "bid_now,review",
            }
        )
        # 세션은 D1 seam 의 주입 축(``session_factory=``)으로 넣는다.
        out = backtest_jobs.run_synthetic_operator_backtest_job(
            request, session_factory=lambda: dummy
        )

        assert out == {"operators": []}
        assert fake.kwargs == {
            "start_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "end_at": datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            "category": "construction",
            "limit": 200,
            "scenario": "base",
            "slugs": ["cn-small-gangwon"],
            "cutoff_hours_before_deadline": 4,
            "history_limit": 60,
            "settle_actions": ("bid_now", "review"),
        }
        assert dummy.closed is True

    def test_experiment_run_body_keeps_the_sender_keys(self, monkeypatch):
        import app.services.synthetic_experiment as synthetic_experiment

        captured: dict = {}
        monkeypatch.setattr(
            synthetic_experiment,
            "run_experiment_backtest",
            lambda payload: captured.update(payload=payload) or {"run_id": 9},
        )

        sent = {
            "start_at": "2025-01-01T00:00:00Z",
            "limit": 200,
            "scenario": "base",
            "settle_actions": False,
            "category": None,
            "experiment_id": 3,
            "run_id": 9,
            "slugs": ["cn-mid-gyeonggi"],
        }
        out = backtest_jobs.run_synthetic_operator_backtest_job(
            SyntheticOperatorBacktestTaskRequest.model_validate(sent)
        )

        assert out == {"run_id": 9}
        # 레거시 러너는 dict 계약을 유지하므로 **발신자가 보낸 키만** 그대로 받는다.
        assert captured["payload"].keys() == sent.keys()
        assert captured["payload"]["settle_actions"] is False
        assert _parse_dt(captured["payload"]["start_at"]) == datetime(
            2025, 1, 1, tzinfo=timezone.utc
        )

    @pytest.mark.parametrize("preset_name", sorted(SYNTHETIC_EXPERIMENT_PRESETS))
    def test_experiment_lab_preset_payload_validates(self, preset_name: str):
        """Experiment Lab 발신(저장된 preset params + run 키)이 DTO 와 대칭인지."""
        definition = SYNTHETIC_EXPERIMENT_PRESETS[preset_name]
        payload = {
            **definition["params"],
            "experiment_id": 1,
            "run_id": 2,
            "slugs": list(definition["operator_slugs"]) or None,
        }

        request = SyntheticOperatorBacktestTaskRequest.model_validate(payload)

        assert request.is_experiment_run is True
        assert request.model_dump(mode="json", exclude_unset=True).keys() == payload.keys()

    def test_sample_gap_source_context_is_accepted(self):
        request = SyntheticOperatorBacktestTaskRequest.model_validate(
            {
                "run_id": 5,
                "source_sample_gap_candidate": {"dimension": "category", "key": "goods"},
            }
        )
        assert request.source_sample_gap_candidate == {
            "dimension": "category",
            "key": "goods",
        }


# ---------------------------------------------------------------------------
# jobs.run_forward_paper_bidding
# ---------------------------------------------------------------------------
class _FakePaperBiddingService:
    def __init__(self) -> None:
        self.forward_kwargs: dict | None = None
        self.historical_kwargs: dict | None = None

    def run_forward_paper_bidding(self, db, **kwargs) -> dict:
        self.forward_kwargs = kwargs
        return {"run_id": 1}

    def run_historical_backtest(self, db, **kwargs) -> dict:
        self.historical_kwargs = kwargs
        return {"run_id": 2}


class TestForwardPaperBiddingBoundary:
    def test_dto_fields_are_accepted_service_kwargs(self):
        """``**request.model_dump()`` 스프레드 drift 가드.

        DTO 에 서비스가 모르는 필드가 생기면 task 가 ``TypeError`` 로 죽는다. 필드 추가
        시점에 여기서 잡는다(런타임 스케줄 실행이 아니라).
        """
        parameters = inspect.signature(
            PaperBiddingBacktestService.run_forward_paper_bidding
        ).parameters
        assert set(ForwardPaperBiddingTaskRequest.model_fields) <= set(parameters)

    def test_beat_payload_is_promoted_and_spread_into_the_service(self, monkeypatch):
        dummy = _DummyDB()
        fake = _FakePaperBiddingService()
        _patch_task_session(monkeypatch, lambda: dummy)
        monkeypatch.setattr(jobs, "PaperBiddingBacktestService", lambda: fake)

        out = jobs.run_forward_paper_bidding.run(
            request_payload={
                "category": "construction",
                "limit": 12,
                "scenario": "aggressive",
                "strategy_version": "scheduled-forward-paper",
                "model_version": "current",
                "history_limit": 40,
                "persist": True,
            }
        )

        assert out == {"run_id": 1}
        assert fake.forward_kwargs == {
            "category": "construction",
            "limit": 12,
            "scenario": "aggressive",
            "strategy_version": "scheduled-forward-paper",
            "model_version": "current",
            "history_limit": 40,
            "persist": True,
        }
        assert dummy.closed is True

    def test_empty_payload_uses_schedule_defaults(self, monkeypatch):
        fake = _FakePaperBiddingService()
        _patch_task_session(monkeypatch, lambda: _DummyDB())
        monkeypatch.setattr(jobs, "PaperBiddingBacktestService", lambda: fake)

        jobs.run_forward_paper_bidding.run()

        assert fake.forward_kwargs["strategy_version"] == "scheduled-forward-paper"
        assert fake.forward_kwargs["limit"] == 100
        assert fake.forward_kwargs["persist"] is True

    def test_unknown_scenario_is_rejected_without_running(self, monkeypatch):
        fake = _FakePaperBiddingService()
        _patch_task_session(monkeypatch, lambda: _DummyDB())
        monkeypatch.setattr(jobs, "PaperBiddingBacktestService", lambda: fake)

        with pytest.raises(ValidationError):
            jobs.run_forward_paper_bidding.run(request_payload={"scenario": "moonshot"})

        assert fake.forward_kwargs is None

    def test_beat_schedule_payload_round_trips(self, monkeypatch):
        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT", 12)
        monkeypatch.setattr(
            settings, "PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY", "construction"
        )
        monkeypatch.setattr(
            settings, "PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO", "aggressive"
        )
        monkeypatch.setattr(
            settings, "PAPER_BIDDING_FORWARD_SCHEDULE_HISTORY_LIMIT", 40
        )
        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_PERSIST", False)

        entry = build_paper_bidding_forward_beat_schedule()[
            "paper_bidding_forward_periodic"
        ]
        request = ForwardPaperBiddingTaskRequest.model_validate(
            entry["kwargs"]["request_payload"]
        )

        assert request == ForwardPaperBiddingTaskRequest(
            category="construction",
            limit=12,
            scenario="aggressive",
            strategy_version="scheduled-forward-paper",
            model_version="current",
            history_limit=40,
            persist=False,
        )

    def test_in_process_scheduler_payload_round_trips(self, monkeypatch):
        from app.services.paper_bidding_scheduler import PaperBiddingForwardScheduler

        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_LIMIT", 7)
        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_CATEGORY", "")
        monkeypatch.setattr(settings, "PAPER_BIDDING_FORWARD_SCHEDULE_SCENARIO", "base")

        payload = PaperBiddingForwardScheduler().build_request_payload()
        request = ForwardPaperBiddingTaskRequest.model_validate(payload)

        assert request.limit == 7
        assert request.category is None
        assert request.strategy_version == "scheduled-forward-paper"


# ---------------------------------------------------------------------------
# jobs.run_historical_backtest
# ---------------------------------------------------------------------------
class TestHistoricalBacktestBoundary:
    def test_beat_payload_is_promoted(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(
            jobs,
            "run_historical_backtest_job",
            lambda request: captured.update(request=request) or {"ok": True},
        )

        jobs.run_historical_backtest.run(
            request_payload={"limit": 50, "settle_actions": "bid_now,review"}
        )

        assert captured["request"] == HistoricalBacktestTaskRequest(
            limit=50, settle_actions=["bid_now", "review"]
        )

    def test_unknown_key_is_rejected_before_the_body_runs(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            jobs,
            "run_historical_backtest_job",
            lambda request: calls.append("ran") or {},
        )

        with pytest.raises(ValidationError):
            jobs.run_historical_backtest.run(request_payload={"start_at": "2025-01-01"})

        assert calls == []

    def test_body_derives_the_window_from_lookback_days(self, monkeypatch):
        import app.services.paper_bidding_backtest as paper_bidding_backtest

        dummy = _DummyDB()
        fake = _FakePaperBiddingService()
        monkeypatch.setattr(
            paper_bidding_backtest, "PaperBiddingBacktestService", lambda: fake
        )

        request = HistoricalBacktestTaskRequest.model_validate(
            {
                "category": "",
                "limit": 50,
                "scenario": "base",
                "lookback_days": 14,
                "cutoff_hours_before_deadline": 0,
                "settle_actions": "bid_now,review",
                "persist": False,
            }
        )
        # 세션은 D1 seam 의 주입 축(``session_factory=``)으로 넣는다.
        out = backtest_jobs.run_historical_backtest_job(
            request, session_factory=lambda: dummy
        )

        assert out == {"run_id": 2}
        kwargs = fake.historical_kwargs
        assert kwargs["category"] is None  # 빈 문자열은 필터 없음으로 흘린다.
        assert kwargs["settle_actions"] == ("bid_now", "review")
        assert kwargs["limit"] == 50
        assert kwargs["persist"] is False
        # 의도된 변경: 종전 ``int(x or 2)`` 가 흡수했던 0 이 서비스까지 그대로 간다.
        assert kwargs["cutoff_hours_before_deadline"] == 0
        window = kwargs["end_at"] - kwargs["start_at"]
        assert window == timedelta(days=14)
        assert dummy.closed is True

    def test_body_falls_back_to_the_configured_lookback(self, monkeypatch):
        import app.services.paper_bidding_backtest as paper_bidding_backtest

        fake = _FakePaperBiddingService()
        monkeypatch.setattr(
            paper_bidding_backtest, "PaperBiddingBacktestService", lambda: fake
        )
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_LOOKBACK_DAYS", 21)

        backtest_jobs.run_historical_backtest_job(
            HistoricalBacktestTaskRequest(), session_factory=_DummyDB
        )

        assert fake.historical_kwargs["end_at"] - fake.historical_kwargs[
            "start_at"
        ] == timedelta(days=21)

    def test_beat_schedule_payload_round_trips(self, monkeypatch):
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_ENABLED", True)
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_LOOKBACK_DAYS", 14)
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_LIMIT", 50)
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_CATEGORY", "")
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_SCENARIO", "base")
        monkeypatch.setattr(
            settings, "HISTORICAL_BACKTEST_SCHEDULE_HISTORY_LIMIT", 80
        )
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_CUTOFF_HOURS", 2)
        monkeypatch.setattr(
            settings, "HISTORICAL_BACKTEST_SCHEDULE_SETTLE_ACTIONS", "bid_now,review"
        )
        monkeypatch.setattr(settings, "HISTORICAL_BACKTEST_SCHEDULE_PERSIST", True)

        entry = build_historical_backtest_beat_schedule()[
            "historical_backtest_periodic"
        ]
        request = HistoricalBacktestTaskRequest.model_validate(
            entry["kwargs"]["request_payload"]
        )

        assert request == HistoricalBacktestTaskRequest(
            category=None,
            limit=50,
            scenario="base",
            strategy_version="scheduled-historical-backtest",
            model_version="current",
            cutoff_hours_before_deadline=2,
            history_limit=80,
            settle_actions=["bid_now", "review"],
            persist=True,
            lookback_days=14,
        )
