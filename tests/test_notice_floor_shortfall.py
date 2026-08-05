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
from app.services.floor_shortfall import (
    EXCLUDED_ASSESSMENT_BAND,
    AssessmentRateSamples,
    load_assessment_rate_samples,
)
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
    demand_agency: str = "수요기관 F",
    issuing_agency: str = "발주기관 G",
) -> tuple[Project, BidDecisionRecord]:
    operator = ensure_operator_account(test_db)
    project = Project(
        title="하한 미달 빈도 테스트 공고",
        description="floor shortfall test",
        requirements="",
        budget_estimate=budget,
        category=category,
        notice_number="20260201-001",
        demand_agency=demand_agency,
        issuing_agency=issuing_agency,
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

    floor = resolve_notice_floor_rate(project)
    assert floor.rate is None
    assert estimate.shortfall_frequency is None
    assert "낙찰하한율을 해석할 수 없어" in (estimate.unmeasurable_reason or "")
    assert "위험이 없다는 뜻이 아닙니다" in (estimate.unmeasurable_reason or "")
    # 하한이 없으면 표본을 읽을 이유도 없다(요청 경로에서 헛스캔 금지).
    assert cache.requested == []


def test_estimate_separates_invalid_bid_inputs_from_missing_floor(test_db):
    """기초금액 0 은 '하한율 미해석'이 아니라 '투찰율 산출 불가'로 나가야 한다.

    두 사유를 합치면 운영자가 "이 공고엔 하한이 안 붙었나 보다"로 잘못 읽는다. 실제로는
    금액 쪽이 비어 있어 우리 계산이 시작조차 못 한 상태다.
    """
    project, _ = _seed_notice(test_db)  # 하한율(0.88)은 정상 해석되는 공고
    cache = _RecordingCache({}, _samples())

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=0.0,
        cache=cache,
    )

    assert resolve_notice_floor_rate(project).rate == 0.88  # 하한율은 멀쩡하다
    assert estimate.shortfall_frequency is None
    reason = estimate.unmeasurable_reason or ""
    assert "투찰율을 산출할 수 없습니다" in reason
    assert "낙찰하한율을 해석할 수 없어" not in reason
    assert "투찰율 산출 불가" in estimate.scope
    assert cache.requested == []


def test_estimate_is_unmeasurable_when_floor_model_does_not_apply(test_db):
    """비국가기관 발주(협동조합·산학협력단)는 하한 모델 적용 대상이 아니라 판정 불가.

    홀드아웃 품질 판정이 쓰는 것과 같은 게이트(#274)를 표시 경로도 통과해야 한다 —
    적용 대상이 아닌 공고에 국가계약 하한을 대면 없는 근거로 위험을 말하게 된다.
    """
    project, _ = _seed_notice(
        test_db, issuing_agency="○○농업협동조합", demand_agency="수요기관 F"
    )
    cache = _RecordingCache({}, _samples())

    estimate = estimate_notice_floor_shortfall(
        test_db,
        project,
        recommended_amount=96_800_000.0,
        bid_base_amount=110_000_000.0,
        cache=cache,
    )

    assert resolve_notice_floor_rate(project).rate is None
    assert estimate.shortfall_frequency is None
    assert "적격심사 낙찰하한 모델이 적용되지 않거나" in (
        estimate.unmeasurable_reason or ""
    )
    assert "하한 모델 미적용" in estimate.scope
    assert cache.requested == []


def test_estimate_is_unmeasurable_for_uncertain_agency_type(test_db):
    """국공립/사립을 이름으로 가릴 수 없는 부류(대학교)도 판정을 생략한다."""
    project, _ = _seed_notice(test_db, issuing_agency="○○대학교")

    assert resolve_notice_floor_rate(project).rate is None


def test_floor_rate_resolution_prefers_issuing_agency(test_db):
    """적용 범위 판정의 기관 축은 발주기관 우선(resolve_agency_group 컨벤션).

    조달청 경유 공고처럼 발주≠수요인 건에서 적용 범위를 가르는 것은 계약 주체인
    발주기관이다. 수요기관이 비국가기관이어도 발주기관이 국가기관이면 판정한다.
    """
    judgeable, _ = _seed_notice(
        test_db, issuing_agency="조달청", demand_agency="○○농업협동조합"
    )
    assert resolve_notice_floor_rate(judgeable).rate == 0.88

    # 반대 배치 — 발주기관이 비국가기관이면 수요기관이 국가기관이어도 판정하지 않는다.
    skipped, _ = _seed_notice(
        test_db, issuing_agency="○○농업협동조합", demand_agency="조달청"
    )
    assert resolve_notice_floor_rate(skipped).rate is None


def test_sample_scope_discloses_the_excluded_assessment_band(test_db):
    """실제 표본 스코프가 제외 밴드(사정률 1±0.001)를 밝혀야 한다.

    그 밴드가 임계 사정률의 어느 쪽에 있느냐로 빈도의 편향 방향이 갈리므로(과대/과소가
    뒤집힌다), 값을 읽는 쪽이 밴드를 볼 수 없으면 방향을 판단할 수 없다.
    """
    samples = load_assessment_rate_samples(test_db, category="construction")

    assert f"사정률 1±{EXCLUDED_ASSESSMENT_BAND:g} 표본 제외" in samples.scope
    assert "카테고리=construction" in samples.scope


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
