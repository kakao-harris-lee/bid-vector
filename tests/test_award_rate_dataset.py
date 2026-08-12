"""낙찰률 학습 행 로더 — 표본 정의(필터 3종)와 예비가격 컬럼 미열람 회귀 가드."""

import json
from datetime import UTC, datetime, timedelta

from app.models.models import HistoricalData
from app.models.pipeline import TenderResult
from app.services.award_rate_dataset import load_award_rate_rows

_NOW = datetime(2026, 8, 12, tzinfo=UTC)
_BASE_AMOUNT = 100_000_000.0


def _insert(
    db,
    *,
    project_id: int,
    opened_at: datetime,
    winning_amount: float = 90_000_000.0,
    base_amount: float = _BASE_AMOUNT,
    base_amount_basis: str | None = "clean",
    base_amount_estimated: float | None = None,
    reserve_prices: list[float] | None = None,
    selected_numbers: list[int] | None = None,
) -> None:
    db.add(
        HistoricalData(
            project_id=project_id,
            notice_number=f"AR-{project_id}",
            category="construction",
            agency_name="시청",
            base_amount=base_amount,
            base_amount_basis=base_amount_basis,
            base_amount_estimated=base_amount_estimated,
            opened_at=opened_at,
            reserve_prices=json.dumps(reserve_prices or []),
            selected_numbers=json.dumps(selected_numbers or []),
        )
    )
    db.add(
        TenderResult(
            project_id=project_id,
            is_current=True,
            winning_amount=winning_amount,
        )
    )
    db.commit()


def test_only_evidenced_denominators_become_training_rows(test_db):
    """``ok`` 만 받는다 — 근거 없는 분모(base-fallback)는 카테고리와 교락한 오염이다."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=3))
    # 오염 태그 + 복구 추정치 없음 → base-fallback → ok-unverified-base
    _insert(
        test_db,
        project_id=2,
        opened_at=_NOW - timedelta(days=2),
        base_amount_basis="derived-yega",
    )
    rows = load_award_rate_rows(test_db, now=_NOW)
    assert [row.denominator_source for row in rows] == ["clean-base"]
    assert rows[0].value == 0.9
    assert rows[0].amount == _BASE_AMOUNT


def test_recovered_base_rows_are_kept_and_tagged(test_db):
    """복구 추정 층도 학습 표본이다 — 다만 출처가 남아 통제 변수로 쓰인다."""
    _insert(
        test_db,
        project_id=1,
        opened_at=_NOW - timedelta(days=1),
        base_amount_basis="derived-yega",
        base_amount_estimated=_BASE_AMOUNT,
    )
    rows = load_award_rate_rows(test_db, now=_NOW)
    assert [row.denominator_source for row in rows] == ["reserve-estimate"]


def test_rows_without_an_award_are_dropped(test_db):
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=1), winning_amount=0.0)
    assert load_award_rate_rows(test_db, now=_NOW) == []


def test_future_opening_times_are_excluded(test_db):
    """개찰이 미래인데 라벨이 있는 행은 적재 사고 — 값을 고치지 않고 관측에서만 뺀다."""
    _insert(test_db, project_id=1, opened_at=_NOW + timedelta(days=1))
    _insert(test_db, project_id=2, opened_at=_NOW - timedelta(days=1))
    rows = load_award_rate_rows(test_db, now=_NOW)
    assert len(rows) == 1
    assert rows[0].opened_at < _NOW


def test_cutoff_excludes_rows_opened_at_or_after_it(test_db):
    cutoff = _NOW - timedelta(days=5)
    _insert(test_db, project_id=1, opened_at=cutoff - timedelta(hours=1))
    _insert(test_db, project_id=2, opened_at=cutoff)
    _insert(test_db, project_id=3, opened_at=cutoff + timedelta(hours=1))
    rows = load_award_rate_rows(test_db, cutoff_at=cutoff, now=_NOW)
    assert len(rows) == 1
    assert rows[0].opened_at < cutoff


def test_rows_are_sorted_by_opening_time(test_db):
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=1))
    _insert(test_db, project_id=2, opened_at=_NOW - timedelta(days=5))
    _insert(test_db, project_id=3, opened_at=_NOW - timedelta(days=3))
    rows = load_award_rate_rows(test_db, now=_NOW)
    assert [row.opened_at for row in rows] == sorted(row.opened_at for row in rows)


def test_reserve_columns_do_not_reach_the_training_rows(test_db):
    """대상 공고의 예비가격·추첨번호가 달라도 학습 행은 동일하다 (train/serve skew 가드).

    로더가 그 컬럼을 읽지 않는다는 것을 **행동**으로 고정한다 — 피처 조립기의 시그니처
    가드(tests/test_award_rate_features.py)와 함께 두 층에서 배제를 잠근다.
    """
    opened_at = _NOW - timedelta(days=1)
    _insert(test_db, project_id=1, opened_at=opened_at)
    plain = load_award_rate_rows(test_db, now=_NOW)

    test_db.query(TenderResult).delete()
    test_db.query(HistoricalData).delete()
    test_db.commit()
    _insert(
        test_db,
        project_id=1,
        opened_at=opened_at,
        reserve_prices=[_BASE_AMOUNT * (0.97 + 0.004 * index) for index in range(15)],
        selected_numbers=[1, 5, 10, 15],
    )
    with_reserves = load_award_rate_rows(test_db, now=_NOW)

    assert plain == with_reserves
