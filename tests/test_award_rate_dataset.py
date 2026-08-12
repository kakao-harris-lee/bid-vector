"""낙찰률 학습 행 로더 — 표본 정의(필터 4종)와 예비가격 컬럼 미열람 회귀 가드."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models.models import HistoricalData, Project
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
    issuing_agency: str | None = "시청",
    award_floor_rate: float | None = None,
    with_project: bool = True,
) -> None:
    """이력 한 건 + (기본값) 그 공고 행.

    ``issuing_agency`` 가 표본 정의의 축이므로 픽스처가 **명시적으로** 정한다. 기본값이
    피드 출처(값 있음)인 이유는 그것이 서빙이 마주하는 모집단이기 때문이고, 개찰결과
    피드로만 본 공고는 ``issuing_agency=None`` 으로 만든다.
    """
    if with_project:
        db.add(
            Project(
                id=project_id,
                title=f"AR-{project_id}",
                category="construction",
                issuing_agency=issuing_agency,
                award_floor_rate=award_floor_rate,
            )
        )
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


# --------------------------------------------------------------------------
# 피드 출처 필터 — 서빙 분포 정합(메커니즘 필터가 아니다)
# --------------------------------------------------------------------------


def test_feed_origin_filter_drops_opening_result_only_notices(test_db):
    """``issuing_agency`` 가 없는 행은 개찰결과 피드로만 본 공고 — 서빙이 마주치지 않는다."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=2))
    _insert(
        test_db,
        project_id=2,
        opened_at=_NOW - timedelta(days=1),
        issuing_agency=None,
    )
    rows = load_award_rate_rows(test_db, now=_NOW, feed_origin_only=True)
    assert len(rows) == 1
    assert rows[0].opened_at == _NOW - timedelta(days=2)


def test_disabling_the_feed_origin_filter_restores_the_previous_sample(test_db):
    """플래그를 끄면 필터 이전 표본 정의가 그대로 돌아온다(전후 비교 장치)."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=2))
    _insert(
        test_db, project_id=2, opened_at=_NOW - timedelta(days=1), issuing_agency=None
    )
    assert len(load_award_rate_rows(test_db, now=_NOW, feed_origin_only=False)) == 2


def test_rows_without_a_project_row_survive_when_the_filter_is_off(test_db):
    """Project 조인은 outer 다 — inner 였다면 필터를 꺼도 이 행이 조용히 사라진다."""
    _insert(
        test_db, project_id=7, opened_at=_NOW - timedelta(days=1), with_project=False
    )
    assert len(load_award_rate_rows(test_db, now=_NOW, feed_origin_only=False)) == 1
    assert load_award_rate_rows(test_db, now=_NOW, feed_origin_only=True) == []


def test_filter_default_follows_the_setting(test_db, monkeypatch):
    """인자를 주지 않으면 설정값을 따른다 — 인자는 그 설정을 우회하는 seam 이다."""
    from app.core.config import settings

    _insert(
        test_db, project_id=1, opened_at=_NOW - timedelta(days=1), issuing_agency=None
    )
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY", True
    )
    assert load_award_rate_rows(test_db, now=_NOW) == []
    monkeypatch.setattr(
        settings, "PRICE_PREDICTION_AWARD_RATE_GBM_FEED_ORIGIN_ONLY", False
    )
    assert len(load_award_rate_rows(test_db, now=_NOW)) == 1


# --------------------------------------------------------------------------
# 공시 낙찰하한율 — 라이브 가격 경로와 같은 정규화·개연 밴드
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (0.88, 0.88),
        (88.0, 0.88),  # percent 스케일도 라이브와 같은 규칙으로 fraction 이 된다
        (1.0, None),  # "예정가 전액 이상 투찰" — 하한으로 성립하지 않는다
        (0.0088, None),  # 스케일 오적재
        (None, None),
        (0.0, None),
    ],
)
def test_published_floor_uses_the_live_normalization_and_band(test_db, stored, expected):
    """학습이 본 값과 서빙이 넘기는 값이 같은 규칙을 통과해야 축이 갈리지 않는다."""
    _insert(
        test_db,
        project_id=1,
        opened_at=_NOW - timedelta(days=1),
        award_floor_rate=stored,
    )
    rows = load_award_rate_rows(test_db, now=_NOW)
    assert rows[0].published_floor_rate == expected


def test_missing_published_floor_stays_none_instead_of_zero(test_db):
    """미공시를 0 으로 접으면 "하한 0%" 라는 다른 주장이 학습 코퍼스에 들어간다."""
    _insert(test_db, project_id=1, opened_at=_NOW - timedelta(days=1))
    assert load_award_rate_rows(test_db, now=_NOW)[0].published_floor_rate is None


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
        # 공고 행은 위 삽입에서 이미 살아 있다(이력만 지웠다) — 같은 공고의 이력을
        # 예비가격만 바꿔 다시 넣는 것이 이 테스트의 대조군이다.
        with_project=False,
    )
    with_reserves = load_award_rate_rows(test_db, now=_NOW)

    assert plain == with_reserves
