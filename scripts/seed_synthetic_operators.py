#!/usr/bin/env python3
"""Seed synthetic operator company profiles for win-rate backtesting.

Creates a fixed catalogue of synthetic operators — each is a separate ``User``
row plus its own ``CompanyProfile`` and ``OperatorStrategy`` — so the existing
paper-bidding backtest can be replayed per operator and win rates compared
across realistic archetypes.

Synthetic accounts use the username prefix ``synthetic-`` so the canonical
``operator`` account from :mod:`app.core.single_user` remains the default
single-operator workflow target.

Usage::

    # Inspect the catalogue without writing to the DB
    python scripts/seed_synthetic_operators.py --dry-run

    # Insert/upsert synthetic operators into the configured database
    python scripts/seed_synthetic_operators.py

    # Remove every synthetic operator (and its profile + strategy)
    python scripts/seed_synthetic_operators.py --purge
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.core.security import get_password_hash
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    join_multi_value_text,
)
from app.models.models import CompanyProfile, OperatorStrategy, User
from app.services.synthetic_custom_operator import synthetic_email


SYNTHETIC_USERNAME_PREFIX = "synthetic-"
SYNTHETIC_PASSWORD = "synthetic-backtest-only"  # never used for real auth


@dataclass(frozen=True)
class SyntheticArchetype:
    """One reproducible operator archetype used for win-rate backtests."""

    slug: str
    display_name: str
    company_name: str
    business_type: str
    license_codes: list[str]
    region_codes: list[str]
    annual_revenue: float
    capacity_score: float
    focus_categories: list[str]
    focus_regions: list[str]
    required_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_regions: list[str] = field(default_factory=list)
    min_budget_estimate: float = 0.0
    max_budget_estimate: float = 0.0
    minimum_match_score: float = 0.6
    minimum_probability_score: float = 0.55
    bid_now_threshold: float = DEFAULT_OPERATOR_BID_NOW_THRESHOLD
    review_threshold: float = DEFAULT_OPERATOR_REVIEW_THRESHOLD
    auto_workload_penalty_multiplier: float = 1.0
    max_recommended_candidates: int = 10
    notes: str = ""


# ----------------------------------------------------------------------
# 12 archetypes — Software 3 / Technical-service 2 / General service 2 /
# Goods 2 / Construction 3.  Budget/region/keyword choices are deliberately
# spread so paper-bidding backtests yield distinguishable win rates.
# ----------------------------------------------------------------------
ARCHETYPES: tuple[SyntheticArchetype, ...] = (
    # --- Software (3) -------------------------------------------------
    SyntheticArchetype(
        slug="sw-small-seoul",
        display_name="소프트웨어 · 소형 · 서울",
        company_name="가상소프트 소형 (서울)",
        business_type="software",
        license_codes=["소프트웨어개발사업자", "직접생산확인(SW)"],
        region_codes=["서울", "경기"],
        annual_revenue=600_000_000.0,
        capacity_score=0.45,
        focus_categories=["software"],
        focus_regions=["서울", "경기"],
        required_keywords=[],
        min_budget_estimate=30_000_000.0,
        max_budget_estimate=300_000_000.0,
        notes="신생 SI/AI 스타트업. 수도권 소형 공고만 추격.",
    ),
    SyntheticArchetype(
        slug="sw-mid-metro",
        display_name="소프트웨어 · 중형 · 수도권",
        company_name="가상소프트 중형 (수도권)",
        business_type="software",
        license_codes=[
            "소프트웨어개발사업자",
            "정보통신공사업",
            "정보보호전문서비스",
        ],
        region_codes=["서울", "경기", "인천"],
        annual_revenue=3_500_000_000.0,
        capacity_score=0.72,
        focus_categories=["software"],
        focus_regions=["서울", "경기", "인천"],
        required_keywords=[],
        exclude_keywords=["하도급"],
        min_budget_estimate=150_000_000.0,
        max_budget_estimate=1_500_000_000.0,
        notes="보안/클라우드/DX 중심 중형 SI.",
    ),
    SyntheticArchetype(
        slug="sw-large-national",
        display_name="소프트웨어 · 대형 · 전국",
        company_name="가상소프트 대형 (전국)",
        business_type="software",
        license_codes=[
            "소프트웨어개발사업자",
            "정보통신공사업",
            "엔지니어링활동주체신고",
            "직접생산확인(SW)",
        ],
        region_codes=["전국"],
        annual_revenue=18_000_000_000.0,
        capacity_score=0.9,
        focus_categories=["software", "technical-service"],
        focus_regions=["전국"],
        required_keywords=[],
        min_budget_estimate=500_000_000.0,
        max_budget_estimate=8_000_000_000.0,
        auto_workload_penalty_multiplier=0.8,
        max_recommended_candidates=15,
        notes="대형 SI/플랫폼 통합. 전국 단위 대형 공고 위주.",
    ),
    # --- Technical service / Engineering (2) -------------------------
    SyntheticArchetype(
        slug="eng-supervision-busan",
        display_name="기술용역 · 감리 · 부산경남",
        company_name="가상엔지니어링 감리 (부산경남)",
        business_type="technical-service",
        license_codes=[
            "건설사업관리(감리)",
            "엔지니어링활동주체신고",
        ],
        region_codes=["부산", "경남", "울산"],
        annual_revenue=2_200_000_000.0,
        capacity_score=0.68,
        focus_categories=["technical-service"],
        focus_regions=["부산", "경남", "울산"],
        required_keywords=[],
        exclude_keywords=["설계공모"],
        min_budget_estimate=80_000_000.0,
        max_budget_estimate=1_200_000_000.0,
        notes="동남권 건축/토목 감리 전문.",
    ),
    SyntheticArchetype(
        slug="eng-design-daejeon",
        display_name="기술용역 · 설계 · 대전충청",
        company_name="가상엔지니어링 설계 (대전충청)",
        business_type="technical-service",
        license_codes=[
            "엔지니어링활동주체신고",
            "기술사사무소",
            "건축사사무소",
        ],
        region_codes=["대전", "충북", "충남", "세종"],
        annual_revenue=5_500_000_000.0,
        capacity_score=0.78,
        focus_categories=["technical-service"],
        focus_regions=["대전", "충북", "충남", "세종"],
        required_keywords=[],
        min_budget_estimate=120_000_000.0,
        max_budget_estimate=2_500_000_000.0,
        notes="설계/엔지니어링 중견. 충청권 종합기술용역.",
    ),
    # --- General service (2) -----------------------------------------
    SyntheticArchetype(
        slug="gs-cleaning-metro",
        display_name="일반용역 · 청소/위탁 · 수도권",
        company_name="가상서비스 청소위탁 (수도권)",
        business_type="service",
        license_codes=["건축물청소관리업", "위생관리"],
        region_codes=["서울", "경기"],
        annual_revenue=900_000_000.0,
        capacity_score=0.42,
        focus_categories=["service", "general-service"],
        focus_regions=["서울", "경기"],
        required_keywords=[],
        exclude_keywords=["야간"],
        min_budget_estimate=20_000_000.0,
        max_budget_estimate=350_000_000.0,
        notes="청소/위탁/환경관리 영세업체.",
    ),
    SyntheticArchetype(
        slug="gs-security-national",
        display_name="일반용역 · 경비/시설관리 · 전국",
        company_name="가상서비스 경비시설 (전국)",
        business_type="service",
        license_codes=["경비업허가", "시설물유지관리업"],
        region_codes=["전국"],
        annual_revenue=7_200_000_000.0,
        capacity_score=0.7,
        focus_categories=["service", "general-service"],
        focus_regions=["전국"],
        required_keywords=[],
        min_budget_estimate=150_000_000.0,
        max_budget_estimate=3_000_000_000.0,
        notes="경비/시설관리 종합. 다지역 동시 수행 가능.",
    ),
    # --- Goods (2) ----------------------------------------------------
    SyntheticArchetype(
        slug="gd-office-sme",
        display_name="물품 · 사무가구/비품 · 중소",
        company_name="가상물품 사무 (중소)",
        business_type="goods",
        license_codes=["직접생산확인(가구)", "중소기업확인서"],
        region_codes=["전국"],
        annual_revenue=1_400_000_000.0,
        capacity_score=0.48,
        focus_categories=["goods"],
        focus_regions=["전국"],
        required_keywords=[],
        exclude_keywords=["군수품"],
        min_budget_estimate=10_000_000.0,
        max_budget_estimate=400_000_000.0,
        notes="사무가구/비품 다지역 납품 중소.",
    ),
    SyntheticArchetype(
        slug="gd-it-equipment-midcap",
        display_name="물품 · IT장비 · 중견",
        company_name="가상물품 IT장비 (중견)",
        business_type="goods",
        license_codes=[
            "직접생산확인(서버/네트워크)",
            "정보통신공사업",
        ],
        region_codes=["서울", "경기", "인천", "충남"],
        annual_revenue=9_500_000_000.0,
        capacity_score=0.75,
        focus_categories=["goods"],
        focus_regions=["서울", "경기", "인천", "충남"],
        required_keywords=[],
        min_budget_estimate=80_000_000.0,
        max_budget_estimate=2_500_000_000.0,
        notes="서버/스토리지/네트워크 장비 공급 중견.",
    ),
    # --- Construction (3) --------------------------------------------
    SyntheticArchetype(
        slug="cn-small-gangwon",
        display_name="공사 · 소형 · 강원",
        company_name="가상건설 소형 (강원)",
        business_type="construction",
        license_codes=[
            "일반건설업(건축)",
            "전문건설업(실내건축)",
        ],
        region_codes=["강원"],
        annual_revenue=2_800_000_000.0,
        capacity_score=0.55,
        focus_categories=["construction"],
        focus_regions=["강원"],
        required_keywords=[],
        exclude_keywords=["고난도"],
        min_budget_estimate=50_000_000.0,
        max_budget_estimate=600_000_000.0,
        notes="강원권 소형 건축/실내 공사.",
    ),
    SyntheticArchetype(
        slug="cn-mid-gyeonggi",
        display_name="공사 · 중형 · 경기충청",
        company_name="가상건설 중형 (경기충청)",
        business_type="construction",
        license_codes=[
            "일반건설업(토목)",
            "일반건설업(건축)",
            "전문건설업(포장)",
        ],
        region_codes=["경기", "충북", "충남"],
        annual_revenue=22_000_000_000.0,
        capacity_score=0.82,
        focus_categories=["construction"],
        focus_regions=["경기", "충북", "충남"],
        required_keywords=[],
        min_budget_estimate=400_000_000.0,
        max_budget_estimate=5_000_000_000.0,
        auto_workload_penalty_multiplier=0.9,
        notes="도로/교량/건축 중형. 경기·충청 광역 운용.",
    ),
    SyntheticArchetype(
        slug="cn-electric-telecom-national",
        display_name="공사 · 전기/통신 · 전국",
        company_name="가상건설 전기통신 (전국)",
        business_type="construction",
        license_codes=[
            "전기공사업",
            "정보통신공사업",
            "소방시설공사업",
        ],
        region_codes=["전국"],
        annual_revenue=14_500_000_000.0,
        capacity_score=0.78,
        focus_categories=["construction"],
        focus_regions=["전국"],
        required_keywords=[],
        min_budget_estimate=200_000_000.0,
        max_budget_estimate=4_500_000_000.0,
        max_recommended_candidates=12,
        notes="전기/통신/소방 복합 전문공사 전국 단위.",
    ),
)


def _username(slug: str) -> str:
    return f"{SYNTHETIC_USERNAME_PREFIX}{slug}"


def upsert_synthetic_operator(db: Session, archetype: SyntheticArchetype) -> dict[str, Any]:
    """Create or update one synthetic operator + profile + strategy."""
    username = _username(archetype.slug)
    email = synthetic_email(username)

    user = db.query(User).filter(User.username == username).first()
    created_user = False
    if user is None:
        user = User(
            username=username,
            email=email,
            full_name=archetype.display_name,
            company=archetype.company_name,
            hashed_password=get_password_hash(SYNTHETIC_PASSWORD),
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        db.flush()
        created_user = True
    else:
        user.email = email
        user.full_name = archetype.display_name
        user.company = archetype.company_name
        user.is_active = True

    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.user_id == user.id)
        .first()
    )
    created_profile = False
    if profile is None:
        profile = CompanyProfile(user_id=user.id)
        db.add(profile)
        created_profile = True
    profile.business_type = archetype.business_type
    profile.license_codes = join_multi_value_text(archetype.license_codes)
    profile.region_codes = join_multi_value_text(archetype.region_codes)
    profile.annual_revenue = float(archetype.annual_revenue)
    profile.capacity_score = float(archetype.capacity_score)

    strategy = (
        db.query(OperatorStrategy)
        .filter(OperatorStrategy.user_id == user.id)
        .first()
    )
    created_strategy = False
    if strategy is None:
        strategy = OperatorStrategy(user_id=user.id)
        db.add(strategy)
        created_strategy = True
    strategy.focus_categories = join_multi_value_text(archetype.focus_categories)
    strategy.focus_regions = join_multi_value_text(archetype.focus_regions)
    strategy.exclude_regions = join_multi_value_text(archetype.exclude_regions)
    strategy.required_keywords = join_multi_value_text(archetype.required_keywords)
    strategy.exclude_keywords = join_multi_value_text(archetype.exclude_keywords)
    strategy.min_budget_estimate = float(archetype.min_budget_estimate)
    strategy.max_budget_estimate = float(archetype.max_budget_estimate)
    strategy.minimum_match_score = float(archetype.minimum_match_score)
    strategy.minimum_probability_score = float(archetype.minimum_probability_score)
    strategy.bid_now_threshold = float(archetype.bid_now_threshold)
    strategy.review_threshold = float(archetype.review_threshold)
    strategy.auto_workload_penalty_multiplier = float(archetype.auto_workload_penalty_multiplier)
    strategy.notify_only_high_priority = True
    strategy.max_recommended_candidates = int(archetype.max_recommended_candidates)

    db.flush()

    return {
        "archetype": archetype.slug,
        "username": username,
        "user_id": int(user.id),
        "company_profile_id": int(profile.id) if profile.id else None,
        "operator_strategy_id": int(strategy.id) if strategy.id else None,
        "business_type": archetype.business_type,
        "region_codes": archetype.region_codes,
        "min_budget_estimate": archetype.min_budget_estimate,
        "max_budget_estimate": archetype.max_budget_estimate,
        "created_user": created_user,
        "created_profile": created_profile,
        "created_strategy": created_strategy,
    }


def purge_synthetic_operators(db: Session) -> dict[str, Any]:
    """Remove every synthetic operator account and its dependent rows."""
    users = (
        db.query(User)
        .filter(User.username.like(f"{SYNTHETIC_USERNAME_PREFIX}%"))
        .all()
    )
    removed: list[dict[str, Any]] = []
    for user in users:
        db.query(OperatorStrategy).filter(OperatorStrategy.user_id == user.id).delete(synchronize_session=False)
        db.query(CompanyProfile).filter(CompanyProfile.user_id == user.id).delete(synchronize_session=False)
        removed.append({"user_id": int(user.id), "username": user.username})
        db.delete(user)
    db.flush()
    return {"removed_count": len(removed), "removed": removed}


def list_synthetic_operators(db: Session) -> list[dict[str, Any]]:
    """Return the synthetic operators already present in the database."""
    users = (
        db.query(User)
        .filter(User.username.like(f"{SYNTHETIC_USERNAME_PREFIX}%"))
        .order_by(User.id.asc())
        .all()
    )
    rows: list[dict[str, Any]] = []
    for user in users:
        profile = (
            db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == user.id)
            .first()
        )
        strategy = (
            db.query(OperatorStrategy)
            .filter(OperatorStrategy.user_id == user.id)
            .first()
        )
        rows.append(
            {
                "user_id": int(user.id),
                "username": user.username,
                "company": user.company,
                "business_type": getattr(profile, "business_type", None),
                "region_codes": getattr(profile, "region_codes", None),
                "focus_categories": getattr(strategy, "focus_categories", None),
                "max_budget_estimate": getattr(strategy, "max_budget_estimate", None),
            }
        )
    return rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the archetype catalogue as JSON without touching the database.",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete every synthetic operator (and its profile + strategy).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List the synthetic operators that already exist in the database.",
    )
    parser.add_argument(
        "--out",
        default="",
        help=(
            "Optional JSON output path. Defaults to "
            "models/reports/synthetic-operators.json after a real seed run."
        ),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.dry_run:
        payload = {
            "count": len(ARCHETYPES),
            "archetypes": [asdict(archetype) for archetype in ARCHETYPES],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if args.list:
            print(json.dumps(list_synthetic_operators(db), ensure_ascii=False, indent=2))
            return 0

        if args.purge:
            result = purge_synthetic_operators(db)
            db.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        seeded: list[dict[str, Any]] = []
        for archetype in ARCHETYPES:
            seeded.append(upsert_synthetic_operator(db, archetype))
        db.commit()
    finally:
        db.close()

    output_path = Path(args.out or "models/reports/synthetic-operators.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"count": len(seeded), "operators": seeded}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "count": len(seeded),
                "out": str(output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
