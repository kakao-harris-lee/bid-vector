"""낙찰 가능성(probability) 보정 — 학습/적용/누수 가드/라벨 정직화 회귀.

PR5 item 1: 보정 안 된 휴리스틱 P(낙찰) → 정산 결과(would_have_won_final)로
보정한 값으로 교체. 표시 라벨은 "가격 적합도(추정)"으로 정직화.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.ai.predictors.historical import (
    apply_probability_calibration,
    calibration_raw_signal,
    load_probability_calibration,
)
from app.models.models import (
    PaperBid,
    PaperBidRun,
    PaperBidSettlement,
    Project,
    User,
)
from app.services.ml_training import PricePredictionTrainingService
from app.services.paper_bidding_backtest import PaperBiddingBacktestService
from app.services.prediction_dataset import PredictionDatasetService


# --------------------------------------------------------------------------- #
# A. 보정 모델 학습 (ml_training._build_probability_calibration)
# --------------------------------------------------------------------------- #
def _calibration_item(
    *,
    confidence: float,
    matched: float,
    history: int,
    business_group: str,
    label,
    price_close: int = 0,
    final: str = "eligible_favorable",
    price_only: str = "plausible",
):
    return {
        "paper_bid_id": 1,
        "project_id": 1,
        "features": {
            "confidence_score": confidence,
            "matched_score": matched,
            "historical_sample_size": history,
            "business_group": business_group,
            "category": business_group,
        },
        "label": label,
        "would_have_won_final": final,
        "price_close_label": price_close,
        "would_have_won_price_only": price_only,
    }


def test_build_probability_calibration_emits_signed_summary_block():
    """학습이 summary.probability_calibration 블록을 산출한다(서명 채널 동일)."""
    svc = PricePredictionTrainingService()
    items = []
    # construction: 강한 신호 -> 승, 약한 신호 -> 패 (2-class identifiable)
    for _ in range(8):
        items.append(
            _calibration_item(
                confidence=0.9,
                matched=0.9,
                history=20,
                business_group="construction",
                label=1,
                final="eligible_favorable",
            )
        )
    for _ in range(8):
        items.append(
            _calibration_item(
                confidence=0.3,
                matched=0.2,
                history=1,
                business_group="construction",
                label=0,
                final="eligible_but_outbid",
                price_only="unlikely",
            )
        )
    dataset = {"items": items}
    calibration = svc._build_probability_calibration(dataset)
    assert "construction" in calibration
    assert "__global__" in calibration
    curve = calibration["construction"]
    assert curve["method"] in {"platt", "base_rate"}
    assert curve["label_source"] == "eligible_favorable"
    assert curve["sample_count"] == 16
    assert curve["positive_count"] == 8


def test_calibration_excludes_unknown_and_gates_on_eligible_favorable():
    """unknown 라벨은 학습 제외, eligible_favorable만 양성(1)으로 간주한다."""
    svc = PricePredictionTrainingService()
    items = [
        # unknown -> label None -> 제외되어야 함
        _calibration_item(
            confidence=0.95,
            matched=0.95,
            history=30,
            business_group="service",
            label=None,
            final="unknown",
        ),
        _calibration_item(
            confidence=0.9,
            matched=0.9,
            history=25,
            business_group="service",
            label=1,
            final="eligible_favorable",
        ),
        _calibration_item(
            confidence=0.2,
            matched=0.1,
            history=0,
            business_group="service",
            label=0,
            final="disqualified",
            price_only="unlikely",
        ),
    ]
    calibration = svc._build_probability_calibration({"items": items})
    service_curve = calibration["service"]
    # unknown 1건이 빠지고 2건만 라벨링됨
    assert service_curve["sample_count"] == 2
    assert service_curve["positive_count"] == 1
    assert service_curve["label_source"] == "eligible_favorable"


def test_calibration_falls_back_to_price_close_when_single_class_eligibility():
    """적격성 라벨이 단일 클래스면 price_close 폴백 라벨을 쓴다(폴백 명시)."""
    svc = PricePredictionTrainingService()
    items = [
        # 모두 eligible_favorable(=1) 단일 클래스 -> 적격성 라벨로는 곡선 불가
        _calibration_item(
            confidence=0.9,
            matched=0.9,
            history=20,
            business_group="goods",
            label=1,
            final="eligible_favorable",
            price_close=1,
            price_only="plausible",
        ),
        _calibration_item(
            confidence=0.4,
            matched=0.3,
            history=2,
            business_group="goods",
            label=1,
            final="eligible_favorable",
            price_close=0,
            price_only="unlikely",
        ),
    ]
    calibration = svc._build_probability_calibration({"items": items})
    goods_curve = calibration["goods"]
    assert goods_curve["label_source"] == "price_close"


def test_inject_probability_calibration_into_ensemble_artifact():
    """summary.probability_calibration이 in-memory 아티팩트에 주입된다(disk 전)."""
    svc = PricePredictionTrainingService()
    artifact = {"artifact_version": 1, "model_version": "test"}
    block = {"construction": {"method": "platt", "scale": 2.0, "bias": -1.0}}
    svc._inject_summary_block(
        artifact, key="probability_calibration", block=block
    )
    assert artifact["summary"]["probability_calibration"]["construction"]["scale"] == 2.0
    # 빈 블록은 no-op
    artifact2 = {"artifact_version": 1}
    svc._inject_summary_block(artifact2, key="probability_calibration", block={})
    assert "summary" not in artifact2


# --------------------------------------------------------------------------- #
# B. 보정 적용 (historical.apply_probability_calibration)
# --------------------------------------------------------------------------- #
def test_apply_probability_calibration_returns_none_without_artifact():
    """보정 아티팩트가 없으면 None을 돌려 휴리스틱 폴백을 유도한다."""
    assert load_probability_calibration() == {}
    result = apply_probability_calibration(
        {
            "confidence_score": 0.8,
            "matched_score": 0.7,
            "historical_sample_size": 10,
            "business_group": "construction",
        }
    )
    assert result is None


def test_apply_probability_calibration_uses_group_then_global_curve():
    """그룹 곡선 우선, 없으면 __global__ 폴백을 적용한다."""
    calibration = {
        "__global__": {"method": "platt", "scale": 0.0, "bias": 0.0},
        "construction": {"method": "platt", "scale": 0.0, "bias": 4.0},
    }
    # bias=4 -> sigmoid(4) ~ 0.98
    high = apply_probability_calibration(
        {
            "confidence_score": 0.5,
            "matched_score": 0.5,
            "historical_sample_size": 5,
            "business_group": "construction",
        },
        calibration=calibration,
    )
    assert high >= 0.95
    # unknown group -> __global__ (bias=0, scale=0) -> sigmoid(0) = 0.5
    mid = apply_probability_calibration(
        {
            "confidence_score": 0.5,
            "matched_score": 0.5,
            "historical_sample_size": 5,
            "business_group": "unknown-group",
        },
        calibration=calibration,
    )
    assert abs(mid - 0.5) < 1e-9


# --------------------------------------------------------------------------- #
# C. 백테스트 경로 (_estimate_probability_score) — 보정 있으면 보정값, 없으면 휴리스틱
# --------------------------------------------------------------------------- #
def test_estimate_probability_score_falls_back_to_heuristic(monkeypatch):
    """보정 아티팩트가 없으면 기존 휴리스틱 값을 그대로 산출(무크래시)."""
    svc = PaperBiddingBacktestService()
    prediction = {"confidence_score": 0.5}
    score = svc._estimate_probability_score(
        matched_score=0.5,
        prediction=prediction,
        history_count=30,
        business_group="construction",
    )
    # 휴리스틱: 0.5*0.38 + 0.5*0.42 + 1.0*0.20 = 0.6
    assert score == 0.6


def test_estimate_probability_score_uses_calibration_when_present(monkeypatch):
    """보정 아티팩트가 있으면 보정값을 산출한다."""
    # ``_estimate_probability_score`` moved to the ``scoring`` submodule when
    # ``paper_bidding_backtest`` was decomposed into a package; patch the symbol
    # where it is now resolved (assertion unchanged).
    import app.services.paper_bidding_backtest.scoring as backtest_mod

    def fake_apply(features, **kwargs):
        return 0.91

    monkeypatch.setattr(backtest_mod, "apply_probability_calibration", fake_apply)
    svc = PaperBiddingBacktestService()
    score = svc._estimate_probability_score(
        matched_score=0.5,
        prediction={"confidence_score": 0.5},
        history_count=30,
        business_group="construction",
    )
    assert score == 0.91


# --------------------------------------------------------------------------- #
# D. 누수 가드 — settled 라벨은 feature로 절대 들어가지 않는다
# --------------------------------------------------------------------------- #
def test_calibration_dataset_does_not_leak_settled_labels_into_features(test_db):
    """build_probability_calibration_dataset의 features에 settled 라벨이 없어야 한다."""
    user = User(
        username="calib-operator",
        email="calib@example.com",
        hashed_password="x",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()

    project = Project(
        title="calib project",
        category="construction",
        budget_estimate=100_000_000.0,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()

    run = PaperBidRun(operator_id=user.id, status="completed")
    test_db.add(run)
    test_db.flush()

    paper_bid = PaperBid(
        run_id=run.id,
        project_id=project.id,
        operator_id=user.id,
        action="bid_now",
        decision_status="planned",
        confidence_score=0.77,
        matched_score=0.66,
        probability_score=0.6,
        predicted_price=90_000_000.0,
    )
    test_db.add(paper_bid)
    test_db.flush()

    settlement = PaperBidSettlement(
        paper_bid_id=paper_bid.id,
        result_status="settled",
        would_have_won_final="eligible_favorable",
        would_have_won_price_only="plausible",
        estimated_price=95_000_000.0,
        minimum_bid_price=88_000_000.0,
    )
    test_db.add(settlement)
    test_db.flush()

    dataset = PredictionDatasetService().build_probability_calibration_dataset(test_db)
    assert dataset["summary"]["sample_count"] == 1
    row = dataset["items"][0]
    features = row["features"]
    # 누수 가드: settled 결과(라벨)는 features에 절대 없어야 함
    leaky_keys = {
        "would_have_won_final",
        "would_have_won_price_only",
        "estimated_price",
        "minimum_bid_price",
        "winning_amount",
        "winning_rate",
        "label",
        "price_close_label",
    }
    assert not (set(features.keys()) & leaky_keys)
    # features는 추론 시점 신호만 — historical_sample_size는 train/serve 스큐를
    # 막기 위해 의도적으로 제외(PaperBid에 미저장 → 항상 0).
    assert set(features.keys()) == {
        "confidence_score",
        "matched_score",
        "business_group",
        "category",
    }
    assert "historical_sample_size" not in features
    assert features["confidence_score"] == 0.77
    assert features["matched_score"] == 0.66
    # 라벨은 별도 키로만 존재
    assert row["label"] == 1
    assert row["would_have_won_final"] == "eligible_favorable"


def test_calibration_dataset_excludes_unknown_from_primary_label(test_db):
    """unknown settlement은 primary label=None으로 학습에서 분리된다."""
    user = User(
        username="calib-operator2",
        email="calib2@example.com",
        hashed_password="x",
        is_active=True,
    )
    test_db.add(user)
    test_db.flush()
    project = Project(
        title="calib project 2",
        category="service",
        budget_estimate=50_000_000.0,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    test_db.add(project)
    test_db.flush()
    run = PaperBidRun(operator_id=user.id, status="completed")
    test_db.add(run)
    test_db.flush()
    paper_bid = PaperBid(
        run_id=run.id,
        project_id=project.id,
        operator_id=user.id,
        action="review",
        decision_status="reviewing",
        confidence_score=0.5,
        matched_score=0.5,
    )
    test_db.add(paper_bid)
    test_db.flush()
    test_db.add(
        PaperBidSettlement(
            paper_bid_id=paper_bid.id,
            result_status="pending",
            would_have_won_final="unknown",
            would_have_won_price_only="unknown",
        )
    )
    test_db.flush()
    dataset = PredictionDatasetService().build_probability_calibration_dataset(test_db)
    assert dataset["summary"]["unknown_excluded_count"] == 1
    assert dataset["items"][0]["label"] is None


# --------------------------------------------------------------------------- #
# E. 라벨 정직화 — 표시 문자열이 "가격 적합도(추정)"으로 바뀐다
# --------------------------------------------------------------------------- #
def test_telegram_label_is_honest():
    """텔레그램 메시지가 '낙찰 가능성' 대신 '가격 적합도(추정)'을 쓴다."""
    from app.services.notifications.telegram import TelegramNotificationService

    svc = TelegramNotificationService()
    message = svc.build_bid_decision_message(
        project_title="테스트 공고",
        project_id=1,
        action="bid_now",
        decision_status="planned",
        priority_score=0.8,
        recommended_amount=90_000_000,
        probability_score=0.6,
        reasoning="근거",
    )
    assert "가격 적합도(추정)" in message
    assert "낙찰 가능성:" not in message


def test_allocation_reasoning_label_is_honest():
    """allocation reasoning 문구가 '가격 적합도(추정)'을 쓴다."""
    import inspect

    from app.services import allocation, allocation_core

    # The reason assembly was extracted into the pure allocation_core (PR-16), so
    # the honesty-spec label now lives there; check both the service and its core.
    source = inspect.getsource(allocation) + inspect.getsource(allocation_core)
    assert "가격 적합도(추정) 점수" in source
    assert "낙찰 가능성 점수" not in source


def test_ko_json_probability_label_is_honest():
    """프론트 ko.json probability_label이 '가격 적합도(추정)'으로 정직화됨."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    ko_path = repo_root / "frontend" / "src" / "shared" / "i18n" / "ko.json"
    data = json.loads(ko_path.read_text(encoding="utf-8"))
    assert data["decision_reasons"]["probability_label"] == "가격 적합도(추정)"


