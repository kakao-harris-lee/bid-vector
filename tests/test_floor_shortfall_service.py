"""사정률 표본 수집(DB 경계)과 하한 미달 빈도 응답 조립 테스트.

사정률은 저장된 컬럼이 아니라 두 관측의 비(``(낙찰가/기초금액) ÷ (낙찰가/예정가)``)로
도출되므로, 두 분모가 독립이 아니면 값이 1 로 붕괴해 "위험 없음"이라는 거짓 신호가
된다. 그래서 이 테스트의 대부분은 **오염 행이 표본에서 빠지는지**를 못 박는다.
"""

from datetime import UTC, datetime

import pytest

from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import BASIS_CLEAN, BASIS_DERIVED_YEGA
from app.services.floor_shortfall import (
    AssessmentRateSamples,
    build_floor_shortfall_estimate,
    estimate_floor_shortfall,
    load_assessment_rate_samples,
)

_BASE_AMOUNT = 100_000_000.0
# 낙찰가/예정가 — KONEPS 가 보고하는 성공사정률(예정가-basis 독립 관측).
_AWARD_RATE_ON_YEGA = 0.9
_OPENED_AT = datetime(2026, 5, 1, tzinfo=UTC)
# 하한 88%에 88.11%로 투찰한 실투찰 조합(임계 사정률 1.00125).
_RECOMMENDED_RATE = 0.8811
_FLOOR_RATE = 0.88


def _add_opening(
    db,
    *,
    assessment_rate: float = 1.01,
    basis: str | None = BASIS_CLEAN,
    reserve_prices: str = "[]",
    opened_at: datetime | None = _OPENED_AT,
    category: str = "construction",
    rate_derived_from_base: bool = False,
    base_amount: float = _BASE_AMOUNT,
) -> Project:
    """개찰 완료 공고 한 건(공고 + 이력 + 개찰결과)을 만든다.

    ``assessment_rate`` 가 이 행이 내놓아야 할 사정률(예정가/기초금액)이다. 기본은
    KONEPS 가 보고한 예정가-basis 낙찰률을 싣지만, ``rate_derived_from_base`` 를 켜면
    보고 낙찰률 자리에 **금액비**(낙찰가/기초금액)를 넣어 파생 오염 행을 재현한다.
    """
    project = Project(
        title="하한 미달 빈도 표본",
        description="사정률 표본 수집 검증",
        requirements="",
        budget_estimate=base_amount,
        category=category,
    )
    db.add(project)
    db.flush()

    planned_price = base_amount * assessment_rate  # 예정가
    winning_amount = planned_price * _AWARD_RATE_ON_YEGA
    reported_rate = (
        winning_amount / base_amount if rate_derived_from_base else _AWARD_RATE_ON_YEGA
    )

    db.add(
        HistoricalData(
            project_id=project.id,
            category=category,
            base_amount=base_amount,
            base_amount_basis=basis,
            reserve_prices=reserve_prices,
            opened_at=opened_at,
            bid_rate=0.0,
        )
    )
    db.add(
        TenderResult(
            project_id=project.id,
            winning_amount=winning_amount,
            winning_rate=reported_rate,
        )
    )
    db.flush()
    return project


