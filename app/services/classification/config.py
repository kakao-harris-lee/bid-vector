"""Declarative scoring configuration for the rule-based notice classifier.

All axis weights, mismatch penalties, structural ratios, and tier-band ladders
live here as declarative data (CLAUDE.md §"Declarative Configurations"). The
``NoticeClassifierService`` re-exports these as class attributes for its public
surface, and the per-axis modules import them directly.
"""

from typing import Callable

# --- Axis scores (positive contributions) ------------------------------------
MATCH_THRESHOLD = 0.65
EXACT_BUSINESS_TYPE_SCORE = 0.35
RELATED_BUSINESS_TYPE_SCORE = 0.2
LICENSE_MATCH_SCORE = 0.25
LICENSE_NEUTRAL_SCORE = 0.05
REGION_MATCH_SCORE = 0.2
REGION_ADVISORY_SCORE = 0.12
REGION_NEUTRAL_SCORE = 0.05
# Additive bonus stacked on the region-match score when the construction notice
# advertises a 지역가산점/지역우대 condition AND the operator's region overlaps the
# notice region — an in-region competitive advantage. Capped so the region axis
# stays bounded at REGION_MATCH_SCORE + REGION_PREFERENCE_BONUS (= 0.28).
# Heuristic matching signal, not an 적격심사 가산점 calculation.
REGION_PREFERENCE_BONUS = 0.08
BUDGET_STRONG_SCORE = 0.2
BUDGET_GOOD_SCORE = 0.15
BUDGET_BORDERLINE_SCORE = 0.1
CAPACITY_HIGH_SCORE = 0.12
CAPACITY_BASE_SCORE = 0.06
CAPABILITY_STRONG_SCORE = 0.15
CAPABILITY_GOOD_SCORE = 0.1
CAPABILITY_BORDERLINE_SCORE = 0.05
SEMANTIC_STRONG_SCORE = 0.18
SEMANTIC_GOOD_SCORE = 0.12
SEMANTIC_BASE_SCORE = 0.05
# 협회 가입(cohort) 축. 요건이 명시되지 않은 대다수 공고는 완전 중립이어야 하므로
# neutral 은 **반드시 0.0** 이다 — score/matched/blocking_axes 를 축 추가 전과
# byte-identical 로 유지해 baseline shift 를 막는다(license-axis 커버리지 비대칭
# 교훈). match 는 license 축과 대칭인 소폭 가점.
ASSOCIATION_NEUTRAL_SCORE = 0.0
ASSOCIATION_MATCH_SCORE = 0.05
# 기술부문(cohort) 축 — 협회 축과 동일 구조·동일값. 공고가 특정 기술부문
# (항만/해양/수로 = TECH_FIELD_TERMS)을 참가자격에 명시하면 미보유 operator 를
# 게이트한다. 요건 없는 공고는 반드시 완전 중립이어야 하므로 neutral 은 **0.0**
# (baseline 불변). match 는 협회/license 축과 대칭인 소폭 가점.
TECH_FIELD_NEUTRAL_SCORE = 0.0
TECH_FIELD_MATCH_SCORE = 0.05

# --- Mismatch penalties (negative contributions) -----------------------------
BUSINESS_TYPE_MISMATCH_PENALTY = 0.3
LICENSE_MISMATCH_PENALTY = 0.35
REGION_MISMATCH_PENALTY = 0.3
BUDGET_MISMATCH_PENALTY = 0.25
CAPABILITY_MISMATCH_PENALTY = 0.25
SEMANTIC_LOW_PENALTY = 0.18
SEMANTIC_VERY_LOW_PENALTY = 0.3
# 협회 요건이 명시된 공고에서 미가입 operator 를 거르는 hard 게이트 penalty
# (license 축과 동일 강도). 요건 없는 공고는 이 penalty 를 절대 부과하지 않는다.
ASSOCIATION_MISMATCH_PENALTY = 0.35
# 기술부문 요건이 명시된 공고에서 미보유 operator 를 거르는 hard 게이트 penalty
# (협회/license 축과 동일 강도). 요건 없는 공고엔 절대 부과하지 않는다.
TECH_FIELD_MISMATCH_PENALTY = 0.35