def test_schema_probability_score_has_honest_description():
    """schema probability_score Field에 P(낙찰) 아님 설명이 붙는다."""
    from app.schemas.schemas import BidDecisionResponse, OpportunityAnalysisResponse

    for model in (BidDecisionResponse, OpportunityAnalysisResponse):
        field = model.model_fields["probability_score"]
        assert field.description is not None
        assert "P(낙찰) 아님" in field.description


# --------------------------------------------------------------------------- #
# F. train/serve raw 정합 — 학습·추론·백테스트가 바이트 동일 raw를 쓴다
# --------------------------------------------------------------------------- #
def test_calibration_raw_signal_identical_across_train_serve_backtest():
    """동일 (confidence, matched)에서 학습 raw == 추론 raw == 백테스트 raw.

    이 단언은 PR #92 리뷰가 지적한 train/serve 스큐(history 항이 학습엔 0,
    추론엔 >0)를 회귀로 잡는다. history는 raw에 더 이상 기여하지 않아야 한다.
    """
    confidence, matched = 0.73, 0.61
    canonical = calibration_raw_signal(confidence, matched)

    # 학습 경로 (ml_training._raw_probability_signal)
    train_raw = PricePredictionTrainingService._raw_probability_signal(
        {"confidence_score": confidence, "matched_score": matched}
    )
    assert train_raw == canonical

    # history를 넣어도(학습은 미저장으로 0, 추론은 >0) raw가 변하지 않아야 함
    train_raw_with_history = PricePredictionTrainingService._raw_probability_signal(
        {
            "confidence_score": confidence,
            "matched_score": matched,
            "historical_sample_size": 30,
        }
    )
    assert train_raw_with_history == canonical

    # 추론·백테스트 경로는 동일 curve(scale=1,bias=0)에서 raw에만 의존하므로,
    # 동일 raw면 보정 결과도 동일해야 한다. history 유무로 결과가 갈리면 스큐.
    identity_curve = {"__global__": {"method": "platt", "scale": 1.0, "bias": 0.0}}
    serve_no_history = apply_probability_calibration(
        {
            "confidence_score": confidence,
            "matched_score": matched,
            "business_group": "construction",
        },
        calibration=identity_curve,
    )
    serve_with_history = apply_probability_calibration(
        {
            "confidence_score": confidence,
            "matched_score": matched,
            "historical_sample_size": 30,
            "business_group": "construction",
        },
        calibration=identity_curve,
    )
    assert serve_no_history == serve_with_history
    # 그리고 그 raw가 canonical raw와 일치(sigmoid(raw)로 환산해 확인)
    import math

    expected = round(1.0 / (1.0 + math.exp(-canonical)), 2)
    assert serve_no_history == expected


