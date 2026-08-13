"""낙찰률 GBM predictor — 가용성 게이트 · 계약 · guardrail 뮤테이션 증명 · 승격 차단.

guardrail red line 은 "결과 ≥ floor" 단언만으로는 **구조적으로 실패할 수 없다**
(#360 교훈: guardrail 이 항상 끌어올리므로). 그래서 여기서도 (a) 원시 predictor 산출이
하한 **미만**임을 먼저 단언해 시나리오가 실제로 guardrail 을 발동시킴을 증명하고,
(b) guardrail 우회 뮤턴트(단계 무력화)에서는 red line 이 **실패함**을 같은 테스트가 보인다.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.ai.predictors.artifact_contracts import PersistedAwardRateGbmArtifact
from app.ai.predictors.award_rate_gbm import (
    AwardRateGbmPredictor,
    load_award_rate_gbm_model,
)
from app.ai.predictors.base import PredictionResult, PricePredictionContext
from app.ai.predictors.registry import build_default_predictor_registry
from app.ai.price_prediction import orchestration as prediction_orchestration
from app.ai.price_prediction import predict_price
from app.core.config import settings
from app.core.constants import AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
from app.models.models import HistoricalData
from app.services.ml_training.award_rate_gbm import (
    AwardRateTrainingRow,
    train_award_rate_gbm,
)

_BASE_AMOUNT = 100_000_000.0
_LEGAL_FLOOR = 0.88

# 학습 픽스처: 기관마다 뚜렷하게 다른 낙찰률을 주어 모델이 기관 축을 배우게 한다.
# 낮은 쪽(0.72)은 법정하한 0.88 보다 명백히 아래라 guardrail 시나리오가 성립한다.
_LOW_RATE = 0.72
_HIGH_RATE = 0.97


def _training_rows(count: int = 400) -> list[AwardRateTrainingRow]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows: list[AwardRateTrainingRow] = []
    for index in range(count):
        low = index % 2 == 0
        rows.append(
            AwardRateTrainingRow(
                value=(_LOW_RATE if low else _HIGH_RATE) + (0.001 * (index % 5)),
                amount=_BASE_AMOUNT,
                category="construction" if low else "service",
                agency="저가기관" if low else "고가기관",
                denominator_source="clean-base",
                opened_at=start + timedelta(hours=index),
                # 진단 축(피처 아님) — 명시적으로 채운다. 기본값이 없는 이유는
                # "안 넘겼다"와 "미공시"가 다른 상태이기 때문이다.
                published_floor_rate=0.88 if index % 3 else None,
            )
        )
    return rows


@pytest.fixture
def artifact_path(tmp_path):
    """실제로 학습한 아티팩트를 디스크에 남기고 그 경로를 준다."""
    artifact = train_award_rate_gbm(_training_rows(), folds=3)
    path = tmp_path / "award-rate-gbm.json"
    path.write_text(
        PersistedAwardRateGbmArtifact.model_validate(artifact).model_dump_json(),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def gbm_enabled(monkeypatch, artifact_path):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH", str(artifact_path)
    )
    return artifact_path


def _context(
    *, category: str = "construction", agency_name: str | None = "저가기관"
) -> PricePredictionContext:
    return PricePredictionContext(
        budget=_BASE_AMOUNT,
        category=category,
        description="항만 준설 공사",
        historical_records=(),
        agency_name=agency_name,
    )


# --------------------------------------------------------------------------
# 가용성 게이트
# --------------------------------------------------------------------------


def test_availability_requires_experimental_flag(monkeypatch, artifact_path):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", False)
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH", str(artifact_path)
    )
    availability = AwardRateGbmPredictor().check_availability(_context())
    assert not availability.available
    assert "Experimental" in availability.reason


def test_availability_requires_a_configured_artifact(monkeypatch):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH", "")
    availability = AwardRateGbmPredictor().check_availability(_context())
    assert not availability.available
    assert "No award-rate GBM model artifact" in availability.reason


def test_availability_reports_a_missing_artifact_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(
        settings,
        "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH",
        str(tmp_path / "missing.json"),
    )
    availability = AwardRateGbmPredictor().check_availability(_context())
    assert not availability.available
    assert "was not found" in availability.reason


def test_availability_requires_a_positive_budget(gbm_enabled):
    availability = AwardRateGbmPredictor().check_availability(
        PricePredictionContext(
            budget=0.0, category="construction", description="", historical_records=()
        )
    )
    assert not availability.available
    assert "positive budget" in availability.reason


# --------------------------------------------------------------------------
# 배우지 않은 공종 가드 — 어휘 부재 + 얕은 표본
# --------------------------------------------------------------------------


_THIN_CATEGORY = "general"
_THIN_CATEGORY_ROWS = 6


def _thin_category_artifact(tmp_path):
    """construction 은 두텁고 ``general`` 은 6행뿐인 아티팩트(라이브 구성의 축소판)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        AwardRateTrainingRow(
            value=_LOW_RATE + (0.001 * (index % 5)),
            amount=_BASE_AMOUNT,
            category="construction",
            agency="저가기관",
            denominator_source="clean-base",
            opened_at=start + timedelta(hours=index),
            published_floor_rate=None,
        )
        for index in range(200)
    ] + [
        AwardRateTrainingRow(
            value=_HIGH_RATE,
            amount=_BASE_AMOUNT,
            category=_THIN_CATEGORY,
            agency="얕은기관",
            denominator_source="clean-base",
            opened_at=start + timedelta(hours=500 + index),
            published_floor_rate=None,
        )
        for index in range(_THIN_CATEGORY_ROWS)
    ]
    path = tmp_path / "thin-category.json"
    path.write_text(
        PersistedAwardRateGbmArtifact.model_validate(
            train_award_rate_gbm(rows, folds=3)
        ).model_dump_json(),
        encoding="utf-8",
    )
    return path