# --- Structural ratios & bonuses ---------------------------------------------
# Shared borderline acceptance ratio for the 시공능력평가액/연매출 budget branches:
# a capacity/revenue-to-budget ratio at or above this (but below 1×) is a
# conservative "타이트하지만 통과" borderline. The 3×/1× rungs above it are
# self-documenting natural ratios and stay inline.
BUDGET_BORDERLINE_RATIO = 0.6
# 낙찰 실적 가점: total_awards × AWARD_BONUS_PER_AWARD, capped at AWARD_BONUS_CAP,
# stacked onto the capacity_score capability estimate.
AWARD_BONUS_PER_AWARD = 0.03
AWARD_BONUS_CAP = 0.25

# --- Declarative tier ladders & structural thresholds ------------------------
# capacity_score budget fallback ladder (structural — the 0.8/0.4 boundaries
# are not operator-tuned). Each rung is (score, reason template); resolve_band
# walks it with the default ``>=`` compare, reproducing the original
# ``>= 0.8`` / ``>= 0.4`` / else cascade byte-for-byte. The ``float("-inf")``
# sentinel rung is the always-match fallback; its template carries no
# ``{capacity_score}`` placeholder (left untouched by ``.format``).
CAPACITY_SCORE_BUDGET_BANDS = (
    (
        0.8,
        (
            CAPACITY_HIGH_SCORE,
            "연매출 정보는 없지만 capacity_score={capacity_score:.2f}로 높아 예산 적합도에 보수적 가점을 반영했습니다.",
        ),
    ),
    (
        0.4,
        (
            CAPACITY_BASE_SCORE,
            "연매출 정보가 없어 capacity_score={capacity_score:.2f} 기준으로 예산 적합도를 보수 평가했습니다.",
        ),
    ),
    (
        float("-inf"),
        (0.0, "연매출 및 수행역량 정보가 부족해 예산 적합도는 보수적으로 평가했습니다."),
    ),
)
# project-scale ladder (structural) → semantic scope text. resolve_band's
# default ``>=`` compare reproduces the ``>= 0.85`` / ``>= 0.65`` / ``>= 0.5``
# cascade; the ``float("-inf")`` rung is the "기본 프로젝트" fallback.
PROJECT_SCOPE_BANDS = (
    (0.85, "대형 복합 프로젝트"),
    (0.65, "중대형 프로젝트"),
    (0.5, "중형 프로젝트"),
    (float("-inf"), "기본 프로젝트"),
)
# company-capability bands for the semantic scope text (describe_company_capability).
# The revenue ladder mixes comparators — ``>= 1e9`` / ``>= 3e8`` / ``> 0`` — so it
# stays a first-match PREDICATE table to preserve the ``> 0`` (positive-but-small)
# boundary exactly; folding the ``> 0`` rung into a ``>=`` threshold would
# misclassify a zero/absent revenue as "중형". The last always-true rung is the
# "매출 정보 부족" fallback.
REVENUE_CAPABILITY_BANDS: tuple[tuple[Callable[[float], bool], str], ...] = (
    (lambda revenue: revenue >= 1_000_000_000, "대형 프로젝트 대응 가능"),
    (lambda revenue: revenue >= 300_000_000, "중대형 프로젝트 대응 가능"),
    (lambda revenue: revenue > 0, "중형 프로젝트 대응 가능"),
    (lambda revenue: True, "매출 정보 부족"),
)
# award-history ladder (clean descending ``>=``) → resolve_band reproduces the
# ``>= 5`` / ``>= 2`` / else cascade; the ``float("-inf")`` rung is the
# "낙찰 실적 제한적" fallback.
AWARD_CAPABILITY_BANDS = (
    (5, "낙찰 실적 다수"),
    (2, "낙찰 실적 보유"),
    (float("-inf"), "낙찰 실적 제한적"),
)
