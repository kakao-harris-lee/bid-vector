"""낙찰률 GBM 피처 커널 — 계약 고정 · 수축 방향 · 예비가격 배제 회귀 가드.

이 파일의 중심은 **피처 계약**이다. 학습과 서빙이 같은 조립 함수를 부르므로, 그 함수의
시그니처가 곧 "무엇이 피처가 될 수 있는가"이고, 여기 단언이 그 경계를 고정한다.
"""

import inspect
from dataclasses import fields

import pytest

from app.domain.award_rate_features import (
    AGENCY_ENCODING_FEATURE,
    AGENCY_SAMPLE_FEATURE,
    AWARD_RATE_AGENCY_PRIOR_STRENGTH,
    AWARD_RATE_FEATURE_NAMES,
    CATEGORICAL_AWARD_RATE_FEATURES,
    CATEGORY_FEATURE,
    DENOMINATOR_SOURCE_FEATURE,
    LOG_AMOUNT_FEATURE,
    SERVING_DENOMINATOR_SOURCE,
    UNKNOWN_CATEGORY_CODE,
    AwardRateFeatureSpace,
    AwardRateObservation,
    build_agency_target_encoding,
    normalize_feature_key,
)
from app.domain.reliable_base import ReliableBaseSource
from app.services.ml_training.award_rate_gbm import AwardRateTrainingRow


def _space(observations, *, categories=("construction", "service")):
    return AwardRateFeatureSpace(
        categories=tuple(categories),
        denominator_sources=("clean-base", "reserve-estimate"),
        agency_encoding=build_agency_target_encoding(observations),
    )


# --------------------------------------------------------------------------
# 피처 계약 — 예비가격 파생값이 들어올 자리가 없다는 것을 시그니처로 고정한다
# --------------------------------------------------------------------------


def test_feature_names_are_the_declared_order():
    """피처 순서는 아티팩트에 직렬화되는 위치 계약이다 — 바뀌면 부스터가 다른 잎을 탄다."""
    assert AWARD_RATE_FEATURE_NAMES == (
        CATEGORY_FEATURE,
        LOG_AMOUNT_FEATURE,
        AGENCY_ENCODING_FEATURE,
        AGENCY_SAMPLE_FEATURE,
        DENOMINATOR_SOURCE_FEATURE,
    )
    assert CATEGORICAL_AWARD_RATE_FEATURES == {
        CATEGORY_FEATURE,
        DENOMINATOR_SOURCE_FEATURE,
    }


def test_feature_builder_cannot_accept_reserve_derived_inputs():
    """조립기는 금액·공종·기관·분모출처 넷만 받는다 (train/serve skew red line).

    대상 공고의 예비가격·추첨번호·실현 사정률은 투찰 시점에 존재하지 않으므로 피처가
    될 수 없다. "쓰지 말자"는 규율 대신 **넘길 자리가 없다**로 강제하고, 이 단언이 그
    시그니처를 고정한다. 인자가 하나라도 늘면 여기서 실패한다.
    """
    parameters = inspect.signature(AwardRateFeatureSpace.build_row).parameters
    assert set(parameters) == {
        "self",
        "amount",
        "category",
        "agency",
        "denominator_source",
    }
    # 키워드 전용이라 호출부가 위치로 잘못 밀어 넣을 수 없다.
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
        for name in ("amount", "category", "agency", "denominator_source")
    )


def test_published_floor_is_carried_for_diagnosis_but_is_not_a_feature():
    """공시 하한은 학습 행에 실리되 **조립기에 넘길 자리가 없다**.

    이 두 단언이 함께 있어야 의미가 있다: 행에는 있으니 리포트가 두 층의 학습/홀드아웃
    평균을 비교할 수 있고(재도입 조건 확인), 조립기 시그니처에는 없으니 그 값이 피처로
    새지 않는다. 백필 커버리지가 시간에 기울어 학습의 "미공시"와 서빙의 "미공시"가 다른
    것을 뜻하는 동안은 후자가 red line 이다 — 사유는 그 필드 docstring.
    """
    assert "published_floor_rate" in {
        field.name for field in fields(AwardRateTrainingRow)
    }
    assert "published_floor_rate" not in inspect.signature(
        AwardRateFeatureSpace.build_row
    ).parameters
    assert not [
        name
        for name in AWARD_RATE_FEATURE_NAMES
        if "floor" in name
    ]


def test_training_row_carries_no_reserve_or_price_disclosure_fields():
    """학습 행에도 예비가격 계열 필드가 없다 — 로더가 그 컬럼을 읽지 않는다는 계약."""
    field_names = {field.name for field in fields(AwardRateTrainingRow)}
    assert field_names == {
        "value",
        "amount",
        "category",
        "agency",
        "denominator_source",
        "opened_at",
        "published_floor_rate",
    }
    forbidden = ("reserve", "selected_number", "assessment", "yega", "predicted")
    assert not [name for name in field_names for token in forbidden if token in name]


def test_serving_source_is_the_observed_base_stratum():
    """서빙은 복구 추정 층이 아니라 관측 기초금액 층으로 고정된다."""
    assert SERVING_DENOMINATOR_SOURCE is ReliableBaseSource.CLEAN_BASE


# --------------------------------------------------------------------------
# 인코딩 — 수축 방향과 미관측 폴백
# --------------------------------------------------------------------------


