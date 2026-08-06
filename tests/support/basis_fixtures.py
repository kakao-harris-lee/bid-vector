"""과세 공고 basis 값표와 공고 빌더 — basis 정합 테스트들의 단일 출처.

#354(capture 축)와 #355(expected_margin 축)가 같은 과세 공고 시나리오를 쓰는데
두 파일이 각자 상수를 선언하면 한쪽만 바뀌어 조용히 갈라진다. 값표는 여기서만
선언하고 두 테스트 파일이 import 한다.

과세 공고 기준: 기초금액 = 추정가격 × 1.1 (부가세 포함분). 면세 공고는 두 금액이
같아 모든 basis 정합이 no-op 이 되므로, 회귀를 드러내려면 반드시 과세형이어야 한다.
"""

from __future__ import annotations

from app.models.models import HistoricalData, Project

BUDGET_ESTIMATE = 100_000_000.0
"""추정가격(ex-VAT) — ``Project.budget_estimate`` 에 저장되는 값."""

BASE_AMOUNT = 110_000_000.0
"""기초금액/사업금액(부가세 포함) — 투찰율이 곱해지는 base."""

RECOMMENDED_AMOUNT = 99_000_000.0
"""추천 투찰가 = 0.90 × 기초금액 = 0.99 × 추정가격.

두 basis 에서 서로 다른 rate 가 나오도록 고른 값이다: 기초금액 기준 0.90 은
낙찰하한(0.88) 바로 위, 추정가격 기준 0.99 는 하한에서 한참 위로 보인다.
"""

RATE_ON_BASE = RECOMMENDED_AMOUNT / BASE_AMOUNT          # 0.90 — 정합
RATE_ON_ESTIMATE = RECOMMENDED_AMOUNT / BUDGET_ESTIMATE  # 0.99 — basis 혼동 시


def build_vat_notice(
    *,
    budget_estimate: float = BUDGET_ESTIMATE,
    category: str = "construction",
    title: str = "basis 정합 검증 공고",
) -> Project:
    """DB 에 넣지 않는 순수 ``Project`` — 인메모리 스코어링 테스트용."""
    return Project(
        title=title,
        description="투찰 기준금액 basis 정합",
        requirements="",
        budget_estimate=budget_estimate,
        category=category,
        status="open",
    )


def persist_vat_notice(
    db,
    *,
    budget_estimate: float = BUDGET_ESTIMATE,
    base_amount: float | None = BASE_AMOUNT,
    category: str = "construction",
) -> Project:
    """과세 공고 + 수집된 기초금액 행을 DB 에 적재한다.

    ``bid_rate`` 를 0 으로 두어 이 행이 학습 표본(``explicit_bid_rate_only``)으로는
    잡히지 않게 한다 — 기초금액만 나르는 행이다. ``base_amount=None`` 이면 기초금액
    미확보 공고(추정가격 폴백 경로)를 만든다.
    """
    project = build_vat_notice(budget_estimate=budget_estimate, category=category)
    db.add(project)
    db.flush()
    if base_amount is not None:
        db.add(
            HistoricalData(
                project_id=project.id,
                base_amount=base_amount,
                bid_rate=0.0,
                category=category,
            )
        )
    db.commit()
    db.refresh(project)
    return project
