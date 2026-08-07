"""Run aggregation + reviewer-facing report for the base_amount_basis backfill.

Split out of ``scripts/backfill_base_amount_basis.py`` (그 파일이 500줄 소프트 한도를
넘어서 §4.5-4 로 분해): the script owns the DB sweep (paging, classification, stamping)
and this module owns "무엇이 관측됐고 승인자에게 어떻게 보이는가". The counters exist for
ONE purpose — answering the §0 approval question before an ``--apply`` writes to the
production DB — so they live next to the terminal report that renders them.

``--audit`` JSON 조립(``summarize``)은 **여기 없다**: 파일 형태는 스크립트의 출력 계약이라
CLI 와 함께 움직인다.

No DB access and no writes: every function takes already-loaded values.
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.models.models import HistoricalData  # noqa: E402
from app.services.base_amount_basis import ALL_BASES  # noqa: E402

UNKNOWN_BUCKET = "unknown"  # Project 행이 없거나 값이 비어 분해 키를 못 얻은 경우

MAX_SAMPLES = 12  # dry-run before/after evidence rows (per run)


@dataclass(frozen=True)
class NoticeFacts:
    """분류·분해에 필요한 공고(Project) 측 사실. Project 행이 없으면 기본값이 선다."""

    budget_estimate: float = 0.0
    status: str = UNKNOWN_BUCKET
    category: str = UNKNOWN_BUCKET


NO_NOTICE_FACTS = NoticeFacts()


@dataclass
class BackfillStats:
    """Aggregate counts for one backfill run (dry-run or apply)."""

    applied: bool = False
    recheck: bool = False
    basis_filter: str | None = None
    scanned: int = 0
    by_basis: Counter = field(default_factory=Counter)
    estimated_filled: int = 0  # non-clean rows that got an estimate
    estimated_missing: int = 0  # non-clean rows without recoverable reserves
    # 추정치 채움을 공고 status 로 교차 분해한다. ``get_reliable_base`` 가 금액을 실제로
    # 바꾸는 경로는 "non-clean basis + 양수 추정치" 하나뿐이라, 열린 공고 ∩ 추정치 = 0
    # 이라는 사실이 곧 "이 백필은 라이브 투찰 base 금액을 바꾸지 않는다"의 증명이 된다.
    estimated_filled_by_status: Counter = field(default_factory=Counter)
    # 저장된 라벨과 **달라진** 행 수. ``basis_filter`` 패스뿐 아니라 ``--recheck`` 에서도
    # 센다 — 룰이 재정렬됐을 때 그것을 전 행에 반영하는 패스가 바로 --recheck 이므로, 그
    # 패스에만 증적이 없으면 무엇이 움직였는지 아무도 볼 수 없다. 한 번도 스탬프된 적 없는
    # 행의 첫 태깅은 이동이 아니다(``previous_basis`` 가 None).
    reclassified: int = 0
    # 이동 행을 공고 status / category 로 분해한다. 총량만으로는 재태깅이 열린 공고를
    # 건드리는지, 특정 카테고리에 몰렸는지 알 수 없어 승인 판단의 근거가 되지 못한다.
    reclassified_by_status: Counter = field(default_factory=Counter)
    reclassified_by_category: Counter = field(default_factory=Counter)
    # 이동 행 중 복구 추정치를 가진 수 — 라벨만 바뀌는 행과 구분한다(위 참조).
    reclassified_with_reserve_estimate: int = 0
    # 추정가격이 base 와 정확히 같은 행. 수집이 추정가격을 못 얻으면
    # ``matching.resolve_budget_estimate`` 가 base_amount 를 그대로 추정가격으로 쓰므로
    # 비율이 항상 1.0 이 되어 base 가 아무리 오염돼도 이 규칙에 걸리지 않는다. 규칙이
    # 구조적으로 못 보는 코호트라 검증 커버리지로 함께 보고한다.
    est_equals_base: int = 0
    samples: list[dict[str, Any]] = field(default_factory=list)

    @property
    def bucket_shrink_ratio(self) -> float:
        """선택한 버킷에서 빠져나가는 비율(이동 ÷ 스캔). 스캔 0 이면 0.0."""
        return round(self.reclassified / self.scanned, 4) if self.scanned else 0.0

    def basis_counts(self) -> dict[str, int]:
        """``ALL_BASES`` 전 라벨의 계수 — 관측되지 않은 라벨도 0 으로 함께 낸다."""
        return {basis: int(self.by_basis.get(basis, 0)) for basis in ALL_BASES}

    # JSON 요약(``summarize``)은 이 dataclass 가 아니라 CLI 쪽이 조립한다: ``--audit``
    # 파일 형태는 스크립트의 **출력 계약**이라 CLI 와 함께 움직여야 하고, 이 모듈은
    # 계수와 사람이 읽는 리포트만 소유한다.


def record_reclassification(
    stats: BackfillStats,
    record: HistoricalData,
    *,
    previous_basis: str | None,
    basis: str,
    estimated: float | None,
    notice: NoticeFacts,
) -> None:
    """Count one label-changing row and keep a bounded before/after sample.

    ``reclassified_with_reserve_estimate`` separates the rows this actually re-prices
    downstream from the rows that only change label: ``get_reliable_base`` swaps in a
    value ONLY when a non-clean row carries a positive reserve estimate.

    The sample carries BOTH amounts and their ratio because that ratio is the whole
    evidence for a ``suspect-ratio`` verdict — a reviewer must be able to see why a
    row moved without re-querying the DB.
    """
    stats.reclassified += 1
    stats.reclassified_by_status[notice.status] += 1
    stats.reclassified_by_category[notice.category] += 1
    if estimated is not None and estimated > 0:
        stats.reclassified_with_reserve_estimate += 1
    if len(stats.samples) >= MAX_SAMPLES:
        return
    base_amount = float(record.base_amount) if record.base_amount is not None else None
    ratio = (
        round(base_amount / notice.budget_estimate, 6)
        if base_amount and notice.budget_estimate > 0
        else None
    )
    stats.samples.append(
        {
            "id": record.id,
            "base_amount": base_amount,
            "budget_estimate": notice.budget_estimate or None,
            "base_to_estimate_ratio": ratio,
            "from_basis": previous_basis,
            "to_basis": basis,
            "estimated": estimated,
        }
    )


def print_impact_report(stats: BackfillStats) -> None:
    """Print the move impact in a form a reviewer reads before approving an apply.

    The JSON summary already carries every number; this block exists because the
    approval questions ("몇 행이 어느 공고 상태·카테고리에서 버킷을 떠나는가",
    "그중 하류 금액이 실제로 바뀌는 행은", "규칙이 못 보는 행은 얼마나 되는가") should be
    answerable from the terminal without piping through ``jq``. Nothing here writes — a
    dry-run prints exactly what an ``--apply`` would move.
    """
    if stats.basis_filter is None and stats.reclassified == 0:
        return
    bucket = f"'{stats.basis_filter}' 버킷" if stats.basis_filter else "스캔"
    shrink = f" (축소율 {stats.bucket_shrink_ratio:.2%})" if stats.basis_filter else ""
    print(
        f"[backfill-base-basis] {bucket} {stats.scanned}행 중 "
        f"{stats.reclassified}행 이동{shrink}"
    )
    print(
        f"  그중 복구 추정치 보유: {stats.reclassified_with_reserve_estimate}"
        " (이 축만 하류 금액이 바뀐다)"
    )
    print(
        f"  추정가격==base 라 비율 규칙이 못 보는 행: {stats.est_equals_base}"
        f" / 스캔 {stats.scanned}"
    )
    for title, counter in (
        ("status", stats.reclassified_by_status),
        ("category", stats.reclassified_by_category),
        ("추정치 채움 status", stats.estimated_filled_by_status),
    ):
        breakdown = ", ".join(f"{key}={count}" for key, count in sorted(counter.items()))
        print(f"  이동 {title}별: {breakdown or '없음'}")
    for sample in stats.samples:
        print(
            f"  샘플 id={sample['id']} {sample['from_basis']} → {sample['to_basis']} "
            f"base={sample['base_amount']} est={sample['budget_estimate']} "
            f"ratio={sample['base_to_estimate_ratio']}"
        )
