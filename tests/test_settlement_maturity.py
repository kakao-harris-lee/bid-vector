"""정산 성숙도 커널 — 주 경계·비율·빈 구간 계약.

성숙도는 embargo 의 유일한 입력이므로, 여기서 틀리면 게이트가 "잰 것"과 "잰 줄 아는 것"이
달라진다. 실 코퍼스 수치는 백테스트 리포트가 내고 이 파일은 규칙만 고정한다.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.time import KST
from app.domain.settlement_maturity import (
    MATURITY_WINDOW_LENGTH,
    MaturityWindow,
    SettlementObservation,
    build_weekly_maturity,
    week_start_utc,
)


def _kst(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=KST)


def test_week_starts_on_the_korean_monday_not_the_utc_one():
    """개찰은 한국 업무시간에 일어난다. UTC 자정 경계를 쓰면 월요일 오전 개찰이 전주로
    밀려 성숙도가 엉뚱한 구간에 붙는다."""
    monday_morning = _kst(2026, 8, 10, 10)  # 월 10:00 KST = 일 01:00 UTC
    assert week_start_utc(monday_morning) == _kst(2026, 8, 10).astimezone(UTC)

    sunday_evening = _kst(2026, 8, 16, 23)
    assert week_start_utc(sunday_evening) == _kst(2026, 8, 10).astimezone(UTC)

    next_monday = _kst(2026, 8, 17, 0)
    assert week_start_utc(next_monday) == _kst(2026, 8, 17).astimezone(UTC)


def test_windows_are_contiguous_weeks_and_do_not_overlap():
    observations = [
        SettlementObservation(opened_at=_kst(2026, 8, 10, 10), settled=True),
        SettlementObservation(opened_at=_kst(2026, 8, 17, 10), settled=False),
    ]
    windows = build_weekly_maturity(observations)

    assert [window.start for window in windows] == sorted(
        window.start for window in windows
    )
    assert windows[0].end == windows[1].start
    assert all(
        window.end - window.start == MATURITY_WINDOW_LENGTH for window in windows
    )


def test_maturity_is_the_settled_share_of_what_opened():
    observations = [
        SettlementObservation(opened_at=_kst(2026, 8, 10, 10), settled=index < 7)
        for index in range(10)
    ]
    (window,) = build_weekly_maturity(observations)

    assert window.opened_count == 10
    assert window.settled_count == 7
    assert window.maturity == pytest.approx(0.7)


def test_weeks_without_observations_are_absent_not_zero():
    """빈 주를 0/0 으로 채우면 "개찰이 없었다"와 "하나도 정산되지 않았다"가 같은 행으로
    보인다. 두 상태는 다른 사실이다."""
    observations = [
        SettlementObservation(opened_at=_kst(2026, 8, 10, 10), settled=True),
        SettlementObservation(opened_at=_kst(2026, 8, 24, 10), settled=True),
    ]
    windows = build_weekly_maturity(observations)

    assert len(windows) == 2
    assert windows[1].start - windows[0].start == timedelta(days=14)


def test_empty_window_is_not_mature():
    """분모 0을 1.0 으로 보면 관측이 없는 구간이 가장 성숙한 구간이 된다."""
    window = MaturityWindow(
        start=_kst(2026, 8, 10).astimezone(UTC),
        end=_kst(2026, 8, 17).astimezone(UTC),
        opened_count=0,
        settled_count=0,
    )
    assert window.maturity == 0.0


def test_window_membership_includes_the_start_and_excludes_the_end():
    """이 반개구간 규칙이 인접 창의 겹침 0과 학습/평가 배타성을 동시에 만든다."""
    window = MaturityWindow(
        start=_kst(2026, 8, 10).astimezone(UTC),
        end=_kst(2026, 8, 17).astimezone(UTC),
        opened_count=1,
        settled_count=1,
    )
    assert window.contains(window.start) is True
    assert window.contains(window.end) is False
    assert window.contains(window.end - timedelta(microseconds=1)) is True
    assert window.contains(window.start - timedelta(microseconds=1)) is False


def test_naive_datetimes_are_read_as_utc():
    """SQLite 는 naive, Postgres 는 aware 를 낸다 — 경계가 두 종류를 섞어 죽으면 안 된다."""
    window = MaturityWindow(
        start=datetime(2026, 8, 10, tzinfo=UTC),
        end=datetime(2026, 8, 17, tzinfo=UTC),
        opened_count=1,
        settled_count=1,
    )
    assert window.contains(datetime(2026, 8, 12)) is True
    naive = build_weekly_maturity(
        [SettlementObservation(opened_at=datetime(2026, 8, 12), settled=True)]
    )
    assert naive[0].opened_count == 1