def test_deep_agency_keeps_its_own_mean_shallow_agency_shrinks():
    """κ 대비 표본이 깊으면 자기 평균에 가깝고, 얕으면 공종 평균으로 접힌다."""
    deep = [
        AwardRateObservation(agency="깊은기관", category="construction", value=0.80)
        for _ in range(int(AWARD_RATE_AGENCY_PRIOR_STRENGTH) * 10)
    ]
    others = [
        AwardRateObservation(agency=f"기관{index}", category="construction", value=0.95)
        for index in range(200)
    ]
    shallow = [AwardRateObservation(agency="얕은기관", category="construction", value=0.80)]
    encoding = build_agency_target_encoding(deep + others + shallow)

    deep_mean, deep_count = encoding.encode(agency="깊은기관", category="construction")
    shallow_mean, shallow_count = encoding.encode(
        agency="얕은기관", category="construction"
    )
    assert deep_count == len(deep)
    assert shallow_count == 1
    # 같은 관측값(0.80)인데 표본 깊이만 다르다 — 얕은 쪽이 공종 평균 쪽으로 접힌다.
    assert deep_mean < shallow_mean
    assert deep_mean == pytest.approx(0.80, abs=0.02)
    category_mean = encoding.category_means["construction"]
    assert abs(shallow_mean - category_mean) < abs(shallow_mean - 0.80)


def test_unseen_agency_falls_back_to_category_mean_with_zero_samples():
    encoding = build_agency_target_encoding(
        [
            AwardRateObservation(agency="가", category="service", value=0.97),
            AwardRateObservation(agency="나", category="service", value=0.99),
        ]
    )
    mean, count = encoding.encode(agency="처음보는기관", category="service")
    assert count == 0
    assert 0.9 < mean < 1.0


def test_agency_is_keyed_together_with_category():
    """한 기관이 두 공종을 발주하면 축이 갈린다 — 기관만으로 잡으면 구성비가 섞인다."""
    encoding = build_agency_target_encoding(
        [
            AwardRateObservation(agency="시청", category="construction", value=0.85)
            for _ in range(60)
        ]
        + [
            AwardRateObservation(agency="시청", category="service", value=0.99)
            for _ in range(60)
        ]
    )
    construction, _ = encoding.encode(agency="시청", category="construction")
    service, _ = encoding.encode(agency="시청", category="service")
    assert construction < service


def test_empty_observations_produce_an_empty_encoding():
    encoding = build_agency_target_encoding([])
    assert encoding.global_mean == 0.0
    assert encoding.encode(agency="가", category="service") == (0.0, 0)


# --------------------------------------------------------------------------
# 행 조립 — 어휘 코드 · log 금액 · 정규화
# --------------------------------------------------------------------------


def test_build_row_maps_vocabulary_positions_and_log_amount():
    space = _space(
        [AwardRateObservation(agency="시청", category="service", value=0.95)]
    )
    row = space.build_row(
        amount=1_000_000_000.0,
        category="service",
        agency="시청",
        denominator_source="reserve-estimate",
    )
    assert len(row) == len(AWARD_RATE_FEATURE_NAMES)
    assert row[0] == 1.0  # categories=("construction", "service")
    assert row[1] == pytest.approx(9.0)  # log10(1e9)
    assert row[4] == 1.0  # denominator_sources=("clean-base", "reserve-estimate")


def test_unseen_category_and_source_use_the_missing_code():
    space = _space([AwardRateObservation(agency="시청", category="service", value=0.95)])
    row = space.build_row(
        amount=1.0, category="처음보는공종", agency="시청", denominator_source="없음"
    )
    assert row[0] == UNKNOWN_CATEGORY_CODE
    assert row[4] == UNKNOWN_CATEGORY_CODE


def test_non_positive_amount_is_floored_instead_of_raising():
    """0원/음수는 실적이 아니라 적재 사고 — log 가 죽는 대신 최소 좌표로 떨어진다."""
    space = _space([AwardRateObservation(agency="시청", category="service", value=0.95)])
    for amount in (0.0, -1.0):
        assert space.build_row(
            amount=amount,
            category="service",
            agency="시청",
            denominator_source="clean-base",
        )[1] == pytest.approx(0.0)


def test_key_normalization_is_shared_by_aggregation_and_lookup():
    """집계와 조회가 같은 키를 만들지 않으면 서빙에서 모든 기관이 미관측으로 떨어진다."""
    assert normalize_feature_key("  Seoul City ") == "seoul city"
    assert normalize_feature_key(None) == ""
    encoding = build_agency_target_encoding(
        [AwardRateObservation(agency="  시청 ", category="Service", value=0.9)]
    )
    _mean, count = encoding.encode(
        agency=normalize_feature_key("시청"), category=normalize_feature_key("SERVICE")
    )
    assert count == 1


def test_build_row_normalizes_before_lookup():
    space = _space(
        [AwardRateObservation(agency="시청", category="service", value=0.90)],
        categories=("service",),
    )
    lowered = space.build_row(
        amount=1.0, category="service", agency="시청", denominator_source="clean-base"
    )
    spaced = space.build_row(
        amount=1.0, category=" Service ", agency=" 시청 ", denominator_source="clean-base"
    )
    assert lowered == spaced
