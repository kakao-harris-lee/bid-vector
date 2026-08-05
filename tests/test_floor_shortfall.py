"""하한 미달 빈도 커널(순수)의 값 테이블 테스트.

추천가는 기초금액 기준, 실격 하한은 예정가격 기준이라 사정률(예정가/기초금액) 추첨
결과가 둘 사이를 가른다. 임계 사정률 a* = 추천율/하한율을 **초과**한 표본 비율이
이 커널의 전부이므로, 경계·표본부족·개연범위를 값으로 못 박는다.

정직 명세 §2: 이 값은 확률이 아니라 과거 표본 비율이며, 표본이 부족하면 0 이 아니라
None(판정 불가)이어야 한다.
"""

import pytest

from app.domain.floor_shortfall import (
    ASSESSMENT_RATE_MAX,
    ASSESSMENT_RATE_MIN,
    MIN_ASSESSMENT_SAMPLES,
    floor_shortfall_frequency,
    is_plausible_assessment_rate,
    resolve_critical_assessment_rate,
)

# 실투찰이 반복해서 밟은 조합: 하한 88%에 88.11%로 투찰 → 여유 +0.125%.
_REAL_RECOMMENDED_RATE = 0.8811
_REAL_FLOOR_RATE = 0.88


def _samples(*groups: tuple[float, int]) -> list[float]:
    """``(값, 개수)`` 묶음을 펼쳐 표본 리스트로 만든다."""
    values: list[float] = []
    for value, count in groups:
        values.extend([value] * count)
    return values


class TestResolveCriticalAssessmentRate:
    def test_critical_rate_is_recommended_over_floor(self):
        """a* = 추천율/하한율 — 88.11%/88%는 사정률 +0.125%에서 실격으로 뒤집힌다."""
        assert resolve_critical_assessment_rate(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE
        ) == pytest.approx(1.00125, abs=1e-6)

    @pytest.mark.parametrize(
        "recommended_rate,floor_rate",
        [(0.0, 0.88), (0.88, 0.0), (-0.9, 0.88), (0.88, -0.88)],
    )
    def test_non_positive_inputs_are_undefined(self, recommended_rate, floor_rate):
        assert resolve_critical_assessment_rate(recommended_rate, floor_rate) is None


class TestIsPlausibleAssessmentRate:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1.0, True),
            (ASSESSMENT_RATE_MIN, True),
            (ASSESSMENT_RATE_MAX, True),
            (ASSESSMENT_RATE_MIN - 0.01, False),
            (ASSESSMENT_RATE_MAX + 0.01, False),
            (0.0, False),
            (None, False),
            (float("nan"), False),
        ],
    )
    def test_plausible_range(self, value, expected):
        """개연 범위 밖·None·NaN 은 추첨 결과가 아니라 데이터 오류라 표본에서 뺀다."""
        assert is_plausible_assessment_rate(value) is expected


class TestFloorShortfallFrequency:
    def test_counts_share_of_samples_above_critical_rate(self):
        """빈도 = a* 초과 표본 / 전체 표본. 값 테이블로 고정."""
        samples = _samples((0.99, 150), (1.01, 50))  # a* = 1.00125

        result = floor_shortfall_frequency(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, samples
        )

        assert result is not None
        assert result.sample_count == 200
        assert result.shortfall_sample_count == 50
        assert result.shortfall_frequency == pytest.approx(0.25)
        assert result.critical_assessment_rate == pytest.approx(1.00125, abs=1e-6)

    def test_sample_equal_to_critical_rate_is_not_a_shortfall(self):
        """사정률이 정확히 a* 면 투찰가 == 하한가 → 적격(미만이 실격)이라 세지 않는다."""
        critical_rate = resolve_critical_assessment_rate(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE
        )
        samples = _samples((critical_rate, MIN_ASSESSMENT_SAMPLES))

        result = floor_shortfall_frequency(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, samples
        )

        assert result is not None
        assert result.shortfall_sample_count == 0
        assert result.shortfall_frequency == 0.0

    def test_marginal_bid_flags_most_samples(self):
        """하한에 붙여 투찰하면(a* ≈ 1) 사정률이 1을 넘는 표본이 전부 미달로 잡힌다."""
        samples = _samples((1.002, 100), (0.998, 60))

        result = floor_shortfall_frequency(0.88, 0.88, samples)

        assert result is not None
        assert result.critical_assessment_rate == pytest.approx(1.0)
        assert result.shortfall_sample_count == 100
        assert result.shortfall_frequency == pytest.approx(0.625)

    def test_comfortable_margin_yields_zero_frequency(self):
        """여유가 개연 범위를 넘어서면 빈도는 0 — 0 과 None(판정 불가)은 다른 값이다."""
        samples = _samples((1.02, MIN_ASSESSMENT_SAMPLES))

        result = floor_shortfall_frequency(0.95, 0.88, samples)

        assert result is not None
        assert result.shortfall_frequency == 0.0
        assert result.sample_count == MIN_ASSESSMENT_SAMPLES

    def test_returns_none_below_minimum_samples(self):
        """표본 부족은 '위험 없음'이 아니라 '판정 불가' — 0.0 이 아닌 None."""
        samples = _samples((1.01, MIN_ASSESSMENT_SAMPLES - 1))

        assert (
            floor_shortfall_frequency(
                _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, samples
            )
            is None
        )

    def test_minimum_sample_gate_is_injectable(self):
        samples = _samples((1.01, 4), (0.99, 4))

        result = floor_shortfall_frequency(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, samples, min_samples=8
        )

        assert result is not None
        assert result.sample_count == 8
        assert result.shortfall_frequency == pytest.approx(0.5)

    def test_implausible_samples_leave_the_denominator(self):
        """개연 범위 밖 값은 분모에서 빠지고, 그 결과 표본이 모자라면 None 이 된다."""
        samples = _samples((1.01, 4), (5.0, 4), (0.0, 4))

        result = floor_shortfall_frequency(
            _REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, samples, min_samples=4
        )

        assert result is not None
        assert result.sample_count == 4
        assert result.shortfall_sample_count == 4
        assert result.shortfall_frequency == 1.0

        assert (
            floor_shortfall_frequency(
                _REAL_RECOMMENDED_RATE,
                _REAL_FLOOR_RATE,
                samples,
                min_samples=5,
            )
            is None
        )

    @pytest.mark.parametrize(
        "recommended_rate,floor_rate", [(0.0, 0.88), (0.88, 0.0), (-0.88, 0.88)]
    )
    def test_returns_none_when_critical_rate_undefined(
        self, recommended_rate, floor_rate
    ):
        samples = _samples((1.01, MIN_ASSESSMENT_SAMPLES))

        assert (
            floor_shortfall_frequency(recommended_rate, floor_rate, samples) is None
        )

    def test_empty_samples_return_none(self):
        assert (
            floor_shortfall_frequency(_REAL_RECOMMENDED_RATE, _REAL_FLOOR_RATE, [])
            is None
        )
