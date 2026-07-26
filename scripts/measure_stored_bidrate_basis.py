#!/usr/bin/env python3
"""저장된 낙찰률 라벨의 예정가 vs 기초금액 basis 비대칭 계측 (읽기 전용).

배경 (#261 리뷰 nit)
--------------------
가격 학습 데이터셋에 basis 잔여 비대칭이 있다. #199/#225 로 **base 피처**와 파생-rate
폴백(``winning_amount ÷ get_reliable_base``, path-3)은 기초금액-basis 로 정렬됐지만,
지배적 rate 라벨(``HistoricalData.bid_rate`` ≈ ``TenderResult.winning_rate`` =
낙찰가/예정가, path-1)은 여전히 **예정가-relative** 다. 서빙은 이 rate 를 기초금액
(``resolve_notice_bid_base``)에 곱하므로 basis mismatch 가 남는다.

이 스크립트는 그 비대칭의 **방향·크기**를 실측한다. path-1(예정가 기준)과
기초금액-basis 환산 라벨(path-3)을 둘 다 계산 가능한 settled 행에서 비교하고,
``get_reliable_base`` 가 원본 base 로 폴백해 path-3 가 예정가로 오염되는
**confound 를 분리**해(clean-base·reserve-estimate = 정직한 기초금액) 정직한
신호만 집계한다. 문서: ``docs/design/stored-bidrate-basis-alignment.md``.

계측은 프로덕션 라벨 로직을 그대로 재사용한다(드리프트 방지): path-1 정규화는
``PredictionDatasetService._normalize_bid_rate_value`` 를, base 선택은
``get_reliable_base`` 를 호출한다. 유효범위 게이트는 서비스의 클래스 상수를 쓴다.

DB read-only(write/commit 없음). 외부 API 호출 없음. 시각은 KST.

사용 예:
    docker compose exec -T api python scripts/measure_stored_bidrate_basis.py
    (카테고리 분해가 필요하면 위 명령에 ``--by-category`` 를 덧붙인다)
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal
from app.core.time import kst_now
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, TenderResult
from app.services.prediction_dataset import PredictionDatasetService

# 정직한(오염되지 않은) 기초금액을 내는 reliable-base source 집합. 그 외
# (BASE_FALLBACK on derived-yega)는 원본 base_amount 로 폴백하는데, 그 값이
# 예정가-역산 오염이면 path-3 == path-1 이 되어 비대칭이 spurious 하게 0 으로 보인다.
HONEST_BASE_SOURCES: frozenset[str] = frozenset(
    {ReliableBaseSource.CLEAN_BASE.value, ReliableBaseSource.RESERVE_ESTIMATE.value}
)

# 실무적으로 유의미한 per-notice 사정률 이탈 임계값(리포트 부가 지표).
_MATERIAL_DIVERGENCE_THRESHOLDS = (0.005, 0.01)

_DATASET = PredictionDatasetService()


@dataclass
class _Pair:
    """한 settled 학습 행의 두 basis 라벨과 first-priority 라벨."""

    path1_yega: float  # 낙찰가/예정가 (TenderResult.winning_rate 정규화)
    path3_base: float  # 낙찰가/기초금액 (winning_amount / reliable_base)
    hd_bid_rate: float | None  # HistoricalData.bid_rate (실 first-priority 라벨)
    category: str


def _gate(value: float | None) -> float | None:
    """서비스와 동일한 [MIN, MAX] 유효범위 게이트 (양수·범위 내만 통과)."""
    if value is None:
        return None
    if _DATASET.VALID_BID_RATE_MIN <= value <= _DATASET.VALID_BID_RATE_MAX:
        return float(value)
    return None


def _best_tender_result_by_project(db) -> dict[int, TenderResult]:
    """project 별 대표 settled TenderResult (최신 id 우선 — 지배 라벨 소스와 일치)."""
    results = (
        db.query(TenderResult)
        .filter(TenderResult.project_id.isnot(None))
        .filter((TenderResult.winning_amount > 0) | (TenderResult.winning_rate > 0))
        .order_by(TenderResult.project_id.asc(), TenderResult.id.desc())
        .all()
    )
    best: dict[int, TenderResult] = {}
    for result in results:
        project_id = int(result.project_id)
        if project_id not in best:  # 최신 id (desc 정렬 → 첫 등장이 최신)
            best[project_id] = result
    return best


def _percentiles(values: list[float], points: tuple[int, ...]) -> dict[int, float]:
    """정렬 후 최근접-순위 백분위 (numpy 의존 없이 stdlib 로)."""
    if not values:
        return {p: 0.0 for p in points}
    ordered = sorted(values)
    last = len(ordered) - 1
    out: dict[int, float] = {}
    for p in points:
        idx = round(p / 100.0 * last)
        out[p] = round(ordered[min(max(idx, 0), last)], 6)
    return out


def _summarize(name: str, pairs: list[_Pair]) -> None:
    """한 버킷의 path3-path1 비대칭과 HD.bid_rate basis 정렬을 출력."""
    if not pairs:
        print(f"  {name:36s} N=0")
        return
    diffs = [p.path3_base - p.path1_yega for p in pairs]
    sajung = [p.path3_base / p.path1_yega for p in pairs]  # 예정가/기초금액
    mean_diff = statistics.fmean(diffs)
    mean_sajung = statistics.fmean(sajung)
    over_bid_pct = (1.0 / mean_sajung - 1.0) * 100.0  # 예정가-rate × 기초금액 초과율
    pct = _percentiles(diffs, (5, 50, 95))
    print(
        f"  {name:36s} N={len(pairs):6d} "
        f"mean(p3-p1)={mean_diff:+.5f} med={pct[50]:+.5f} "
        f"사정률_mean={mean_sajung:.5f} 초과추천={over_bid_pct:+.3f}% "
        f"p5={pct[5]:+.5f} p95={pct[95]:+.5f}"
    )
    hd_vs_p1 = [
        p.hd_bid_rate - p.path1_yega for p in pairs if p.hd_bid_rate is not None
    ]
    hd_vs_p3 = [
        p.hd_bid_rate - p.path3_base for p in pairs if p.hd_bid_rate is not None
    ]
    if hd_vs_p1:
        print(
            f"  {'':36s}   HD.bid_rate-p1(예정가) mean={statistics.fmean(hd_vs_p1):+.6f} "
            f"|mean|={statistics.fmean(abs(x) for x in hd_vs_p1):.6f}"
        )
    if hd_vs_p3:
        print(
            f"  {'':36s}   HD.bid_rate-p3(기초금액) mean={statistics.fmean(hd_vs_p3):+.6f} "
            f"|mean|={statistics.fmean(abs(x) for x in hd_vs_p3):.6f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--by-category",
        action="store_true",
        help="정직한 base 행을 카테고리별로 분해 출력 (공사=초기 고객 세그먼트)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        best = _best_tender_result_by_project(db)

        priority_source: Counter[str] = Counter()
        by_bucket: dict[str, list[_Pair]] = defaultdict(list)
        honest: list[_Pair] = []
        confound: list[_Pair] = []
        by_category_honest: dict[str, list[_Pair]] = defaultdict(list)

        rows = (
            db.query(HistoricalData)
            .filter(HistoricalData.project_id.isnot(None))
            .yield_per(2000)
        )
        for record in rows:
            tender_result = best.get(int(record.project_id))
            reliable = get_reliable_base(
                base_amount=record.base_amount,
                basis=record.base_amount_basis,
                base_amount_estimated=record.base_amount_estimated,
            )
            base = (
                float(reliable.value)
                if reliable.value is not None and reliable.value > 0
                else None
            )

            path1 = (
                _DATASET._normalize_bid_rate_value(tender_result.winning_rate)
                if tender_result is not None
                else None
            )
            path3 = None
            if tender_result is not None and base is not None:
                winning_amount = float(tender_result.winning_amount or 0.0)
                if winning_amount > 0:
                    path3 = _gate(winning_amount / base)
            hd_rate = _gate(float(record.bid_rate or 0.0))

            # first-priority 라벨 소스 (프로덕션 _resolve_bid_rate 우선순위 미러)
            if hd_rate is not None:
                priority_source["historical_data (예정가)"] += 1
            elif path1 is not None:
                priority_source["tender_result_winning_rate (예정가)"] += 1
            elif path3 is not None:
                priority_source["tender_result_winning_amount (기초금액)"] += 1
            else:
                priority_source["none"] += 1

            if path1 is None or path3 is None:
                continue
            pair = _Pair(
                path1_yega=path1,
                path3_base=path3,
                hd_bid_rate=hd_rate,
                category=str(record.category or "unknown"),
            )
            basis = str(record.base_amount_basis or "None")
            by_bucket[f"{reliable.source.value}|{basis}"].append(pair)
            if reliable.source.value in HONEST_BASE_SOURCES:
                honest.append(pair)
                by_category_honest[pair.category].append(pair)
            else:
                confound.append(pair)

        print(f"[measure_stored_bidrate_basis] {kst_now():%Y-%m-%d %H:%M:%S} KST")
        print("\n=== first-priority 라벨 소스 (project 연결 HistoricalData 전수) ===")
        for source, count in priority_source.most_common():
            print(f"  {source}: {count}")

        print("\n=== reliable-base source | basis 별 비대칭 ===")
        for tag, pairs in sorted(by_bucket.items(), key=lambda kv: -len(kv[1])):
            _summarize(tag, pairs)

        print("\n=== 집계: 정직한 기초금액 base (clean-base + reserve-estimate) ===")
        _summarize("HONEST", honest)
        print(
            "\n=== 집계: confound base (base-fallback = 원본 base_amount, 예정가 오염) ==="
        )
        _summarize("CONFOUND", confound)

        if honest:
            sajung = [p.path3_base / p.path1_yega for p in honest]
            for threshold in _MATERIAL_DIVERGENCE_THRESHOLDS:
                diverged = sum(1 for s in sajung if abs(s - 1.0) > threshold)
                print(
                    f"\n정직 행 |사정률-1| > {threshold * 100:.1f}%p: "
                    f"{diverged} / {len(sajung)} = {diverged / len(sajung) * 100:.1f}%"
                )

        if args.by_category:
            print("\n=== 정직한 base 행 카테고리별 (공사=초기 고객 세그먼트) ===")
            for category, pairs in sorted(
                by_category_honest.items(), key=lambda kv: -len(kv[1])
            ):
                _summarize(category, pairs)
    finally:
        db.close()


if __name__ == "__main__":
    main()