def test_category_training_rows_counts_the_artifact_vocabulary(tmp_path):
    """판정 근거는 아티팩트에서 유도된다 — 새 계약 필드 없이 정확한 행 수가 나온다."""
    model = load_award_rate_gbm_model(str(_thin_category_artifact(tmp_path)))
    assert model.category_training_rows("construction") == 200
    assert model.category_training_rows(_THIN_CATEGORY) == _THIN_CATEGORY_ROWS
    assert model.category_training_rows("goods") == 0
    assert model.category_training_rows(None) == 0


def test_unseen_category_makes_the_predictor_unavailable(gbm_enabled):
    """학습 코퍼스에 없던 공종(goods)에는 답하지 않는다 — historical 로 폴백한다."""
    availability = AwardRateGbmPredictor().check_availability(_context(category="goods"))
    assert not availability.available
    assert "never trained on category 'goods'" in availability.reason
    # 무엇이 없어서 못 냈는지가 사유에 남는다(운영자 정직성).
    assert "other categories' rows" in availability.reason


def test_learned_category_stays_available(gbm_enabled):
    """가드가 정상 공종까지 막지 않는다 — 대조군이 없으면 위 단언은 무의미하다."""
    availability = AwardRateGbmPredictor().check_availability(
        _context(category="construction")
    )
    assert availability.available
    assert availability.reason is None


