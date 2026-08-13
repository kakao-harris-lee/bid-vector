"""품질 사다리(값표) — 각 단계가 실제로 거르는가, 그리고 무엇을 세는가.

DB 를 만지지 않는다: :func:`build_landing_row` 가 스칼라만 받으므로 사다리 전체가 값
테이블로 검증된다(§4.7-4). 여기서 고정하는 것은 "몇 건이 통과하는가"가 아니라 **어떤
행이 어느 사유로 떨어지는가**다 — 사유가 뭉개지면 "표본이 얕다"와 "표본이 오염됐다"가
리포트에서 구별되지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.services.ml_training.award_landing_dataset import _CorpusAccumulator
from app.services.ml_training.award_landing_ladder import (
    AMBIGUOUS_REGIME_LABEL,
    DEFAULT_REGIME_GATE,
    ERA_TIER_POST_REVISION,
    ERA_TIER_PRE_REVISION,
    REGIME_GATE_POLICIES,
    LadderStage,
    LandingCandidate,
    LandingRowOutcome,
    build_landing_row,
    resolve_era_tier,
)

FLOOR_BOUND_TEXT = "적격심사 가격입찰 공사"
NEGOTIATED_TEXT = "수의계약 견적 제출 안내"
FIFTEEN_RESERVES = json.dumps([1.0] * 15)


def run_ladder(
    *, gate: str = DEFAULT_REGIME_GATE, **overrides: object
) -> LandingRowOutcome:
    """통과하는 기본 행에 축 하나만 덮어써 사다리를 돌린다."""
    base: dict[str, object] = {
        "project_id": 1,
        "opened_at": datetime(2026, 7, 1, tzinfo=UTC),
        "category": "construction",
        "agency_name": "울산광역시",
        "regime_text": FLOOR_BOUND_TEXT,
        "award_floor_rate": 0.89745,
        "winning_amount": 88_000_000.0,
        "winning_rate": 0.9,
        "base_amount": 100_000_000.0,
        "base_amount_basis": "clean",
        "base_amount_estimated": None,
        "reserve_prices": FIFTEEN_RESERVES,
    }
    base.update(overrides)
    return build_landing_row(
        LandingCandidate(**base),  # type: ignore[arg-type]
        regime_labels=REGIME_GATE_POLICIES[gate],
    )


def test_baseline_row_passes_with_recomputed_axes() -> None:
    """통과 행의 세 축은 ω=낙찰가/B, a=ω/w, Δ=w−f 로 **재계산**된다(n5)."""
    outcome = run_ladder()

    assert outcome.stage is None
    row = outcome.row
    assert row is not None
    assert row.omega_direct == pytest.approx(0.88)
    assert row.assessment_ratio == pytest.approx(0.88 / 0.9)
    assert row.margin == pytest.approx(0.9 - 0.89745)
    # 커널 관측은 같은 a 로 φ·ω 를 만든다 — ω 는 직접값과 일치해야 한다.
    assert row.observation.realized_winning_rate == pytest.approx(row.omega_direct)
    assert row.observation.realized_floor_rate == pytest.approx(
        0.89745 * row.assessment_ratio
    )


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"award_floor_rate": None}, LadderStage.FLOOR_RATE_MISSING),
        ({"award_floor_rate": 1.0}, LadderStage.FLOOR_RATE_UNITY),
        ({"award_floor_rate": 0.999}, LadderStage.FLOOR_RATE_IMPLAUSIBLE),
        ({"agency_name": "한국대학교 산학협력단"}, LadderStage.FLOOR_NOT_APPLICABLE),
        # 미정산은 NULL 이 아니라 0.0 으로 적재된다 — 이 함정이 사다리에 있어야 한다.
        ({"winning_amount": 0.0}, LadderStage.WINNING_AMOUNT_MISSING),
        ({"winning_rate": 0.0}, LadderStage.WINNING_RATE_UNUSABLE),
        ({"winning_rate": 3.0}, LadderStage.WINNING_RATE_UNUSABLE),
        ({"base_amount_basis": "derived-yega"}, LadderStage.BASE_NOT_CLEAN),
        ({"reserve_prices": "[]"}, LadderStage.RESERVE_PRICES_MISSING),
        ({"winning_amount": 70_000_000.0}, LadderStage.ASSESSMENT_IMPLAUSIBLE),
        (
            {"category": "service", "regime_text": NEGOTIATED_TEXT},
            LadderStage.REGIME_EXCLUDED,
        ),
    ],
)
def test_each_ladder_stage_rejects_its_own_row(
    overrides: dict[str, object], expected: LadderStage
) -> None:
    outcome = run_ladder(**overrides)

    assert outcome.row is None
    assert outcome.stage is expected


def test_negative_margin_is_rejected_not_clamped() -> None:
    """낙찰률이 하한 아래면 거부한다 — clamp 하면 오염이 0+ 스파이크로 위장된다."""
    outcome = run_ladder(winning_rate=0.89, winning_amount=87_000_000.0)

    assert outcome.stage is LadderStage.NEGATIVE_MARGIN
    assert outcome.negative_margin is not None
    assert outcome.negative_margin.margin == pytest.approx(0.89 - 0.89745)


def test_negative_margin_audit_survives_the_unity_gate() -> None:
    """``f == 1.0`` 로 떨어진 행도 Δ<0 감사는 남는다 — m1 의 반례를 볼 수 있어야 한다.

    개연 게이트를 먼저 걸면 "Δ<0 은 전부 f==1.0"이라는 명제의 반례가 될 수 있는 행이
    관측되기 전에 사라진다. 감사는 게이트 **이전** 하한율로 잰다.
    """
    outcome = run_ladder(award_floor_rate=1.0)

    assert outcome.stage is LadderStage.FLOOR_RATE_UNITY
    assert outcome.negative_margin is not None
    assert outcome.negative_margin.floor_rate_fraction == 1.0
    assert outcome.negative_margin.magnitude == pytest.approx(0.1)


def test_regime_label_is_reported_even_when_the_gate_excludes_the_row() -> None:
    """게이트 전 레짐 분포를 세려면 탈락 행의 라벨도 돌아와야 한다."""
    outcome = run_ladder(category="service", regime_text=NEGOTIATED_TEXT)

    assert outcome.stage is LadderStage.REGIME_EXCLUDED
    assert outcome.regime_label == "near_100"


def test_construction_negotiated_quote_stays_floor_bound() -> None:
    """#312 공사 수의견적 가드가 이 코퍼스에도 그대로 적용된다는 사실을 고정한다.

    같은 텍스트가 공종에 따라 다른 라벨을 받는다 — 하한이 해석된 공사 수의견적은
    ``near_100`` 이 아니라 ``floor_bound`` 다. 로더가 라이브와 **같은 규칙표**를 부르는
    한 이 비대칭은 백테스트 코퍼스 구성에도 그대로 나타나야 하고, 그 사실이 리포트의
    레짐 히스토그램을 읽는 전제다.
    """
    construction = run_ladder(regime_text=NEGOTIATED_TEXT)
    service = run_ladder(category="service", regime_text=NEGOTIATED_TEXT)

    assert construction.regime_label == "floor_bound"
    assert construction.row is not None
    assert service.regime_label == "near_100"


def test_loose_regime_gate_admits_regimes_the_classifier_could_not_confirm() -> None:
    """대조군이 회수하는 것은 "텍스트가 없는 공고"가 **아니다**.

    ``ambiguous`` 는 규칙표의 어느 신호도 서지 않아 fallback 으로 떨어진 라벨이고,
    실코퍼스의 714행은 **전부 제목·본문을 갖고 있다**. 그래서 이 테스트는 텍스트가
    있으면서 가격경쟁 단서만 없는 공고로 그 경로를 고정한다 — 초기 문안("텍스트 부재로
    탈락")은 실측상 거짓이었고 그 거짓 전제를 테스트가 고정하고 있었다.

    두 정책의 차이는 곧 **포함 가정의 크기**다: 대조군은 "가격경쟁이 아닌 것을 뺀다"가
    아니라 "가격경쟁이라고 **확정되지 않은** 것을 넣는다".
    """
    unconfirmed = "2026년도 시설 유지관리 사업 안내 공고문"
    strict = run_ladder(regime_text=unconfirmed)
    loose = run_ladder(regime_text=unconfirmed, gate="not_negotiated")

    assert strict.regime_label == AMBIGUOUS_REGIME_LABEL
    assert strict.stage is LadderStage.REGIME_EXCLUDED
    assert loose.row is not None
    assert loose.row.regime_label == AMBIGUOUS_REGIME_LABEL


def test_ladder_accounting_is_complete() -> None:
    """탈락 + 채택 = 후보. 침묵 제외가 있으면 이 항등식이 깨진다.

    회계가 새면 "표본이 얕다"(시간이 해법)와 "표본이 오염됐다"(데이터가 해법)를 리포트가
    구별하지 못한다 — 사다리의 존재 이유가 그 구별이다.
    """
    accumulator = _CorpusAccumulator()
    outcomes = [
        run_ladder(project_id=1),
        run_ladder(project_id=2, award_floor_rate=1.0),
        run_ladder(project_id=3, winning_amount=0.0),
        run_ladder(project_id=4, base_amount_basis="derived-yega"),
        run_ladder(project_id=5, category="service", regime_text=NEGOTIATED_TEXT),
    ]
    for project_id, outcome in enumerate(outcomes, start=1):
        assert accumulator.claim(project_id)
        accumulator.add(outcome)

    corpus = accumulator.build(regime_gate=DEFAULT_REGIME_GATE, as_of=None)

    assert corpus.candidate_count == len(outcomes)
    assert sum(corpus.ladder_counts.values()) + corpus.accepted_count == len(outcomes)
    assert corpus.accepted_count == 1
    # 같은 공고가 두 번 나오면 후보로 세지 않는다(중복 개찰 행 방어).
    assert accumulator.claim(1) is False


@pytest.mark.parametrize(
    ("opened_at", "expected"),
    [
        (datetime(2026, 1, 29, tzinfo=UTC), ERA_TIER_PRE_REVISION),
        # 시행일 당일은 신율이다(경계 포함).
        (datetime(2026, 1, 30, tzinfo=UTC), ERA_TIER_POST_REVISION),
        (datetime(2026, 7, 1, tzinfo=UTC), ERA_TIER_POST_REVISION),
    ],
)
def test_era_tier_boundary(opened_at: datetime, expected: str) -> None:
    assert resolve_era_tier(opened_at) == expected


def test_amount_band_follows_the_reliable_base_not_the_award_amount() -> None:
    """금액대는 기초금액(B) 축이다 — 낙찰가로 버킷하면 셀이 하한율과 교락한다."""
    outcome = run_ladder(base_amount=50_000_000.0, winning_amount=44_000_000.0)

    assert outcome.row is not None
    assert outcome.row.amount_band == "30m_100m"
