#!/usr/bin/env python3
"""basis 명시 낙찰률 라벨 vs 기존 저장 라벨 — 커버리지·분포·basis 일관성 실측 (읽기 전용).

무엇을 재는가
-------------
1. **커버리지 대비.** 기존 학습 라벨 경로가 실제로 라벨을 내는 행 수와, 새 라벨
   (``winning_amount ÷ get_reliable_base``)이 성립하는 행 수를 같은 코퍼스에서 센다.
   tier 별로 나눠 세므로 "어느 tier 가 몇 행을 책임지는가"까지 보인다.
2. **분포 대비.** 두 라벨이 모두 있는 행에서 평균·표준편차·분위수와 차이의 분포.
3. **basis 판정.** 저장 라벨이 어느 축인지 **구별 가능한** 부분모집단에서, 저장 라벨이
   기초금액-relative(``낙찰가÷clean base``)와 예정가-relative(``보고 낙찰률``) 중 어느
   쪽에 붙는지 센다. 두 가설의 예측값이 사실상 같은 행(사정률≈1)은 구별 불가라 제외한다.
4. **선택 편향 공시.** 3번의 부분모집단은 clean base 를 요구하므로 reserve detail 수집에
   성공한 행으로 치우친다. 그 성공률이 카테고리와 상관되므로 카테고리 구성을 전체
   코퍼스와 나란히 출력한다 — 부분모집단 수치를 전체로 일반화하지 말라는 뜻이다.

판정 프리미티브는 프로덕션과 동일한 것을 쓴다(드리프트 방지):
``build_award_rate_label`` · ``PredictionDatasetService._normalize_bid_rate_value`` ·
``is_plausible_rate_label`` · ``PredictionDatasetService._load_latest_tender_results``.

DB read-only(write/commit 없음). 외부 API 호출 없음. 시각은 KST.

사용 예:
    docker compose exec -T api python scripts/measure_award_rate_label_coverage.py
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.database import SessionLocal
from app.core.time import kst_now
from app.domain.award_rate_label import AwardRateLabelStatus, build_award_rate_label
from app.domain.rate_normalization import is_plausible_rate_label
from app.domain.reliable_base import ReliableBaseSource
from app.models.models import HistoricalData
from app.services.base_amount_basis import BASIS_CLEAN
from app.services.prediction_dataset import PredictionDatasetService

# ``_load_latest_tender_results`` 는 project_id 집합을 IN 절로 넣는다. 운영 코퍼스 전체를
# 한 번에 넣으면 바인드 파라미터 상한(Postgres 65535)에 걸리므로 나눠 부른다.
_PROJECT_ID_CHUNK = 5_000

# 두 basis 가설의 예측값이 이 상대오차 안에서 겹치면 저장 라벨이 어느 축인지 **구별할 수
# 없다**(사정률 ≈ 1 인 행). 그런 행을 세면 두 가설 모두 "맞는" 것으로 계상돼 비율이
# 무의미해지므로 분모에서 뺀다.
_DISTINGUISHABLE_TOLERANCE = 1e-3
# 저장 라벨이 어느 가설과 "같다"고 볼 상대오차. 저장 시 6자리 반올림을 거치므로 완전
# 일치는 요구하지 않는다.
_MATCH_TOLERANCE = 1e-4

_DATASET = PredictionDatasetService()


@dataclass(frozen=True)
class _Row:
    """한 학습 행에서 뽑은 두 라벨과 판정 재료."""

    category: str
    stored_label: float | None  # HistoricalData.bid_rate (유효 창 통과분)
    reported_rate: float | None  # TenderResult.winning_rate 정규화 (예정가-relative)
    new_label: float | None  # 낙찰가 ÷ 신뢰 기초금액
    new_status: str
    denominator_source: str
    base_basis: str | None


def _relative_gap(left: float, right: float) -> float:
    """두 값의 상대 격차 |a-b| / |b| (분모가 0 이면 무한대로 본다)."""
    if right == 0:
        return float("inf")
    return abs(left - right) / abs(right)


def _quantiles(values: list[float]) -> tuple[float, float, float]:
    """(p5, p50, p95) — stdlib 만으로, 표본이 적으면 최소/중앙/최대로 퇴화한다."""
    if len(values) < 3:
        ordered = sorted(values)
        return (ordered[0], ordered[len(ordered) // 2], ordered[-1])
    cuts = statistics.quantiles(values, n=20, method="inclusive")
    return (cuts[0], statistics.median(values), cuts[-1])


def _describe(name: str, values: list[float]) -> None:
    """한 분포의 N·mean·sd·분위수를 한 줄로 출력."""
    if not values:
        print(f"  {name:32s} N=0")
        return
    p5, p50, p95 = _quantiles(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    print(
        f"  {name:32s} N={len(values):6d} mean={statistics.fmean(values):.6f} "
        f"sd={sd:.6f} p5={p5:.6f} p50={p50:.6f} p95={p95:.6f}"
    )


def _load_rows(db) -> list[_Row]:
    """코퍼스 전체를 훑어 행별 두 라벨을 계산한다(읽기 전용)."""
    records = (
        db.query(
            HistoricalData.project_id,
            HistoricalData.category,
            HistoricalData.bid_rate,
            HistoricalData.base_amount,
            HistoricalData.base_amount_basis,
            HistoricalData.base_amount_estimated,
        )
        .filter(HistoricalData.project_id.isnot(None))
        .all()
    )
    project_ids = sorted({int(record.project_id) for record in records})
    latest: dict[int, object] = {}
    for start in range(0, len(project_ids), _PROJECT_ID_CHUNK):
        chunk = set(project_ids[start : start + _PROJECT_ID_CHUNK])
        latest.update(_DATASET._load_latest_tender_results(db, project_ids=chunk))

    rows: list[_Row] = []
    for record in records:
        result = latest.get(int(record.project_id))
        winning_amount = getattr(result, "winning_amount", None)
        label = build_award_rate_label(
            winning_amount=winning_amount,
            base_amount=record.base_amount,
            base_amount_basis=record.base_amount_basis,
            base_amount_estimated=record.base_amount_estimated,
        )
        stored = float(record.bid_rate or 0.0)
        rows.append(
            _Row(
                category=str(record.category or "unknown"),
                stored_label=stored if is_plausible_rate_label(stored) else None,
                reported_rate=_DATASET._normalize_bid_rate_value(
                    getattr(result, "winning_rate", None)
                ),
                new_label=label.value,
                new_status=label.status.value,
                denominator_source=label.denominator_source.value,
                base_basis=record.base_amount_basis,
            )
        )
    return rows


def _report_coverage(rows: list[_Row]) -> None:
    """기존 라벨 경로와 새 라벨의 행 수 대비."""
    stored = sum(1 for row in rows if row.stored_label is not None)
    reported = sum(1 for row in rows if row.reported_rate is not None)
    tier_chain = sum(
        1
        for row in rows
        if row.stored_label is not None
        or row.reported_rate is not None
        or row.new_label is not None
    )
    new_ok = sum(1 for row in rows if row.new_label is not None)
    both = sum(
        1 for row in rows if row.new_label is not None and row.stored_label is not None
    )
    new_only = sum(
        1 for row in rows if row.new_label is not None and row.stored_label is None
    )
    stored_only = sum(
        1 for row in rows if row.new_label is None and row.stored_label is not None
    )

    print("\n=== 커버리지 (project 연결 HistoricalData 전수) ===")
    print(f"  전체 행                                : {len(rows)}")
    print(f"  tier-1 저장 라벨 (HistoricalData.bid_rate): {stored}")
    print(f"  tier-2 보고 낙찰률 (winning_rate)        : {reported}")
    print(f"  기존 tier 체인 합집합 (1|2|3)            : {tier_chain}")
    print(f"  새 라벨 (낙찰가 ÷ 신뢰 기초금액)          : {new_ok}")
    print(f"    ├ 저장 라벨과 공존                     : {both}")
    print(f"    ├ 새 라벨만                            : {new_only}")
    print(f"    └ 저장 라벨만                          : {stored_only}")

    print("\n  새 라벨 불성립 사유:")
    for status, count in Counter(
        row.new_status for row in rows if row.new_status != AwardRateLabelStatus.OK.value
    ).most_common():
        print(f"    {status:24s} {count}")

    print("\n  새 라벨의 분모 출처(성립 행만 — 신뢰 축):")
    for source, count in Counter(
        row.denominator_source for row in rows if row.new_label is not None
    ).most_common():
        print(f"    {source:24s} {count}")


def _report_distributions(rows: list[_Row]) -> None:
    """두 라벨이 모두 있는 행에서의 분포·차이."""
    paired = [
        row
        for row in rows
        if row.new_label is not None and row.stored_label is not None
    ]
    print("\n=== 분포 대비 (두 라벨 공존 행) ===")
    _describe("기존 저장 라벨", [row.stored_label for row in paired])  # type: ignore[misc]
    _describe("새 라벨(기초금액-relative)", [row.new_label for row in paired])  # type: ignore[misc]
    _describe(
        "차이 (새 − 기존)",
        [row.new_label - row.stored_label for row in paired],  # type: ignore[operator]
    )
    identical = sum(
        1
        for row in paired
        if _relative_gap(row.new_label, row.stored_label) < _MATCH_TOLERANCE  # type: ignore[arg-type]
    )
    if paired:
        print(
            f"  두 라벨이 사실상 같은 행: {identical} / {len(paired)} = "
            f"{identical / len(paired) * 100:.1f}%"
        )


def _decidable_rows(rows: list[_Row]) -> list[_Row]:
    """저장 라벨의 basis 를 판정할 수 있는 부분모집단.

    조건은 셋이다: clean base(분모가 진짜 기초금액), 보고 낙찰률 존재(예정가-relative 축의
    관측), 두 가설의 예측값이 서로 충분히 갈릴 것(사정률≈1 이면 구별 불가).
    """
    return [
        row
        for row in rows
        if row.stored_label is not None
        and row.reported_rate is not None
        and row.new_label is not None
        and row.base_basis == BASIS_CLEAN
        and row.denominator_source == ReliableBaseSource.CLEAN_BASE.value
        and _relative_gap(row.new_label, row.reported_rate)
        >= _DISTINGUISHABLE_TOLERANCE
    ]


def _basis_verdict(row: _Row) -> str:
    """이 행의 저장 라벨이 어느 축에 붙는가."""
    base_match = _relative_gap(row.stored_label, row.new_label) < _MATCH_TOLERANCE  # type: ignore[arg-type]
    yega_match = _relative_gap(row.stored_label, row.reported_rate) < _MATCH_TOLERANCE  # type: ignore[arg-type]
    if base_match and yega_match:
        return "양쪽 일치(구별 실패)"
    if base_match:
        return "기초금액-relative"
    if yega_match:
        return "예정가-relative"
    return "둘 다 아님"


def _report_selection_bias(rows: list[_Row], decidable: list[_Row]) -> None:
    """부분모집단이 전체와 어떻게 다른지 — 수치를 일반화하지 말라는 공시."""
    print("\n  선택 편향 공시 — 카테고리 구성 (부분모집단 vs 전체):")
    sub_counts = Counter(row.category for row in decidable)
    all_counts = Counter(row.category for row in rows)
    for category, count in sub_counts.most_common():
        share = count / len(decidable) * 100
        overall = all_counts[category] / len(rows) * 100
        print(
            f"    {category:22s} 부분모집단 {share:5.1f}%  전체 {overall:5.1f}%  "
            f"(n={count})"
        )
    print(
        "  ※ 이 부분모집단은 clean base(=reserve detail 수집 성공)를 요구하므로 위 구성이"
        " 전체와 다르다. 비율을 전체 코퍼스로 일반화하지 말 것."
    )


def _report_basis_verdict(rows: list[_Row]) -> None:
    """저장 라벨이 어느 basis 에 붙는지 — 구별 가능한 부분모집단에서만."""
    decidable = _decidable_rows(rows)
    print("\n=== 저장 라벨의 basis 판정 (clean base · 두 가설 구별 가능 행) ===")
    print(f"  부분모집단 N = {len(decidable)}")
    if not decidable:
        return

    verdicts = Counter(_basis_verdict(row) for row in decidable)
    for verdict, count in verdicts.most_common():
        print(f"    {verdict:22s} {count:6d}  ({count / len(decidable) * 100:5.1f}%)")
    print(
        "  ※ 새 라벨은 이 부분모집단 전체에서 정의상 기초금액-relative 다"
        " (분모 = clean base). 저장 라벨만 축이 갈린다."
    )
    _report_selection_bias(rows, decidable)


def main() -> None:
    db = SessionLocal()
    try:
        rows = _load_rows(db)
        print(f"[measure_award_rate_label_coverage] {kst_now():%Y-%m-%d %H:%M:%S} KST")
        _report_coverage(rows)
        _report_distributions(rows)
        _report_basis_verdict(rows)
    finally:
        db.close()


if __name__ == "__main__":
    main()