def test_calibration_raw_signal_renormalized_weights_sum_to_one():
    """confidence/matched만으로 재정규화된 가중치 합이 1(둘 다 1이면 raw≈1)."""
    assert abs(calibration_raw_signal(1.0, 1.0) - 1.0) < 1e-9
    assert calibration_raw_signal(0.0, 0.0) == 0.0
    # confidence 가중치(0.525) > matched 가중치(0.475)
    only_confidence = calibration_raw_signal(1.0, 0.0)
    only_matched = calibration_raw_signal(0.0, 1.0)
    assert only_confidence > only_matched
    assert abs((only_confidence + only_matched) - 1.0) < 1e-9


# --------------------------------------------------------------------------- #
# G. Platt 단조성 + base_rate 단일클래스 곡선
# --------------------------------------------------------------------------- #
def test_platt_curve_is_monotonic_for_separable_two_class():
    """분리 가능 2-class 입력에서 high-raw의 p > low-raw의 p (scale>0)."""
    svc = PricePredictionTrainingService()
    items = []
    # 강한 신호 -> 승(1), 약한 신호 -> 패(0)
    for _ in range(12):
        items.append(
            _calibration_item(
                confidence=0.95,
                matched=0.95,
                history=0,
                business_group="construction",
                label=1,
                final="eligible_favorable",
            )
        )
    for _ in range(12):
        items.append(
            _calibration_item(
                confidence=0.1,
                matched=0.1,
                history=0,
                business_group="construction",
                label=0,
                final="eligible_but_outbid",
                price_only="unlikely",
            )
        )
    calibration = svc._build_probability_calibration({"items": items})
    curve = calibration["construction"]
    assert curve["method"] == "platt"
    assert curve["scale"] > 0  # 단조 증가

    table = {"construction": curve}
    high = apply_probability_calibration(
        {"confidence_score": 0.95, "matched_score": 0.95, "business_group": "construction"},
        calibration=table,
    )
    low = apply_probability_calibration(
        {"confidence_score": 0.1, "matched_score": 0.1, "business_group": "construction"},
        calibration=table,
    )
    assert high > low


