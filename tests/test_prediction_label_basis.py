"""라벨 basis 정합 — 새 라벨의 계약과 **기존 라벨 경로 불변**의 회귀 가드.

이 파일이 지키는 두 가지
------------------------
1. **red line — 기존 라벨 해석 경로 불변.** ``_resolve_bid_rate`` 의 tier 우선순위와
   ``_normalize_bid_rate_value`` 의 스케일·창 판정을 값 표로 얼려 둔다. 새 라벨을 붙이는
   변경이 기존 학습 라벨을 한 톨도 움직이지 않았음을, 그리고 앞으로도 움직이면 실패함을
   여기서 증명한다. 왜 얼리는가: #195 가 발주처 밴드에 이미 E[사정률]을 곱하므로
   (``app/domain/basis_conversion``), 라벨까지 같이 환산하면 사정률이 두 번 반영된다.
2. **새 라벨의 축.** ``award_rate_label`` 은 저장 ``bid_rate`` 와 **다른 값일 수 있고**,
   그 차이가 곧 이 작업의 근거다(저장 라벨은 예정가-relative 와 기초금액-relative 가
   섞여 있다). 두 라벨이 갈리는 행에서 새 라벨이 기초금액-relative 로 일관됨을 고정한다.

값 표는 DB 없이 돈다(§4.7.4). 시리즈 계약 테스트만 ``test_db`` 를 쓴다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.core.time import utc_now
from app.models.models import HistoricalData, Project, TenderResult
from app.services.base_amount_basis import (
    BASIS_CLEAN,
    BASIS_DERIVED_YEGA,
    BASIS_SUSPECT_RATIO,
)
from app.services.floor_shortfall import assessment_rate_from_opening
from app.services.prediction_dataset import PredictionDatasetService

SERVICE = PredictionDatasetService()

# 직렬화 시리즈 포인트의 키 집합 — 소비자 계약이다. 새 라벨(``award_rate_label``) 하나만
# 늘고 기존 키는 하나도 사라지지 않았음을 여기서 못 박는다(추가만, 제거 없음).
EXPECTED_SERIES_KEYS = frozenset(
    {
        "historical_data_id",
        "project_id",
        "notice_number",
        "category",
        "business_group",
        "business_group_source",
        "business_type_code",
        "business_type_label",
        "agency_name",
        "base_amount",
        "base_amount_basis",
        "predicted_price",
        "bid_rate",
        "bid_rate_source",
        "award_rate_label",
        "reserve_prices",
        "selected_numbers",
        "opened_at",
        "tender_result_id",
        "winning_amount",
        "winning_rate",
        "tender_result_status",
        "tender_result_announced_at",
    }
)


def _record(
    *,
    bid_rate=0.0,
    base_amount=0.0,
    base_amount_basis=None,
    base_amount_estimated=None,
    predicted_price=0.0,
):
    """``_resolve_bid_rate`` 가 실제로 읽는 속성만 가진 최소 스텁(ORM·DB 불필요)."""
    return SimpleNamespace(
        bid_rate=bid_rate,
        base_amount=base_amount,
        base_amount_basis=base_amount_basis,
        base_amount_estimated=base_amount_estimated,
        predicted_price=predicted_price,
    )


def _result(*, winning_rate=0.0, winning_amount=0.0):
    return SimpleNamespace(winning_rate=winning_rate, winning_amount=winning_amount)


# (설명, record, tender_result, allow_predicted_price_fallback, 기대 (rate, source))
#
# 기대값은 이 변경 **이전** 구현의 실제 출력이다(특성화 — 정답이 아니라 현행 동작).
_RESOLVE_BID_RATE_TABLE = [
    (
        "tier1 저장 라벨",
        _record(bid_rate=0.9),
        None,
        True,
        (0.9, "historical_data"),
    ),
    (
        "tier1 하단 경계(0.5 포함)",
        _record(bid_rate=0.5),
        None,
        True,
        (0.5, "historical_data"),
    ),
    (
        "tier1 상단 경계(1.5 포함)",
        _record(bid_rate=1.5),
        None,
        True,
        (1.5, "historical_data"),
    ),
    (
        "tier1 창 아래 → tier2 낙찰률",
        _record(bid_rate=0.499999),
        _result(winning_rate=0.88),
        True,
        (0.88, "tender_result_winning_rate"),
    ),
    (
        "tier1 창 위 → tier2 percent 스케일 낙찰률",
        _record(bid_rate=1.500001),
        _result(winning_rate=87.5),
        True,
        (0.875, "tender_result_winning_rate"),
    ),
    (
        "tier2 창 밖(200%) → tier3 금액비",
        _record(base_amount=100_000_000.0, base_amount_basis=BASIS_CLEAN),
        _result(winning_rate=200.0, winning_amount=88_000_000.0),
        True,
        (0.88, "tender_result_winning_amount"),
    ),
    (
        "tier3 금액비 — 오염 base 는 복구 추정치로 대체",
        _record(
            base_amount=113_636_363.6,
            base_amount_basis=BASIS_DERIVED_YEGA,
            base_amount_estimated=100_000_000.0,
        ),
        _result(winning_amount=88_000_000.0),
        True,
        (0.88, "tender_result_winning_amount"),
    ),
    (
        "tier3 금액비 창 밖 → tier4 예측가",
        _record(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            predicted_price=91_000_000.0,
        ),
        _result(winning_amount=200_000_000.0),
        True,
        (0.91, "predicted_price"),
    ),
    (
        "tier4 예측가",
        _record(base_amount=100_000_000.0, predicted_price=91_000_000.0),
        None,
        True,
        (0.91, "predicted_price"),
    ),
    (
        "tier4 차단(explicit_bid_rate_only)",
        _record(base_amount=100_000_000.0, predicted_price=91_000_000.0),
        None,
        False,
        (None, None),
    ),
    (
        "라벨 없음",
        _record(),
        None,
        True,
        (None, None),
    ),
]


@pytest.mark.parametrize(
    ("record", "tender_result", "allow_fallback", "expected"),
    [pytest.param(*case[1:], id=case[0]) for case in _RESOLVE_BID_RATE_TABLE],
)
def test_resolve_bid_rate_tier_priority_is_frozen(
    record, tender_result, allow_fallback, expected
):
    """red line: tier 우선순위와 각 tier 의 출처 문자열이 그대로다."""
    assert (
        SERVICE._resolve_bid_rate(
            record,
            tender_result=tender_result,
            allow_predicted_price_fallback=allow_fallback,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0.88, 0.88),
        (87.5, 0.875),
        (0.5, 0.5),  # 하단 경계 — 포함
        (1.5, 1.5),  # 상단 경계 — 포함(스케일 판별도 여기서 fraction 유지)
        (0.499999, None),  # 창 아래
        (1.500001, None),  # /100 되어 0.015 → 창 아래
        (200.0, None),  # /100 되어 2.0 → 창 위
        (0.0, None),
        (-1.0, None),
        (None, None),
        ("", None),
        ("abc", None),
    ],
)
def test_normalize_bid_rate_value_window_is_frozen(raw, expected):
    """red line: 스케일 판별 → 유효 창 → 반올림의 결합 동작이 그대로다."""
    assert SERVICE._normalize_bid_rate_value(raw) == expected


# ---------------------------------------------------------------------------
# ``assessment_rate_from_opening`` 특성화 값표 — **라이브 표시 경로의 회귀 가드**.
#
# 이 함수가 만든 사정률 표본이 투찰서에 뜨는 하한 미달 빈도의 분모가 된다
# (``bid_summary`` → ``notice_floor_shortfall`` → ``build_floor_shortfall_estimate``).
# 이 PR 이 금액비 계산을 라벨 커널로 위임했으므로 위임 **이전 구현의 귀결**을 여기 얼린다.
# 실 코퍼스 대조는 일회성이라 사라지지만 이 표는 남아, 이 함수가 나중에 바뀌면 잡힌다.
#
# 표가 덮는 경계(팀리드 지정 + 리뷰어 관찰):
#   · 분자   None / 0 / 음수 / NaN / +inf / 문자열
#   · 분모   clean / 미태깅(None) / non-clean+복구추정치 / non-clean+복구실패 / 0 / None
#            / NaN / ±inf
#   · 보고율 없음 / fraction / percent 스케일 / 창 밖
#   · 독립성 복수예비가격 0개 / 4개(경계 아래) / 5개(경계) / 15개
#
# 이 함수는 **clean 분모만** 받는다. 커널이 값을 내주는 ``reserve-estimate``·
# ``base-fallback`` 도 여기서는 버려지는데, 그 거부는 위임 이전과 동일하다(구 구현도
# ``reliable.source is not CLEAN_BASE`` 에서 None 을 냈다). 학습 라벨과 갈리는 지점이지만
# 이 PR 이 만든 갈림이 아니다.
# ---------------------------------------------------------------------------
_RESERVES_15 = json.dumps([100_000_000.0 + index for index in range(15)])
# 독립 예정가 증거로 인정하는 최소 개수는 5(``MIN_RESERVE_PRICES_FOR_INDEPENDENT_RATE``).
_RESERVES_5 = json.dumps([100_000_000.0 + index for index in range(5)])
_RESERVES_4 = json.dumps([100_000_000.0 + index for index in range(4)])

# 분모 쪽을 고정한 기본 행 — 분자·보고율만 흔드는 케이스가 이것을 펼쳐 쓴다.
_CLEAN = dict(
    base_amount=100_000_000.0,
    base_amount_basis=BASIS_CLEAN,
    base_amount_estimated=None,
    reserve_prices=None,
)

# (설명, kwargs, 기대값)
_ASSESSMENT_RATE_TABLE = [
    (
        "clean base + 독립 보고율 → 사정률",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        0.875 / 0.87,
    ),
    (
        "percent 스케일 보고율도 같은 결과",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=87.0,
        ),
        0.875 / 0.87,
    ),
    (
        "보고율=금액비인데 복수예비가격 증거 있음 → 사정률 1",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_15,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        1.0,
    ),
    (
        "보고율=금액비인데 증거 없음 → 파생 의심으로 버림",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        None,
    ),
    (
        "복구 추정치는 이 용도에서 거부(꼬리 오염)",
        dict(
            base_amount=113_636_363.6,
            base_amount_basis=BASIS_DERIVED_YEGA,
            base_amount_estimated=100_000_000.0,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "오염 태그 없는 base 폴백도 거부",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=None,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "낙찰 금액 없음",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=0.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "보고 낙찰률 없음",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=None,
        ),
        None,
    ),
    (
        "금액비가 유효 창 밖",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=200_000_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    # ── 분자 경계 ────────────────────────────────────────────────────────────
    (
        "낙찰 금액 None",
        dict(**_CLEAN, winning_amount=None, winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 음수",
        dict(**_CLEAN, winning_amount=-1.0, winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 NaN — <=0 은 통과하지만 유효 창에서 탈락",
        dict(**_CLEAN, winning_amount=float("nan"), winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 +inf — 비가 inf 라 유효 창 밖",
        dict(**_CLEAN, winning_amount=float("inf"), winning_rate=0.87),
        None,
    ),
    (
        "낙찰 금액 숫자 문자열 — optional_float 이 받는다",
        dict(**_CLEAN, winning_amount="87500000", winning_rate=0.87),
        0.875 / 0.87,
    ),
    (
        "낙찰 금액 비수치 문자열",
        dict(**_CLEAN, winning_amount="abc", winning_rate=0.87),
        None,
    ),
    # ── 분모 경계 ────────────────────────────────────────────────────────────
    (
        "base 0 — 양수 분모 없음",
        dict(
            base_amount=0.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base None",
        dict(
            base_amount=None,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base NaN — _positive_or_none 이 막는다",
        dict(
            base_amount=float("nan"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        # 리뷰어 관찰: ``_positive_or_none`` 은 NaN 만 막고 ``+inf`` 는 통과시킨다
        # (``inf > 0`` 이 참). 그 값이 분모가 되면 비가 0.0 이라 유효 창에서 탈락하므로
        # 라벨로 새지 않는다 — 고치는 대신 그 거동을 여기서 고정한다.
        "base +inf — 분모로 통과하지만 비가 0.0 이라 창 밖",
        dict(
            base_amount=float("inf"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "base -inf — 양수가 아니라 분모 없음",
        dict(
            base_amount=float("-inf"),
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    (
        "non-clean + 복구 추정치 없음 → base 폴백도 거부",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_SUSPECT_RATIO,
            base_amount_estimated=None,
            reserve_prices=None,
            winning_amount=87_500_000.0,
            winning_rate=0.87,
        ),
        None,
    ),
    # ── 보고율·독립성 경계 ───────────────────────────────────────────────────
    (
        "보고율이 창 밖(200%) → 정규화 후에도 2.0",
        dict(**_CLEAN, winning_amount=87_500_000.0, winning_rate=200.0),
        None,
    ),
    (
        "보고율=금액비 + 복수예비가격 4개(경계 아래) → 독립 증거 부족",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_4,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        None,
    ),
    (
        "보고율=금액비 + 복수예비가격 5개(경계) → 독립 인정",
        dict(
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            base_amount_estimated=None,
            reserve_prices=_RESERVES_5,
            winning_amount=87_500_000.0,
            winning_rate=0.875,
        ),
        1.0,
    ),
]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [pytest.param(*case[1:], id=case[0]) for case in _ASSESSMENT_RATE_TABLE],
)
def test_assessment_rate_from_opening_is_frozen(kwargs, expected):
    """red line: 사정률 표본 도출이 커널 위임 전후로 같다."""
    assert assessment_rate_from_opening(**kwargs) == expected


def _seed_award_row(
    test_db,
    *,
    notice_number: str,
    stored_bid_rate: float,
    base_amount: float,
    winning_amount: float,
    winning_rate: float,
    base_amount_basis: str | None = BASIS_CLEAN,
):
    """한 공고의 (HistoricalData, TenderResult) 쌍을 심는다."""
    now = utc_now()
    project = Project(
        title=f"Label basis {notice_number}",
        description="label basis alignment fixture",
        requirements="none",
        budget_estimate=base_amount,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add_all(
        [
            HistoricalData(
                project_id=project.id,
                notice_number=notice_number,
                agency_name="한국수산자원공단",
                category="construction",
                base_amount=base_amount,
                base_amount_basis=base_amount_basis,
                predicted_price=0.0,
                bid_rate=stored_bid_rate,
                opened_at=now - timedelta(days=1),
            ),
            TenderResult(
                project_id=project.id,
                winning_company="낙찰사",
                winning_amount=winning_amount,
                winning_rate=winning_rate,
                result_status="awarded",
                announced_at=now - timedelta(hours=12),
            ),
        ]
    )
    test_db.commit()
    return project


def test_series_point_adds_label_without_dropping_existing_keys(test_db):
    """추가만, 제거 없음 — 시리즈 포인트의 키 집합 계약."""
    _seed_award_row(
        test_db,
        notice_number="LABEL-KEYS-1",
        stored_bid_rate=0.88,
        base_amount=100_000_000.0,
        winning_amount=87_500_000.0,
        winning_rate=0.88,
    )

    series = PredictionDatasetService().load_historical_series(
        test_db, category="construction", limit=10
    )

    assert len(series) == 1
    assert set(series[0]) == EXPECTED_SERIES_KEYS


def test_new_label_is_base_relative_where_stored_label_is_yega_relative(test_db):
    """핵심 대비: 저장 라벨이 예정가-relative 인 행에서 새 라벨은 기초금액-relative 다.

    수집은 실 기초금액을 확보하지 못하면 KONEPS ``sucsfbidRate``(=낙찰가/예정가)를 그대로
    ``bid_rate`` 에 넣는다(``app/services/koneps/scsbid.py``). 여기서는 그 상황을 재현한다:
    저장 라벨 0.88 은 예정가 기준이고, 같은 개찰의 기초금액 기준 낙찰률은 0.875 다
    (사정률 ≈ 1.00571). 서빙이 곱하는 축은 기초금액이므로 두 값을 섞으면 그 차이만큼
    어긋난다.
    """
    _seed_award_row(
        test_db,
        notice_number="LABEL-BASIS-1",
        stored_bid_rate=0.88,  # 예정가-relative (sucsfbidRate 그대로)
        base_amount=100_000_000.0,  # 실 기초금액
        winning_amount=87_500_000.0,
        winning_rate=0.88,
    )

    series = PredictionDatasetService().load_historical_series(
        test_db, category="construction", limit=10
    )
    point = series[0]

    # 기존 라벨은 그대로 저장값(tier1)이다 — 이 PR 이 건드리지 않은 축.
    assert point["bid_rate"] == 0.88
    assert point["bid_rate_source"] == "historical_data"

    # 새 라벨은 낙찰가 ÷ 신뢰 기초금액이라 다른 값이고, 그 사실이 basis 로 신고된다.
    label = point["award_rate_label"]
    assert label["value"] == 0.875
    assert label["status"] == "ok"
    assert label["numerator_basis"] == "winning_amount"
    assert label["denominator_basis"] == "base_amount"
    assert label["denominator_source"] == "clean-base"
    assert label["denominator_value"] == 100_000_000.0
    assert label["base_amount_basis"] == BASIS_CLEAN


def test_label_reports_reason_when_award_amount_is_missing(test_db):
    """개찰 금액이 없으면 값 대신 사유가 남는다 — 커버리지 손실을 사후에 셀 수 있게."""
    now = utc_now()
    project = Project(
        title="Label basis no award",
        description="label basis fixture",
        requirements="none",
        budget_estimate=100_000_000.0,
        category="construction",
    )
    test_db.add(project)
    test_db.flush()
    test_db.add(
        HistoricalData(
            project_id=project.id,
            notice_number="LABEL-NOAWARD-1",
            agency_name="한국수산자원공단",
            category="construction",
            base_amount=100_000_000.0,
            base_amount_basis=BASIS_CLEAN,
            predicted_price=0.0,
            bid_rate=0.88,
            opened_at=now - timedelta(days=1),
        )
    )
    test_db.commit()

    series = PredictionDatasetService().load_historical_series(
        test_db, category="construction", limit=10
    )
    label = series[0]["award_rate_label"]

    assert series[0]["bid_rate"] == 0.88  # 기존 라벨은 여전히 있다
    assert label["value"] is None
    assert label["status"] == "no-winning-amount"
    assert label["denominator_source"] == "clean-base"


def test_unclassified_base_row_is_excluded_by_status_ok_filter(test_db):
    """시계열에 실린 상태로도 ``status == "ok"`` 필터가 근거 없는 분모를 걸러낸다.

    커널 단위 계약(``tests/test_award_rate_label.py``)이 실제 직렬화 경로를 타고 나온
    payload 에서도 성립하는지를 확인한다. 이 PR 이 요약 카운터를 싣지 않으므로(설계 래칫의
    파일 LOC 밴드 — 상세는 PR 본문) 분모의 신뢰 축은 이 행 단위 블록으로만 노출된다.
    """
    _seed_award_row(
        test_db,
        notice_number="LABEL-FALLBACK-1",
        stored_bid_rate=0.9,
        base_amount=120_000_000.0,
        winning_amount=108_000_000.0,
        winning_rate=0.9,
        base_amount_basis=None,
    )

    series = PredictionDatasetService().load_historical_series(
        test_db, category="construction", limit=10
    )
    label = series[0]["award_rate_label"]

    assert label["value"] == 0.9  # 값 자체는 난다
    assert label["status"] == "ok-unverified-base"  # 그러나 ``ok`` 필터에는 안 걸린다
    assert label["denominator_basis"] is None  # 축을 주장하지 않는다
    assert label["denominator_source"] == "base-fallback"
    assert label["base_amount_basis"] is None
