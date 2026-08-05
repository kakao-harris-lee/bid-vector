"""공고 단위 하한 미달 빈도 노출 — 표본 캐시 · 스코프 폴백 · 판정 불가 경로.

빈도 커널 자체는 tests/test_floor_shortfall*.py 가 덮는다. 이 파일은 그 커널을 요청
경로에 붙이면서 생긴 계약을 본다: 요청마다 개찰 이력을 다시 스캔하지 않는가(TTL 캐시),
하한을 해석할 수 없는 공고에서 0%가 아니라 판정 불가로 나가는가, 그리고 하한 비교에
쓰는 율이 추정가격이 아니라 기초금액 기준인가.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.single_user import ensure_operator_account
from app.models.models import BidDecisionRecord, HistoricalData, Project
from app.services import notice_floor_shortfall
from app.services.floor_shortfall import AssessmentRateSamples
from app.services.notice_floor_shortfall import (
    SAMPLE_CACHE,
    AssessmentRateSampleCache,
    estimate_notice_floor_shortfall,
    resolve_notice_floor_rate,
)

SUMMARY_PATH = "/api/v1/operations/bid-decisions/{record_id}/summary"

# 임계 사정률 1.0 을 기준으로 30% 가 미달이 되는 표본(140 × 0.99 + 60 × 1.01).
# 최소 표본 수(150)를 넘겨 빈도가 실제로 발표되게 만든다.
_SAMPLE_VALUES = tuple([0.99] * 140 + [1.01] * 60)


def _samples(values=_SAMPLE_VALUES, scope: str = "테스트 표본") -> AssessmentRateSamples:
    return AssessmentRateSamples(values=tuple(values), scope=scope)


class _RecordingCache:
    """스코프별로 미리 정한 표본을 돌려주고 호출을 기록하는 캐시 대역."""

    def __init__(self, by_category: dict, default: AssessmentRateSamples) -> None:
        self._by_category = by_category
        self._default = default
        self.requested: list = []

    def get(self, db, *, category=None) -> AssessmentRateSamples:
        self.requested.append(category)
        return self._by_category.get(category, self._default)


@pytest.fixture(autouse=True)
def _clear_global_sample_cache():
    """전역 표본 캐시가 테스트 사이에 새지 않게 한다."""
    SAMPLE_CACHE.clear()
    yield
    SAMPLE_CACHE.clear()


def _seed_notice(
    test_db,
    *,
    category: str = "construction",
    budget: float = 100_000_000.0,
    base_amount: float | None = 110_000_000.0,
    award_floor_rate: float | None = 0.88,
    recommended_amount: float = 96_800_000.0,
) -> tuple[Project, BidDecisionRecord]:
    operator = ensure_operator_account(test_db)
    project = Project(
        title="하한 미달 빈도 테스트 공고",
        description="floor shortfall test",
        requirements="",
        budget_estimate=budget,
        category=category,
        notice_number="20260201-001",
        demand_agency="수요기관 F",
        issuing_agency="발주기관 G",
        award_floor_rate=award_floor_rate,
        deadline=datetime.now(UTC) + timedelta(hours=10),
    )
    test_db.add(project)
    test_db.commit()
    test_db.refresh(project)

    if base_amount is not None:
        test_db.add(
            HistoricalData(
                project_id=project.id,
                notice_number=project.notice_number,
                category=category,
                base_amount=base_amount,
            )
        )

    decision = BidDecisionRecord(
        project_id=project.id,
        operator_id=operator.id,
        pursue_bid=True,
        action="bid_now",
        decision_status="planned",
        recommended_amount=recommended_amount,
        probability_score=0.7,
        score_breakdown=json.dumps({"strengths": [], "risk_flags": []}),
        reasoning="테스트",
    )
    test_db.add(decision)
    test_db.commit()
    test_db.refresh(decision)
    return project, decision


# --- 표본 캐시 -----------------------------------------------------------------


def test_sample_cache_reuses_samples_within_ttl(monkeypatch, test_db):
    loads: list = []

    def _fake_load(db, *, category=None, as_of=None):
        loads.append(category)
        return _samples()

    monkeypatch.setattr(
        notice_floor_shortfall, "load_assessment_rate_samples", _fake_load
    )
    now = [1_000.0]
    cache = AssessmentRateSampleCache(ttl_seconds=900, clock=lambda: now[0])

    first = cache.get(test_db, category="construction")
    second = cache.get(test_db, category="construction")

    assert first is second
    assert len(loads) == 1, "TTL 안에서는 개찰 이력을 다시 스캔하지 않아야 한다"


def test_sample_cache_reloads_after_ttl_expiry(monkeypatch, test_db):
    loads: list = []

    def _fake_load(db, *, category=None, as_of=None):
        loads.append(category)
        return _samples(scope=f"load-{len(loads)}")

    monkeypatch.setattr(
        notice_floor_shortfall, "load_assessment_rate_samples", _fake_load
    )
    now = [1_000.0]
    cache = AssessmentRateSampleCache(ttl_seconds=900, clock=lambda: now[0])

    cache.get(test_db, category="construction")
    now[0] += 899.0
    cache.get(test_db, category="construction")
    assert len(loads) == 1

    now[0] += 2.0  # TTL 경과
    refreshed = cache.get(test_db, category="construction")

    assert len(loads) == 2
    assert refreshed.scope == "load-2"


def test_sample_cache_separates_scopes_by_category(monkeypatch, test_db):
    loads: list = []

    def _fake_load(db, *, category=None, as_of=None):
        loads.append(category)
        return _samples(scope=str(category))

    monkeypatch.setattr(
        notice_floor_shortfall, "load_assessment_rate_samples", _fake_load
    )
    cache = AssessmentRateSampleCache(ttl_seconds=900, clock=lambda: 0.0)

    cache.get(test_db, category="construction")
    cache.get(test_db, category="service")
    cache.get(test_db, category="construction")

    assert loads == ["construction", "service"]


def test_sample_cache_ttl_defaults_to_settings(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "FLOOR_SHORTFALL_SAMPLE_CACHE_TTL_SECONDS", 123)

    assert AssessmentRateSampleCache().ttl_seconds == 123.0


# --- 공고 단위 추정 --------------------------------------------------------------


def test_estimate_counts_shortfall_against_bid_base_rate(test_db):
    project, _ = _seed_notice(test_db)
    cache = _RecordingCache({}, _samples())

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=110_000_000.0,
        cache=cache,
    )

    # 기초금액 기준 율 0.88 ÷ 하한 0.88 = 임계 사정률 1.0 → 표본 200 중 60건 초과.
    assert estimate.critical_assessment_rate == 1.0
    assert estimate.shortfall_frequency == 0.3
    assert estimate.shortfall_sample_count == 60
    assert estimate.sample_count == 200
    assert estimate.unmeasurable_reason is None


def test_estimate_widens_scope_when_category_samples_are_short(test_db):
    project, _ = _seed_notice(test_db)
    cache = _RecordingCache(
        {"construction": _samples(values=[0.99] * 10, scope="카테고리=construction")},
        _samples(scope="전 카테고리"),
    )

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=110_000_000.0,
        cache=cache,
    )

    assert cache.requested == ["construction", None]
    assert estimate.shortfall_frequency == 0.3
    # 어느 스코프로 셌는지가 결과에 실려 나간다(조용한 스코프 교체 금지).
    assert estimate.scope == "전 카테고리"


def test_estimate_is_unmeasurable_without_a_resolvable_floor(test_db):
    project, _ = _seed_notice(
        test_db, category="service", award_floor_rate=None, base_amount=None
    )
    cache = _RecordingCache({}, _samples())

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=90_000_000.0,
        bid_base_amount=100_000_000.0,
        cache=cache,
    )

    assert resolve_notice_floor_rate(project) is None
    assert estimate.shortfall_frequency is None
    assert "위험이 없다는 뜻이 아닙니다" in (estimate.unmeasurable_reason or "")
    # 하한이 없으면 표본을 읽을 이유도 없다(요청 경로에서 헛스캔 금지).
    assert cache.requested == []


def test_estimate_is_unmeasurable_when_sample_load_fails(test_db):
    """표본 적재가 실패해도 요약이 깨지지 않고 판정 불가로 나간다(베스트에포트)."""

    class _BrokenCache:
        def get(self, db, *, category=None):
            raise RuntimeError("표본 조회 실패")

    project, _ = _seed_notice(test_db)

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=110_000_000.0,
        cache=_BrokenCache(),
    )

    assert estimate.shortfall_frequency is None
    assert "위험이 없다는 뜻이 아닙니다" in (estimate.unmeasurable_reason or "")


def test_estimate_is_unmeasurable_when_samples_are_insufficient(test_db):
    project, _ = _seed_notice(test_db)
    cache = _RecordingCache({}, _samples(values=[0.99] * 3, scope="부족 표본"))

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=110_000_000.0,
        cache=cache,
    )

    # 표본 부족은 0(위험 없음)이 아니라 사유가 붙은 판정 불가다(정직 명세 §2).
    assert estimate.shortfall_frequency is None
    assert estimate.shortfall_sample_count == 0
    assert estimate.sample_count == 3
    assert "미달" in (estimate.unmeasurable_reason or "")


# --- 응답 경로 -------------------------------------------------------------------


def test_bid_summary_reports_measured_shortfall_frequency(
    monkeypatch, client, test_db
):
    """요약 응답이 실제 빈도 값을 싣는다(스키마 라벨이 아니라 값 검증)."""
    monkeypatch.setattr(
        notice_floor_shortfall,
        "load_assessment_rate_samples",
        lambda db, category=None, as_of=None: _samples(scope="테스트 표본"),
    )
    _, decision = _seed_notice(test_db)

    payload = client.get(SUMMARY_PATH.format(record_id=decision.id)).json()

    shortfall = payload["floor_shortfall"]
    assert shortfall["shortfall_frequency"] == 0.3
    assert shortfall["sample_count"] == 200
    assert shortfall["critical_assessment_rate"] == 1.0
    assert shortfall["scope"] == "테스트 표본"
    assert shortfall["unmeasurable_reason"] is None
