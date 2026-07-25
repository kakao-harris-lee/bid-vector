"""홀드아웃 리포트의 기관 축·품질 플래그 섹션 빌더(집계 함수 주입형).

``scripts/backtest_latest_award_holdouts.py`` 는 DB 조회와 예측 실행을 하는 얇은
오케스트레이터로 두고, "평가된 행 목록 → 리포트 섹션" 변환은 여기서 순수 함수로
수행한다(§4.5.5 위임 / §4.7.4 I/O-계산 분리).

집계 자체(``aggregate``: sample_count·평균 오차·threshold 카운트)는 스크립트가 이미
소유한 단일 출처라 **주입**받는다(§4.7.3) — 여기서 재구현하면 두 표면의 지표 정의가
갈라진다. 덕분에 이 모듈의 테스트는 가짜 ``aggregate_fn`` 하나로 DB·예측 없이 돈다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from app.ai.holdout_grouping import (
    AGENCY_UNKNOWN_KEY,
    DEFAULT_MIN_AGENCY_SAMPLES,
    RESERVED_AGENCY_KEYS,
    bucket_small_agencies,
)
from app.ai.holdout_quality import CLEAN_FLAG_LABEL

# 평가된 행 목록을 요약 dict 로 접는 집계 함수의 계약(주입 지점).
AggregateFn = Callable[[list[dict[str, Any]]], dict[str, Any]]

# 리포트 행에서 읽는 필드 이름(스크립트가 쓰는 키와 한 곳에서 맞춘다).
AGENCY_KEY_FIELD = "agency_group"
AGENCY_DISPLAY_FIELD = "agency_display"
QUALITY_FLAGS_FIELD = "data_quality_flags"

# worst-case 정렬에서 지표가 없는 버킷을 맨 뒤로 보내는 sentinel. 정렬 키는 오차를
# 부호 반전해 쓰므로(-inf → +inf) 실제 오차값이 아무리 커도 이 버킷보다 앞선다.
_MISSING_ERROR_SORT_KEY = float("-inf")


def _order_buckets(
    buckets: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    """표본 수 내림차순 → 키 오름차순으로 정렬된 버킷 dict 를 돌려준다."""
    return dict(
        sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    )


def _recommended_error(summary: dict[str, Any]) -> float | None:
    recommended = summary.get("recommended") or {}
    value = recommended.get("mean_absolute_amount_error_pct")
    return float(value) if isinstance(value, (int, float)) else None


def build_agency_axis_report(
    rows: Sequence[dict[str, Any]],
    *,
    aggregate_fn: AggregateFn,
    min_samples: int = DEFAULT_MIN_AGENCY_SAMPLES,
    worst_limit: int = 10,
) -> dict[str, Any]:
    """기관 축 분할 집계 + worst-case 기관 식별 섹션을 만든다.

    소표본 기관은 ``_etc`` 로 합산되지만 **침묵 제외가 아니다** — 합산된 기관 수와
    표본 수가 ``summary`` 에 남고, ``_etc`` 자체도 하나의 버킷으로 집계된다.
    worst 목록은 ``_etc``/``_unknown`` 같은 잔여 버킷을 숨기지 않고 ``is_residual``
    로 표시만 한다(단일 기관의 성적이 아니므로 소비자가 구분할 수 있어야 한다).
    """
    keys = [str(row.get(AGENCY_KEY_FIELD) or AGENCY_UNKNOWN_KEY) for row in rows]
    bucketing = bucket_small_agencies(keys, min_samples=min_samples)

    buckets: dict[str, list[dict[str, Any]]] = {}
    displays: dict[str, str] = {}
    for row, key in zip(rows, keys):
        bucket_key = bucketing.bucket_for(key)
        buckets.setdefault(bucket_key, []).append(row)
        if bucket_key not in RESERVED_AGENCY_KEYS:
            displays.setdefault(
                bucket_key, str(row.get(AGENCY_DISPLAY_FIELD) or bucket_key)
            )

    ordered = _order_buckets(buckets)
    by_agency = {key: aggregate_fn(bucket_rows) for key, bucket_rows in ordered.items()}

    scored = [(key, summary, _recommended_error(summary)) for key, summary in by_agency.items()]
    # 오차 내림차순(worst 우선), 지표 없는 버킷은 sentinel 로 맨 뒤, 동점은 키 오름차순.
    # 오차만 부호를 뒤집어 오름차순 정렬한다 — ``reverse=True`` 를 쓰면 튜플 전체가
    # 뒤집혀 tie-break 까지 내림차순이 되므로 쓰지 않는다.
    ranked = sorted(
        scored,
        key=lambda item: (
            -(item[2] if item[2] is not None else _MISSING_ERROR_SORT_KEY),
            item[0],
        ),
    )
    worst_agencies = [
        {
            "agency": key,
            "agency_display": displays.get(key, key),
            "is_residual": key in RESERVED_AGENCY_KEYS,
            "sample_count": summary.get("target_count"),
            "mean_absolute_amount_error_pct": mean_error,
        }
        for key, summary, mean_error in ranked[: max(0, int(worst_limit or 0))]
    ]

    return {
        "summary": {
            "min_samples": bucketing.min_samples,
            "distinct_agency_count": bucketing.distinct_agency_count,
            "reported_agency_count": bucketing.reported_agency_count,
            "etc_agency_count": bucketing.etc_agency_count,
            "etc_sample_count": bucketing.etc_sample_count,
            "unknown_sample_count": bucketing.counts.get(AGENCY_UNKNOWN_KEY, 0),
            "evaluated_sample_count": len(rows),
        },
        "agency_displays": displays,
        "by_agency": by_agency,
        "worst_agencies": worst_agencies,
    }


def _flag_buckets(
    rows: Sequence[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(플래그별 버킷, 플래그 있는 행, 플래그 없는 행)."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    flagged: list[dict[str, Any]] = []
    flag_free: list[dict[str, Any]] = []
    for row in rows:
        flags = [str(flag) for flag in (row.get(QUALITY_FLAGS_FIELD) or [])]
        if flags:
            flagged.append(row)
        else:
            flag_free.append(row)
            flags = [CLEAN_FLAG_LABEL]
        for flag in flags:
            buckets.setdefault(flag, []).append(row)
    return buckets, flagged, flag_free


def build_quality_flag_report(
    rows: Sequence[dict[str, Any]],
    *,
    aggregate_fn: AggregateFn,
    evaluated_rows: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """품질 플래그별 집계 + 플래그 제외 전/후 오차 비교 섹션을 만든다.

    Args:
        rows: **집계 스코프** 행. 호출부는 basis-clean 으로 선필터한 행을 넘긴다
            (오차 지표가 오염 표본에 흔들리지 않게 하는 기존 설계).
        evaluated_rows: 선필터 **전** 전체 평가 행. 생략하면 ``rows`` 와 같다고 본다.

    왜 두 스코프를 다 받는가 — 집계 스코프는 clean-only 라서 거기서 센
    ``base_basis_contaminated`` 는 **구조적으로 항상 0**이다. 그 숫자만 리포트에
    실으면 "오염 0건"으로 오독되지만 실제 저장 데이터의 오염 비율은 ~66%다(#199).
    그래서 오차 지표는 설계대로 집계 스코프를 유지하되, 건수는 전체 평가 행 기준
    ``evaluated_flag_counts`` 를 함께 싣고 ``scope`` 에 제외 건수를 명시한다.

    ``by_flag`` 는 한 행이 여러 플래그를 가지면 각 플래그 버킷에 중복 계상된다
    (플래그별 "이 문제를 가진 표본의 성적"을 보는 뷰). 겹치지 않는 분할이 필요한
    비교는 ``partition`` 쪽(all / flag_free / flagged)을 쓴다.
    """
    evaluated = list(rows) if evaluated_rows is None else list(evaluated_rows)
    buckets, flagged, flag_free = _flag_buckets(rows)
    evaluated_buckets, _, _ = _flag_buckets(evaluated)

    ordered = _order_buckets(buckets)
    return {
        "scope": {
            "aggregated_count": len(rows),
            "evaluated_count": len(evaluated),
            "excluded_from_aggregation": len(evaluated) - len(rows),
            "note": (
                "flag_counts/by_flag/partition are computed on the AGGREGATION scope "
                "(clean-only by default), so base_basis_contaminated is 0 there by "
                "construction. See evaluated_flag_counts for all evaluated targets."
            ),
        },
        "flag_counts": {key: len(bucket) for key, bucket in ordered.items()},
        "evaluated_flag_counts": {
            key: len(bucket) for key, bucket in _order_buckets(evaluated_buckets).items()
        },
        "by_flag": {key: aggregate_fn(bucket) for key, bucket in ordered.items()},
        "partition": {
            "all": aggregate_fn(list(rows)),
            "flag_free": aggregate_fn(flag_free),
            "flagged": aggregate_fn(flagged),
        },
    }