class TestLoadAssessmentRateSamples:
    def test_clean_row_with_independent_rate_yields_the_assessment_rate(self, test_db):
        """clean 기초금액 + 독립 관측 낙찰률 → 사정률이 그대로 복원된다."""
        _add_opening(test_db, assessment_rate=1.01)

        samples = load_assessment_rate_samples(test_db)

        assert len(samples.values) == 1
        assert samples.values[0] == pytest.approx(1.01, abs=1e-6)

    def test_derived_yega_basis_row_is_excluded(self, test_db):
        """base_amount 가 예정가 역산(#199)이면 사정률이 1 로 붕괴하므로 제외한다."""
        _add_opening(test_db, basis=BASIS_DERIVED_YEGA)

        assert load_assessment_rate_samples(test_db).values == ()

    def test_unclassified_basis_row_is_excluded(self, test_db):
        """basis 미분류 행은 오염 여부를 증명할 수 없으므로 표본에 넣지 않는다."""
        _add_opening(test_db, basis=None)

        assert load_assessment_rate_samples(test_db).values == ()

    def test_rate_derived_from_base_without_reserve_evidence_is_excluded(
        self, test_db
    ):
        """보고 낙찰률이 금액비 파생이면 사정률=1 의 거짓 표본이라 제외한다."""
        _add_opening(test_db, rate_derived_from_base=True, reserve_prices="[]")

        assert load_assessment_rate_samples(test_db).values == ()

    def test_reserve_prices_restore_an_amount_matching_row(self, test_db):
        """복수예비가격이 충분하면 예정가를 독립 재구성할 수 있어 판정을 유지한다."""
        _add_opening(
            test_db,
            assessment_rate=1.0,
            rate_derived_from_base=True,
            reserve_prices="[1, 2, 3, 4, 5]",
        )

        samples = load_assessment_rate_samples(test_db)

        assert len(samples.values) == 1
        assert samples.values[0] == pytest.approx(1.0, abs=1e-6)

    def test_implausible_assessment_rate_is_dropped(self, test_db):
        """개연 범위(±10%) 밖 값은 추첨 결과가 아니라 데이터 오류다."""
        _add_opening(test_db, assessment_rate=1.4)

        assert load_assessment_rate_samples(test_db).values == ()

    def test_rows_opened_after_as_of_are_excluded(self, test_db):
        """시간 누수 차단: 기준 시점 이후 개찰은 백테스트 표본에 들어오면 안 된다."""
        _add_opening(test_db, opened_at=datetime(2026, 1, 1, tzinfo=UTC))
        _add_opening(test_db, opened_at=datetime(2026, 7, 1, tzinfo=UTC))

        samples = load_assessment_rate_samples(
            test_db, as_of=datetime(2026, 3, 1, tzinfo=UTC)
        )

        assert len(samples.values) == 1

    def test_rows_without_opening_date_are_excluded(self, test_db):
        """개찰일이 없으면 과거임을 증명할 수 없다 → 항상 제외."""
        _add_opening(test_db, opened_at=None)

        assert load_assessment_rate_samples(test_db).values == ()

    def test_category_narrows_the_scope(self, test_db):
        _add_opening(test_db, category="construction")
        _add_opening(test_db, category="service")

        assert len(load_assessment_rate_samples(test_db).values) == 2
        assert (
            len(load_assessment_rate_samples(test_db, category="construction").values)
            == 1
        )

    def test_one_sample_per_project(self, test_db):
        """같은 개찰이 여러 행으로 적재돼도 분포에 중복 가중되지 않는다."""
        project = _add_opening(test_db)
        test_db.add(
            TenderResult(
                project_id=project.id,
                winning_amount=_BASE_AMOUNT * 1.01 * _AWARD_RATE_ON_YEGA,
                winning_rate=_AWARD_RATE_ON_YEGA,
            )
        )
        test_db.flush()

        assert len(load_assessment_rate_samples(test_db).values) == 1

    def test_scope_records_the_selection_basis(self, test_db):
        samples = load_assessment_rate_samples(test_db, category="construction")

        assert "clean-basis" in samples.scope
        assert "construction" in samples.scope


class TestEstimateFloorShortfall:
    def test_insufficient_samples_report_unmeasurable_not_zero(self, test_db):
        """표본 부족은 빈도 0 이 아니라 None + 사유 — 침묵이 안전으로 읽히면 안 된다."""
        _add_opening(test_db)

        estimate = estimate_floor_shortfall(test_db, _RECOMMENDED_RATE, _FLOOR_RATE)

        assert estimate.shortfall_frequency is None
        assert estimate.sample_count == 1
        assert estimate.unmeasurable_reason is not None
        assert "위험이 없다는 뜻이 아닙니다" in estimate.unmeasurable_reason
        # 임계 사정률은 표본과 무관하게 산출되므로 판정 불가여도 제공한다.
        assert estimate.critical_assessment_rate == pytest.approx(1.00125, abs=1e-6)

    def test_measured_estimate_reports_frequency_with_its_evidence(self, test_db):
        for assessment_rate in (1.01, 1.01, 0.99, 0.99):
            _add_opening(test_db, assessment_rate=assessment_rate)

        estimate = estimate_floor_shortfall(
            test_db, _RECOMMENDED_RATE, _FLOOR_RATE, min_samples=4
        )

        assert estimate.shortfall_frequency == pytest.approx(0.5)
        assert estimate.shortfall_sample_count == 2
        assert estimate.sample_count == 4
        assert estimate.minimum_sample_count == 4
        assert estimate.unmeasurable_reason is None

    def test_invalid_rates_report_their_own_reason(self, test_db):
        estimate = estimate_floor_shortfall(test_db, _RECOMMENDED_RATE, 0.0)

        assert estimate.shortfall_frequency is None
        assert estimate.critical_assessment_rate is None
        assert "임계 사정률을 정의할 수 없습니다" in estimate.unmeasurable_reason

    def test_preloaded_samples_bypass_the_database(self, test_db):
        """캐시 seam: 표본을 주입하면 DB 를 다시 읽지 않는다."""
        _add_opening(test_db, assessment_rate=0.99)
        injected = AssessmentRateSamples(values=(1.01, 1.02), scope="주입된 표본")

        estimate = estimate_floor_shortfall(
            test_db,
            _RECOMMENDED_RATE,
            _FLOOR_RATE,
            samples=injected,
            min_samples=2,
        )

        assert estimate.sample_count == 2
        assert estimate.shortfall_frequency == pytest.approx(1.0)
        assert estimate.scope == "주입된 표본"


class TestBuildFloorShortfallEstimate:
    def test_frequency_never_named_a_probability(self):
        """정직 명세 §2: 응답 표면 어디에도 probability 를 노출하지 않는다."""
        estimate = build_floor_shortfall_estimate(
            _RECOMMENDED_RATE,
            _FLOOR_RATE,
            AssessmentRateSamples(values=(1.01,) * 4, scope="테스트"),
            min_samples=4,
        )

        assert "probability" not in estimate.model_dump_json()
        assert not any("probab" in field for field in estimate.model_dump())