def test_thin_category_is_refused_with_its_own_reason(tmp_path, monkeypatch):
    """표본이 임계 미만인 공종(general 6행)도 막고, 사유가 미학습과 구분된다."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(
        settings,
        "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH",
        str(_thin_category_artifact(tmp_path)),
    )
    availability = AwardRateGbmPredictor().check_availability(
        _context(category=_THIN_CATEGORY, agency_name="얕은기관")
    )
    assert not availability.available
    assert f"only {_THIN_CATEGORY_ROWS} training row(s)" in availability.reason
    assert "never trained" not in availability.reason


def test_unlearned_category_refuses_on_the_direct_predict_path_too(gbm_enabled):
    """게이트를 지나지 않는 직접 호출도 막힌다 — 판정이 한 지점이라 둘이 갈리지 않는다."""
    with pytest.raises(ValueError, match="never trained on category 'goods'"):
        AwardRateGbmPredictor().predict(_context(category="goods"))


def test_unseen_category_falls_back_to_historical_through_predict_price(
    gbm_enabled, monkeypatch
):
    """파이프라인 통합: 선호를 켜도 goods 공고는 historical 이 답하고 사유가 남는다."""
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "award_rate_gbm"
    )
    prediction = predict_price(
        budget=_BASE_AMOUNT, category="goods", description="물품 구매", agency_name="시청"
    )
    assert prediction["predictor_name"] != "award_rate_gbm"
    assert "never trained on category 'goods'" in str(prediction["fallback_reason"])


def test_unlearned_category_guard_mutant_proof(gbm_enabled, monkeypatch):
    """가드를 무력화하면 모델이 **본 적 없는 goods 에 투찰율을 낸다** — 가드가 실체다.

    이 단언이 없으면 위 거부 테스트들은 "원래 못 내는 것"을 재는 것일 수도 있다. 여기서
    판정 근거만 뒤집어(공종 행 수를 크게) 예측이 실제로 나온다는 것을 보이면, 거부가
    가드 때문이었음이 증명된다.
    """
    from app.domain.award_rate_features import AwardRateFeatureSpace

    monkeypatch.setattr(
        AwardRateFeatureSpace, "category_training_rows", lambda self, category: 10_000
    )
    availability = AwardRateGbmPredictor().check_availability(_context(category="goods"))
    assert availability.available

    mutant = AwardRateGbmPredictor().predict(_context(category="goods"))
    # 배운 적 없는 공종인데 그럴듯한 투찰율이 나온다 — 이것이 가드가 막는 사고다.
    assert 0.5 < mutant.predicted_bid_rate < 1.0
    print(
        f"\n[MUTANT] guard disabled -> goods available={availability.available} "
        f"predicted_bid_rate={mutant.predicted_bid_rate:.5f} "
        f"pricing_mode={mutant.pricing_mode}"
    )


def test_artifact_with_a_different_feature_set_is_rejected(tmp_path):
    """다른 피처 공간으로 학습된 부스터는 예외 없이 좌표만 어긋난다 — fail-closed."""
    artifact = train_award_rate_gbm(_training_rows(), folds=3)
    artifact["feature_names"] = ["category", "log_amount"]
    path = tmp_path / "drifted.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="different feature set"):
        load_award_rate_gbm_model(str(path))


def test_artifact_missing_a_required_field_is_rejected(tmp_path):
    artifact = train_award_rate_gbm(_training_rows(), folds=3)
    del artifact["global_mean"]
    path = tmp_path / "incomplete.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact contract"):
        load_award_rate_gbm_model(str(path))


# --------------------------------------------------------------------------
# 예측 계약
# --------------------------------------------------------------------------


def test_predict_maps_the_model_onto_the_contract(gbm_enabled):
    result = AwardRateGbmPredictor().predict(_context(agency_name="고가기관", category="service"))

    assert isinstance(result, PredictionResult)
    assert result.pricing_mode == "award_rate_gbm"
    assert result.model_version == "v1.0-award-rate-gbm"
    assert result.predicted_bid_rate == pytest.approx(_HIGH_RATE, abs=0.02)
    labels = [candidate["label"] for candidate in result.bid_rate_candidates]
    assert labels == ["conservative", "base", "aggressive"]
    rates = [candidate["bid_rate"] for candidate in result.bid_rate_candidates]
    assert rates[0] < rates[1] < rates[2]
    assert result.price_range_min == min(
        candidate["predicted_price"] for candidate in result.bid_rate_candidates
    )
    assert result.price_range_max == max(
        candidate["predicted_price"] for candidate in result.bid_rate_candidates
    )
    assert result.guardrail_applied is False
    # 예비가격 패턴은 이 모델의 입력이 아니므로 근거로 싣지 않는다.
    assert result.reserve_price_context is None


def test_reported_sample_size_is_the_training_corpus_not_the_request_history(gbm_enabled):
    """소비하지 않은 표본을 보고하면 그 값이 영속화돼 정확도 리포트까지 간다(L4-2)."""
    context = PricePredictionContext(
        budget=_BASE_AMOUNT,
        category="construction",
        description="",
        historical_records=tuple({"bid_rate": 0.9} for _ in range(7)),
        agency_name="저가기관",
    )
    result = AwardRateGbmPredictor().predict(context)
    assert result.historical_sample_size == len(_training_rows())
    assert result.historical_sample_size != context.historical_sample_size
    assert result.agency_match_sample_size > 0


def test_unknown_agency_reports_zero_agency_samples(gbm_enabled):
    result = AwardRateGbmPredictor().predict(_context(agency_name="처음보는기관"))
    assert result.agency_match_sample_size == 0
    assert "학습 표본에 없어" in result.explanation


def test_predict_is_deterministic(gbm_enabled):
    predictor = AwardRateGbmPredictor()
    assert predictor.predict(_context()) == predictor.predict(_context())


def test_agency_axis_separates_predictions(gbm_enabled):
    predictor = AwardRateGbmPredictor()
    low = predictor.predict(_context(agency_name="저가기관", category="construction"))
    high = predictor.predict(_context(agency_name="고가기관", category="service"))
    assert low.predicted_bid_rate < high.predicted_bid_rate


def test_cached_model_is_shared_instead_of_deep_copied(gbm_enabled):
    """부스터 deepcopy 는 모델 텍스트 재파싱이라 캐시를 무의미하게 만든다."""
    first = load_award_rate_gbm_model(str(gbm_enabled))
    second = load_award_rate_gbm_model(str(gbm_enabled))
    assert first is second


# --------------------------------------------------------------------------
# 파이프라인 통합 · guardrail · 승격 차단
# --------------------------------------------------------------------------


def test_default_registry_exposes_the_gbm_predictor():
    registry = build_default_predictor_registry()
    assert isinstance(registry["award_rate_gbm"], AwardRateGbmPredictor)


def test_predict_price_selects_the_gbm_predictor(gbm_enabled, monkeypatch):
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "award_rate_gbm"
    )
    prediction = predict_price(
        budget=_BASE_AMOUNT,
        category="service",
        description="일반 용역",
        agency_name="고가기관",
    )
    assert prediction["predictor_name"] == "award_rate_gbm"
    assert prediction["predictor_family"] == "gbm"
    assert prediction["pricing_mode"] == "award_rate_gbm"


def test_missing_artifact_falls_back_to_historical(monkeypatch):
    """선호만 켜고 학습 산출물이 없으면 라이브가 깨지지 않고 베이스라인으로 떨어진다."""
    monkeypatch.setattr(settings, "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS", True)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_AWARD_RATE_GBM_MODEL_PATH", "")
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "award_rate_gbm"
    )
    prediction = predict_price(
        budget=_BASE_AMOUNT, category="service", description="일반 용역"
    )
    assert prediction["predictor_name"] != "award_rate_gbm"
    assert "award-rate GBM model artifact" in str(prediction["fallback_reason"])


def test_guardrail_red_line_with_bypass_mutant_proof(gbm_enabled, monkeypatch):
    """red line: 하한 미만 원시 산출이 guardrail 로 상향되고, 우회 뮤턴트면 실패한다."""
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "award_rate_gbm"
    )
    predictor = AwardRateGbmPredictor()

    # (a) 원시 predictor 산출이 실제로 하한 미만이다 — 이 단언이 없으면 아래 red line 은
    #     guardrail 이 없어도 통과하는 무의미한 검사가 된다(#360).
    raw = predictor.predict(_context(agency_name="저가기관", category="construction"))
    assert raw.predicted_bid_rate < _LEGAL_FLOOR - 0.05

    kwargs = dict(
        budget=_BASE_AMOUNT,
        category="construction",
        description="항만 준설 공사",
        agency_name="저가기관",
        legal_floor_bid_rate=_LEGAL_FLOOR,
    )
    guarded = predict_price(**kwargs)
    assert guarded["predictor_name"] == "award_rate_gbm"
    assert guarded["predicted_bid_rate"] >= _LEGAL_FLOOR - 1e-9
    assert guarded["guardrail_applied"] is True
    assert all(
        candidate["bid_rate"] >= _LEGAL_FLOOR - 1e-9
        for candidate in guarded["bid_rate_candidates"]
    )

    # (b) guardrail 우회 뮤턴트: 단계를 무력화하면 red line 이 실패해야 한다 —
    #     즉 이 테스트는 guardrail 이 살아 있어야만 통과한다.
    monkeypatch.setattr(
        prediction_orchestration,
        "_apply_prediction_guardrails",
        lambda payload, **_kwargs: payload,
    )
    mutant = predict_price(**kwargs)
    assert mutant["predicted_bid_rate"] < _LEGAL_FLOOR
    assert mutant["predicted_bid_rate"] == pytest.approx(
        raw.predicted_bid_rate, abs=0.005
    )


def test_gbm_key_is_excluded_from_automatic_promotion():
    """선호를 자동으로 바꾸는 세 경로가 이 키를 집지 못한다(Phase 1 과 같은 형태)."""
    assert "award_rate_gbm" in AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS
    assert "distribution" in AUTO_PROMOTION_EXCLUDED_PREDICTOR_KEYS


def test_gbm_is_excluded_from_backtest_best_candidate(monkeypatch):
    """정확도가 더 좋아도 GBM 은 best 후보가 될 수 없다(자동 승격 차단)."""
    from app.ai.predictor_backtest import build_predictor_backtest_report

    from tests.test_prediction_distribution_predictor import (
        _FixedRatePredictor,
        _bid_rate_history,
    )

    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_HOLDOUT_SIZE", 5)
    monkeypatch.setattr(settings, "PRICE_PREDICTION_BACKTEST_MIN_TRAINING_SAMPLES", 5)
    context = PricePredictionContext(
        budget=_BASE_AMOUNT,
        category="construction",
        description="승격 제외 검증",
        historical_records=tuple(_bid_rate_history(12)),
    )

    report = build_predictor_backtest_report(
        context,
        {
            "award_rate_gbm": _FixedRatePredictor("gbm_probe", 0.91),
            "historical": _FixedRatePredictor("hist_probe", 0.95),
        },
    )

    by_key = {result["predictor_key"]: result for result in report["results"]}
    assert by_key["award_rate_gbm"]["status"] == "completed"
    # 제외가 '우연히 진 것'이 아님을 증명: GBM 오차가 명백히 더 작다.
    assert (
        by_key["award_rate_gbm"]["average_absolute_error_rate"]
        < by_key["historical"]["average_absolute_error_rate"]
    )
    assert report["best_predictor_key"] == "historical"
    assert "award_rate_gbm" in report["excluded_predictor_arms"]


def test_price_prediction_endpoint_serves_the_gbm_payload(
    client, test_db, gbm_enabled, monkeypatch
):
    """K1 회귀: 선호를 켰을 때 응답 모델(pricing_mode Literal)을 통과해야 한다.

    predict_price() dict 계약 테스트만으로는 라우트의 ResponseValidationError(500)를
    못 본다 — 실제 엔드포인트로 왕복해 라우트 경계를 고정한다.
    """
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_PREFERRED_PREDICTOR", "award_rate_gbm"
    )
    project_response = client.post(
        "/api/v1/projects/",
        json={
            "title": "Award-rate GBM Route Project",
            "description": "gbm route boundary test",
            "requirements": "Need gbm payload",
            "budget_estimate": _BASE_AMOUNT,
            "category": "service",
        },
    )
    project_id = project_response.json()["id"]
    test_db.add(
        HistoricalData(
            notice_number="GBM-ROUTE-1",
            category="service",
            agency_name="고가기관",
            base_amount=_BASE_AMOUNT,
            base_amount_basis="clean",
            bid_rate=_HIGH_RATE,
        )
    )
    test_db.commit()

    response = client.post(
        "/api/v1/predictions/price",
        json={
            "project_id": project_id,
            "budget_estimate": _BASE_AMOUNT,
            "category": "service",
            "description": "gbm route boundary test",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["pricing_mode"] == "award_rate_gbm"
    assert data["predictor_name"] == "award_rate_gbm"
    assert data["predictor_family"] == "gbm"