def test_base_rate_curve_for_single_class_group():
    """단일 클래스(폴백도 단일) 그룹은 flat base_rate 곡선(scale=0, bias=logit(base))."""
    import math

    svc = PricePredictionTrainingService()
    # 모두 양성(1) + price_close도 모두 1 -> 어떤 라벨로도 2-class 불가 -> base_rate
    rows = [(0.9, 1, 1), (0.4, 1, 1), (0.6, 1, 1)]
    curve = svc._fit_group_probability_curve(rows)
    assert curve["method"] == "base_rate"
    assert curve["scale"] == 0.0
    assert curve["base_rate"] == 1.0
    # logit(base_rate) with clamp -> bias가 logit(1-1e-6)에 근접(매우 큰 양수)
    assert curve["bias"] == round(math.log((1 - 1e-6) / 1e-6), 6)
    # 적용 시 raw와 무관하게 base_rate(≈1.0)을 반환
    applied = apply_probability_calibration(
        {"confidence_score": 0.1, "matched_score": 0.1, "business_group": "g"},
        calibration={"g": curve},
    )
    assert applied >= 0.99


# --------------------------------------------------------------------------- #
# H. 실시간 경로(opportunity_analysis) 보정 적용 + non-matched 0.49 클램프 유지
# --------------------------------------------------------------------------- #
def test_opportunity_analysis_applies_calibration_and_keeps_non_matched_clamp(
    monkeypatch,
):
    """opportunity_analysis: 보정값 적용하되 non-matched 시 0.49 클램프 유지."""
    import app.services.opportunity_analysis as oa_mod

    # 보정 곡선이 0.92를 반환하도록 monkeypatch
    monkeypatch.setattr(
        oa_mod, "apply_probability_calibration", lambda features, **kw: 0.92
    )

    service = oa_mod.OpportunityAnalysisService()

    # (a) matched=True -> 보정값 0.92가 그대로 살아남는다(override=0 가정)
    matched_classification = {"score": 0.8, "matched": True}
    prob_matched = service._apply_calibrated_or_heuristic_probability(
        heuristic_probability=0.6,
        classification=matched_classification,
        price_prediction={"confidence_score": 0.7},
        business_group="construction",
    )
    assert prob_matched == 0.92

    # (b) matched=False -> 보정값이라도 0.49로 클램프되어야 한다
    non_matched_classification = {"score": 0.8, "matched": False}
    prob_non_matched = service._apply_calibrated_or_heuristic_probability(
        heuristic_probability=0.6,
        classification=non_matched_classification,
        price_prediction={"confidence_score": 0.7},
        business_group="construction",
    )
    assert prob_non_matched <= 0.49
