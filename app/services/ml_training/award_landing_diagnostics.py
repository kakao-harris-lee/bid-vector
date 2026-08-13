"""착지 코퍼스 진단 — 상관·f 분포·음수 마진 크기·**인용 수치 대조** (순수).

왜 인용 수치를 대조하는가
--------------------------
설계 문서(#369)와 PR1 본문은 코퍼스 실측값을 여럿 인용하지만, 그 값들은 세션 스크래치
패드 쿼리에서 나왔고 **저장소 코드로 재현된 적이 없다**(PR2 백로그 ②). 재현 없이 인용만
누적되면 나중 판정이 "그때 그 수치"에 기대게 되는데, 그 수치가 어떤 코퍼스 정의 위에서
나왔는지는 아무도 다시 세울 수 없다. 그래서 이 모듈은 인용값을 **선언 표**(:data:
`QUOTED_REFERENCES`)로 두고 재산출값을 그 옆에 나란히 싣는다 — 판단은 리포트를 읽는
사람이 한다(자동 통과/실패 판정을 붙이지 않는다. 코퍼스 정의가 다르면 값이 달라야
정상이고, 그 차이를 "실패"로 찍으면 사다리를 인용값에 맞추게 된다).

**이 표가 하는 일은 "재현"이 아니라 "다른 코퍼스에서의 재산출"이다.** 인용값의 모집단은
:attr:`QuotedReference.scope` 가 신고하는 대로 **floor_bound 레짐 게이트를 적용하지 않은**
2,152행이고, 이 리포트의 기본 코퍼스는 그 게이트를 적용한 부분집합이다. 두 수가 같아야
할 이유가 없으므로, 값이 붙으면 "모집단 차이가 그 항목엔 작았다"는 뜻이고 갈리면 "그
항목이 게이트에 민감했다"는 뜻이다 — 어느 쪽도 그 자체로 결함이 아니다. 예: ``corr(a,Δ)``
는 인용 −0.0045 대비 재산출이 한 자릿수 배 크지만 **부호가 같고 여전히 |corr| < 0.05**
이며, 애초에 층 A 접지 진리는 이 상관과 무관하다(결합추정).

상관은 무엇을 말하는가
-----------------------
* ``corr(a, Δ)`` — 층 C 합성이 쓰는 **유일한 가정**(``a ⊥ Δ``)의 진단. 층 A 접지 진리는
  결합추정이라 이 값이 흔들려도 채점은 흔들리지 않는다(설계 §5.1 M1).
* ``corr(f, Δ)`` — 식 3(f-재기준)이 **새로 얹는 가정**(``Δ ⊥ f``)의 진단. 설계는 이쪽이
  훨씬 약한 가정이라고 명시했고(NEW-4), era tier 셀 키가 그 교란을 걷어내는지가 F1 이다.
* ``corr(a, ω)`` — 폐기된 층 B 의 편향 크기. 강한 양의 상관이 "생존항만 정확 CDF 로
  적분"이 왜 ``WW`` 를 과대추정하는지의 근거다(설계 §5.1 M1).

순수 함수(I/O 0). 상관은 stdlib :func:`statistics.correlation` 을 쓴다 — 같은 산술을
다시 구현하지 않는다(§4.5-8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import StatisticsError, correlation, median
from typing import Final

from app.schemas._base import StrictModel
from app.services.ml_training.award_landing_ladder import (
    LandingRow,
    NegativeMarginAudit,
)

__all__ = [
    "NEGATIVE_MARGIN_MAGNITUDE_EDGES",
    "QUOTED_REFERENCES",
    "AssessmentSummary",
    "CorrelationSummary",
    "NegativeMarginSummary",
    "QuotedComparison",
    "QuotedReference",
    "build_assessment_summary",
    "build_correlation_summary",
    "build_negative_margin_summary",
    "compare_to_quoted",
    "median_or_none",
    "pearson",
]

# 음수 마진 크기의 구간 경계(rate 단위). 커널 ``NEGATIVE_MARGIN_EPSILON`` 재조정(PR2
# 백로그 ⑥)의 판단은 "크기가 어느 규모에 몰려 있는가" 하나로 갈린다:
#
#   * ≲1e-5 — 입력 rate 의 양자화 오차 규모. 이 구간뿐이면 ε 을 넓혀도 삼키는 것이
#     표현 잡음뿐이고, 지금 거부되는 표본은 하필 **스파이크 최하단**이라 거부가 낙관
#     방향으로 작동한다(커널 상수 주석).
#   * ≥1e-3 — basis 오염·era 불일치 규모. 이 구간이 있으면 ε 을 넓히는 순간 진짜 오염이
#     0+ 스파이크로 위장돼 사라진다. **넓히지 않는 근거**가 된다.
NEGATIVE_MARGIN_MAGNITUDE_EDGES: Final[tuple[float, ...]] = (
    1e-6,
    1e-5,
    1e-4,
    1e-3,
    1e-2,
)


@dataclass(frozen=True)
class QuotedReference:
    """설계 문서·PR1 본문이 인용한 실측값 한 줄(§4.5-3 선언 데이터).

    ``sample_count`` 와 ``scope`` 는 값만큼 중요하다 — 같은 이름의 수치가 다른 코퍼스
    정의 위에서 나왔다면 재산출값과 달라야 정상이고, 그 판단은 스코프를 봐야 선다.
    """

    name: str
    value: float
    sample_count: int
    scope: str


# 인용값 모집단의 provenance — **전부 같은 한 줄**이라 상수로 둔다(§4.5-1).
# 설계 §4 의 셀 깊이표는 "하드 필터 전 상한"이라고 스스로 헤지하지만 §6·§7 의 상관·
# 실격률·f 분포 인용에는 그 헤지가 없다. 그 수치들은 floor_bound 레짐 게이트를
# **적용하지 않은** 모집단에서 나왔고, 그래서 게이트를 적용한 이 리포트의 기본
# 코퍼스와 값이 갈리는 것이 정상이다. provenance 를 값 옆에 붙여 그 사실이 대조표를
# 읽는 자리에서 바로 보이게 한다.
_UNGATED: Final[str] = " · floor_bound 게이트 미적용 모집단"

# 인용 수치 표 — 설계 §4·§5.1·§6·§7 과 PR1 본문에서 그대로 옮겨 왔다. 값을 여기 모으는
# 이유는 리포트가 "무엇과 비교했는지"를 스스로 신고하게 하려는 것이다.
QUOTED_REFERENCES: Final[tuple[QuotedReference, ...]] = (
    QuotedReference(
        "corr_assessment_margin", -0.0045, 2152, f"clean 2,152{_UNGATED} (설계 §6)"
    ),
    QuotedReference(
        "corr_assessment_omega", 0.587, 2152, f"clean 2,152{_UNGATED} (설계 §5.1 M1)"
    ),
    QuotedReference(
        "corr_floor_margin_construction",
        -0.155,
        1409,
        f"clean 공사{_UNGATED} (설계 NEW-4)",
    ),
    QuotedReference(
        "corr_floor_margin_service", -0.595, 743, f"clean 용역{_UNGATED} (설계 NEW-4)"
    ),
    QuotedReference(
        "naive_argmax_disqualification_construction",
        0.325,
        1405,
        f"층 A argmax 0.89775{_UNGATED} (설계 §7 M2)",
    ),
    QuotedReference(
        "naive_argmax_disqualification_service",
        0.526,
        743,
        f"층 A argmax 0.87825{_UNGATED} (설계 §7 M2)",
    ),
    QuotedReference(
        "service_floor_share_088",
        0.668,
        743,
        f"용역 셀 f=0.88 점유{_UNGATED} (설계 §6 M5)",
    ),
    QuotedReference(
        "negative_margin_count",
        4.0,
        2152,
        f"Δ<0 건수 — 전부 f==1.0{_UNGATED} (설계 §4 m1)",
    ),
    QuotedReference(
        "assessment_mean", 0.9913, 2152, f"clean 2,152 사정률 평균{_UNGATED} (설계 §2 m5)"
    ),
    QuotedReference(
        "assessment_median",
        0.9975,
        2152,
        f"clean 2,152 사정률 중앙값{_UNGATED} (설계 §2 m5)",
    ),
    QuotedReference(
        "assessment_above_one_share",
        0.370,
        2152,
        f"clean 2,152 a>1 비율{_UNGATED} (설계 §2 m5)",
    ),
    QuotedReference(
        "median_margin_construction",
        0.00555,
        0,
        f"공사 하한 대비 중앙값 +55.5bp{_UNGATED} (설계 §2)",
    ),
    QuotedReference(
        "median_margin_service",
        0.00133,
        0,
        f"용역 하한 대비 중앙값 +13.3bp{_UNGATED} (설계 §2)",
    ),
)


class QuotedComparison(StrictModel):
    """인용값 ↔ 재산출값 한 줄. ``recomputed`` 가 ``None`` 이면 **못 쟀다**는 뜻이다."""

    name: str
    quoted: float
    quoted_sample_count: int
    quoted_scope: str
    recomputed: float | None
    recomputed_sample_count: int
    difference: float | None


class CorrelationSummary(StrictModel):
    """세 상관과 그 표본 수 — 가정 진단(§6·§5.1 M1·NEW-4)."""

    sample_count: int
    corr_assessment_margin: float | None
    corr_assessment_omega: float | None
    corr_floor_margin: float | None


class AssessmentSummary(StrictModel):
    """사정률 축 요약 — 추첨 게임의 형태를 리포트가 자기 코퍼스에서 다시 잰다."""

    sample_count: int
    mean: float | None
    median_value: float | None
    above_one_share: float | None


class NegativeMarginSummary(StrictModel):
    """``Δ<0`` 감사 — 건수·f==1.0 점유·크기 구간 분포(ε 재조정 근거, 백로그 ⑥)."""

    count: int
    unity_floor_count: int
    unity_floor_share: float | None
    max_magnitude: float | None
    magnitude_buckets: dict[str, int]
    distinct_floor_rates: list[float]


def pearson(pairs: Sequence[tuple[float, float]]) -> float | None:
    """표본 상관계수. 표본이 2 미만이거나 한 축이 상수면 ``None``(**0 이 아니다**).

    0 으로 채우면 "상관이 없다"는 결론이 되고, 실제로는 "잴 수 없었다"이다.
    """
    if len(pairs) < 2:
        return None
    try:
        return correlation([x for x, _ in pairs], [y for _, y in pairs])
    except StatisticsError:
        return None


def build_correlation_summary(rows: Sequence[LandingRow]) -> CorrelationSummary:
    """``corr(a,Δ)`` · ``corr(a,ω)`` · ``corr(f,Δ)`` 를 같은 표본에서 함께 낸다."""
    return CorrelationSummary(
        sample_count=len(rows),
        corr_assessment_margin=pearson(
            [(row.assessment_ratio, row.margin) for row in rows]
        ),
        corr_assessment_omega=pearson(
            [(row.assessment_ratio, row.omega_direct) for row in rows]
        ),
        corr_floor_margin=pearson([(row.floor_rate, row.margin) for row in rows]),
    )


def build_assessment_summary(rows: Sequence[LandingRow]) -> AssessmentSummary:
    """사정률 평균·중앙값·``a>1`` 비율."""
    values = [row.assessment_ratio for row in rows]
    if not values:
        return AssessmentSummary(
            sample_count=0, mean=None, median_value=None, above_one_share=None
        )
    return AssessmentSummary(
        sample_count=len(values),
        mean=sum(values) / len(values),
        median_value=median(values),
        above_one_share=sum(1 for value in values if value > 1.0) / len(values),
    )


def _magnitude_bucket(magnitude: float) -> str:
    """크기 → 구간 라벨. 경계는 **상한 미만**(``< edge``)이다."""
    previous = "0"
    for edge in NEGATIVE_MARGIN_MAGNITUDE_EDGES:
        if magnitude < edge:
            return f"{previous}~{edge:g}"
        previous = f"{edge:g}"
    return f">={previous}"


def build_negative_margin_summary(
    audits: Sequence[NegativeMarginAudit], *, unity_floor_rate: float
) -> NegativeMarginSummary:
    """``Δ<0`` 감사 요약 — 설계 m1("전부 f==1.0")의 재현 여부가 여기서 갈린다."""
    if not audits:
        return NegativeMarginSummary(
            count=0,
            unity_floor_count=0,
            unity_floor_share=None,
            max_magnitude=None,
            magnitude_buckets={},
            distinct_floor_rates=[],
        )
    unity = sum(
        1 for audit in audits if audit.floor_rate_fraction == unity_floor_rate
    )
    buckets: dict[str, int] = {}
    for audit in audits:
        label = _magnitude_bucket(audit.magnitude)
        buckets[label] = buckets.get(label, 0) + 1
    return NegativeMarginSummary(
        count=len(audits),
        unity_floor_count=unity,
        unity_floor_share=unity / len(audits),
        max_magnitude=max(audit.magnitude for audit in audits),
        magnitude_buckets=dict(sorted(buckets.items())),
        distinct_floor_rates=sorted({audit.floor_rate_fraction for audit in audits}),
    )


def compare_to_quoted(
    recomputed: dict[str, tuple[float | None, int]],
    *,
    references: Sequence[QuotedReference] = QUOTED_REFERENCES,
) -> list[QuotedComparison]:
    """선언 인용표와 재산출값을 나란히 놓는다(판정 없음 — 값만 병기).

    Args:
        recomputed: 인용 이름 → ``(재산출값 | None, 재산출 표본 수)``. 이름이 표에 없는
            항목은 **버리지 않고 무시한다** — 표가 단일 출처이므로 표에 없는 이름을
            리포트에 실으면 두 벌이 된다.
    """
    rows: list[QuotedComparison] = []
    for reference in references:
        value, sample_count = recomputed.get(reference.name, (None, 0))
        rows.append(
            QuotedComparison(
                name=reference.name,
                quoted=reference.value,
                quoted_sample_count=reference.sample_count,
                quoted_scope=reference.scope,
                recomputed=value,
                recomputed_sample_count=sample_count,
                difference=None if value is None else value - reference.value,
            )
        )
    return rows


def median_or_none(values: Sequence[float]) -> float | None:
    """빈 표본에서 예외 대신 ``None`` — "0"과 "못 쟀다"를 가른다."""
    return median(values) if values else None
