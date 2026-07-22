#!/usr/bin/env python3
"""basis-aware ``get_reliable_base`` wiring의 라이브 base 변경 영향 계측 (읽기 전용).

#199 ``base_amount_basis`` 태그를 소비하는 basis-aware wiring이 ``resolve_notice_bid_base``
가 고르는 기초금액을 **바꾸는 공고 수**를 실측한다. P2 진단 예측: OPEN 공고의 base_amount
오염(derived-yega)은 사실상 0건(오염은 전부 개찰 후 settled 스냅샷)이라 라이브 영향 ~0.
이 스크립트는 그 예측을 실측으로 확인해 PR에 정직하게 싣기 위한 것이다.

계측 방법: ``resolve_notice_bid_base`` 와 동일하게 각 project의 **latest 양수 base_amount**
HistoricalData 행을 고른 뒤, 그 행에 ``get_reliable_base`` 를 적용해 반환값이 원본
``base_amount`` 와 다른지 센다. Project.status(open vs 그 외)와 선택 source 별로 분해한다.

DB read-only(write/commit 없음). 외부 API 호출 없음. 시각은 KST.

사용 예:
    docker exec bid_vector_api python scripts/measure_reliable_base_impact.py
    docker exec bid_vector_api python scripts/measure_reliable_base_impact.py --samples 20
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import func

from app.core.database import SessionLocal
from app.core.time import kst_now
from app.domain.reliable_base import ReliableBaseSource, get_reliable_base
from app.models.models import HistoricalData, Project

_OPEN_STATUS = "open"


def _latest_positive_base_rows(db):
    """Return the latest positive-``base_amount`` HistoricalData row per project.

    Mirrors ``resolve_notice_bid_base`` selection (filter base_amount>0, latest id
    per project_id) so the measured population is exactly what the resolver sees.
    """
    max_id_subq = (
        db.query(func.max(HistoricalData.id))
        .filter(
            HistoricalData.project_id.isnot(None),
            HistoricalData.base_amount > 0,
        )
        .group_by(HistoricalData.project_id)
    )
    return (
        db.query(HistoricalData)
        .filter(HistoricalData.id.in_(max_id_subq.subquery().select()))
        .all()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="바뀌는 공고 상세 샘플 최대 개수(정직 확인용, 기본 10)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = _latest_positive_base_rows(db)
        project_status = dict(
            db.query(Project.id, Project.status).all()
        )

        total = len(rows)
        changed = 0
        changed_open = 0
        source_counts: Counter[str] = Counter()
        basis_counts: Counter[str] = Counter()
        changed_samples: list[dict[str, object]] = []

        for row in rows:
            reliable = get_reliable_base(
                base_amount=row.base_amount,
                basis=row.base_amount_basis,
                base_amount_estimated=row.base_amount_estimated,
            )
            source_counts[reliable.source.value] += 1
            basis_counts[str(row.base_amount_basis)] += 1

            raw_base = float(row.base_amount or 0.0)
            new_value = float(reliable.value) if reliable.value is not None else 0.0
            row_changed = (
                reliable.source is ReliableBaseSource.RESERVE_ESTIMATE
                and abs(new_value - raw_base) > 1e-6
            )
            if not row_changed:
                continue
            changed += 1
            status = project_status.get(row.project_id)
            if status == _OPEN_STATUS:
                changed_open += 1
            if len(changed_samples) < args.samples:
                changed_samples.append(
                    {
                        "project_id": row.project_id,
                        "status": status,
                        "basis": row.base_amount_basis,
                        "raw_base": raw_base,
                        "reliable_base": new_value,
                    }
                )

        print(f"[measure_reliable_base_impact] {kst_now():%Y-%m-%d %H:%M:%S} KST")
        print(f"모집단(latest 양수 base 행/공고): {total}")
        print(f"wiring으로 base가 바뀌는 공고: {changed}")
        print(f"  그중 OPEN 공고: {changed_open}  (P2 예측: ~0)")
        print("source 분포:")
        for source, count in source_counts.most_common():
            print(f"  {source}: {count}")
        print("basis 분포(latest 양수 base 행 기준):")
        for basis, count in basis_counts.most_common():
            print(f"  {basis}: {count}")
        if changed_samples:
            print(f"바뀌는 공고 샘플(최대 {args.samples}):")
            for sample in changed_samples:
                print(
                    f"  project={sample['project_id']} status={sample['status']} "
                    f"basis={sample['basis']} "
                    f"{sample['raw_base']:.0f} → {sample['reliable_base']:.0f}"
                )
        else:
            print("바뀌는 공고 없음 — 라이브 영향 0 (P2 예측과 일치).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
