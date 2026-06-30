#!/usr/bin/env python3
"""Configure the canonical operator as the 해양엔지니어링협회 게이트. --apply 없이는 dry-run.

초기 고객 = 해양엔지니어링협회 가입 업체(해양/항만 엔지니어링·기술용역 firm).
이 스크립트는 synthetic operator가 아니라 :mod:`app.core.single_user` 의 canonical
``operator`` 계정의 ``CompanyProfile`` + ``OperatorStrategy`` 를 해양 아키타입으로
설정해, 해양 공고가 추천/투찰가 산정 대상으로 식별되도록 한다.

Phase 1 즉효 게이트는 ``required_keywords`` (OR 매칭) + ``focus_categories`` 다.
면허 코드는 Phase 2(KONEPS 면허제한 수집) 대비 + 프로필 저장용이며
``app.services.classifier`` 의 LICENSE_ALIASES 로 ENG001/PORT001/MAR001/HYDRO001 로
정규화된다.

금액 필드(annual_revenue/capacity_score/construction_capacity_amount/
awarded_contract_limit)와 매칭 임계값(minimum_match_score 등)은 건드리지 않는다.

Usage::

    # 변경 계획만 출력(commit 없음) — 기본 동작
    python scripts/seed_marine_gate.py
    python scripts/seed_marine_gate.py --dry-run

    # canonical operator profile/strategy 에 실제 commit
    python scripts/seed_marine_gate.py --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.core.single_user import (
    ensure_operator_account,
    ensure_operator_profile,
    ensure_operator_strategy,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import CompanyProfile, OperatorStrategy

# ----------------------------------------------------------------------
# 게이트 정의값 (docs/marine-engineering-gate.md 와 단일 출처)
# ----------------------------------------------------------------------
MARINE_BUSINESS_TYPE = "technical-service"

# 면허군: classifier LICENSE_ALIASES 로 ENG001/PORT001/MAR001/HYDRO001 정규화.
# "해양엔지니어링" 은 의도적으로 ENG001+MAR001 둘 다 매칭된다(해양엔지니어링
# firm 은 엔지니어링 firm 이기도 함).
MARINE_LICENSE_CODES = ["엔지니어링", "항만및해안", "해양엔지니어링", "수로조사"]
MARINE_REGION_CODES = ["전국"]

# construction 포함: 해양 공사(예: "○○항 방파제 보강공사")는 KONEPS 카테고리가
# construction 이라 제외되던 문제를 해결한다. required_keywords(OR) 와 AND 로
# 결합되므로 해양 키워드가 있는 공사만 통과하고, 일반 건물/도로 공사는 키워드
# 미스로 제외된다.
MARINE_FOCUS_CATEGORIES = ["technical-service", "service", "construction"]

# Phase 1 OR 매칭 키워드 31개. 항만/해양/연안 토목·기술용역 어휘.
# 일반 어휘 6개(해양/수산/해상/매립/등대/수중)는 기관명·타도메인 오탐이 커서
# 제외한다(해양·수산→해양수산부 기관명, 해상→해상보험/운송, 매립→폐기물매립지,
# 등대→등대운영관리, 수중→수중생태조사). 구체 해양공학 합성어는 유지한다.
# "준설"도 제외: 라이브 데이터에서 하수관로·저수지·배수로 준설공사(지자체 토목)를
# 대량 오탐하고 진짜 해양 준설은 항만/항로/방파제/연안 등 다른 키워드로 잡힌다.
MARINE_REQUIRED_KEYWORDS = [
    "항만",
    "어항",
    "방파제",
    "안벽",
    "부두",
    "잔교",
    "선착장",
    "물양장",
    "호안",
    "방조제",
    "갑문",
    "계류",
    "케이슨",
    "사석",
    "해저",
    "연안",
    "해안",
    "갯벌",
    "조간대",
    "해양조사",
    "수로측량",
    "조위",
    "조류관측",
    "해양환경",
    "항만설계",
    "해안침식",
    "항로",
    "항로표지",
    "어초",
    "인공어초",
    "마리나",
]

# 무제한 예산 게이트(금액 축으로 거르지 않음).
MARINE_MIN_BUDGET_ESTIMATE = 0.0
MARINE_MAX_BUDGET_ESTIMATE = 0.0


def _capture_state(profile: CompanyProfile, strategy: OperatorStrategy) -> dict[str, Any]:
    """Snapshot only the gate-relevant fields (no secrets / no PII)."""
    return {
        "business_type": profile.business_type,
        "license_codes": split_multi_value_text(profile.license_codes),
        "region_codes": split_multi_value_text(profile.region_codes),
        "focus_categories": split_multi_value_text(strategy.focus_categories),
        "required_keywords": split_multi_value_text(strategy.required_keywords),
        "min_budget_estimate": float(strategy.min_budget_estimate or 0.0),
        "max_budget_estimate": float(strategy.max_budget_estimate or 0.0),
    }


def _apply_gate(profile: CompanyProfile, strategy: OperatorStrategy) -> None:
    """Set the canonical profile/strategy to the marine-gate archetype in place.

    Idempotent: assigns absolute values, so re-running yields the same result.
    Amount fields and match thresholds are deliberately left untouched.
    """
    profile.business_type = MARINE_BUSINESS_TYPE
    profile.license_codes = join_multi_value_text(MARINE_LICENSE_CODES)
    profile.region_codes = join_multi_value_text(MARINE_REGION_CODES)

    strategy.focus_categories = join_multi_value_text(MARINE_FOCUS_CATEGORIES)
    strategy.required_keywords = join_multi_value_text(MARINE_REQUIRED_KEYWORDS)
    strategy.min_budget_estimate = MARINE_MIN_BUDGET_ESTIMATE
    strategy.max_budget_estimate = MARINE_MAX_BUDGET_ESTIMATE


def run_marine_gate(db: Session, apply: bool = False) -> dict[str, Any]:
    """Resolve the canonical operator and (dry-run) plan or (apply) commit the gate.

    Returns a before/after plan dict. When ``apply`` is False the session is
    rolled back so the gate mutations are NOT persisted; when True the changes
    are committed. The caller owns session lifecycle (open/close).
    """
    operator = ensure_operator_account(db)
    profile = ensure_operator_profile(db)
    strategy = ensure_operator_strategy(db)

    before = _capture_state(profile, strategy)
    _apply_gate(profile, strategy)
    after = _capture_state(profile, strategy)

    if apply:
        db.commit()
    else:
        # Discard the in-memory gate mutations; nothing the gate touched is
        # persisted on a dry-run.
        db.rollback()

    return {
        "status": "applied" if apply else "dry-run",
        "applied": apply,
        "operator_id": int(operator.id),
        "before": before,
        "after": after,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit the marine-gate profile/strategy to the canonical operator.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the before→after plan without writing (default behaviour).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    apply = bool(args.apply) and not args.dry_run

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = run_marine_gate(db, apply=apply)
    finally:
        db.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
