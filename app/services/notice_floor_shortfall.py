"""공고 한 건에 대한 하한 미달 빈도 노출 — 표본 캐시 + 공고 입력 해석.

왜 별도 모듈인가
----------------
:mod:`app.services.floor_shortfall` 는 "사정률 표본을 정직하게 골라 빈도를 센다"는
한 가지 일만 한다. 그 결과를 **운영자 대면 응답**에 실으려면 두 가지가 더 필요하다.

1. **공고에서 입력 두 개를 뽑는 해석** — 추천 투찰율(추천가 ÷ 기초금액)과 낙찰하한율
   (예정가 기준). 특히 하한율은 공고가 게시한 값·산림 예규·공사 era-tier 중 무엇을
   쓸지가 이미 선언된 우선순위(:func:`~app.ai.holdout_quality.resolve_legal_floor_rate`)로
   정해져 있으므로, 여기서 규칙을 다시 쓰지 않고 그 해석기를 호출만 한다(§4.5.8).
2. **요청 경로에서의 표본 재사용** — 표본 분포는 추첨 메커니즘이라 공고마다 달라지지
   않는데, 공고를 열 때마다 개찰 이력 수천 행을 다시 스캔하면 GET 이 그만큼 느려진다.
   그래서 적재 결과를 TTL 캐시로 재사용한다(TTL 은 Settings 선언 — §4.5.1).

정직 명세(§2)
-------------
이 모듈이 내보내는 값은 **확률이 아니라 과거 표본 비율**이며, 낼 수 없을 때는 0 이
아니라 사유가 붙은 "판정 불가"다. 특히 **하한율을 해석할 수 없는 공고**(게시값 없음 +
공사 tier 도 해석 불가)는 위험이 없는 것이 아니라 측정이 불가능한 것이므로, 표본을
읽지도 않고 사유를 붙여 돌려준다.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Final, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.floor_shortfall import MIN_ASSESSMENT_SAMPLES
from app.models.models import Project
from app.schemas.prediction import FloorShortfallEstimate
from app.services.bid_base import (
    resolve_notice_legal_floor_bid_rate,
    resolve_notice_legal_floor_inputs,
)
from app.services.floor_shortfall import (
    AssessmentRateSamples,
    build_floor_shortfall_estimate,
    load_assessment_rate_samples,
)

logger = logging.getLogger(__name__)

# 하한율을 해석하지 못해 표본을 아예 읽지 않았을 때의 scope/사유(선언 — 호출부가 문자열을
# 조립하지 않는다). "위험 없음"이 아니라 "측정 불가"임을 문구 자체가 말하게 둔다.
_SCOPE_NOT_LOADED: Final[str] = "표본 미조회(낙찰하한율 미해석)"
_REASON_FLOOR_UNRESOLVED: Final[str] = (
    "이 공고의 낙찰하한율을 해석할 수 없어(게시값 없음) 하한 미달 여부의 경계를 "
    "정의하지 못합니다. 위험이 없다는 뜻이 아닙니다."
)
_REASON_EXPOSURE_FAILED: Final[str] = (
    "하한 미달 빈도를 산출하는 중 오류가 발생해 값을 발표하지 않습니다. "
    "위험이 없다는 뜻이 아닙니다."
)


class AssessmentRateSampleCache:
    """카테고리별 사정률 표본 TTL 캐시 — 요청 경로의 반복 전표 스캔을 막는다.

    캐시 키는 카테고리다. 표본 자체는 과거 개찰 분포라 **공고 불변**이므로, 같은
    카테고리를 보는 모든 요청이 TTL 동안 한 번의 적재를 나눠 쓴다. 시계는 주입 가능해
    (``clock``) TTL 만료를 실제 대기 없이 테스트할 수 있다(§4.7.3).

    TTL 은 ``settings.FLOOR_SHORTFALL_SAMPLE_CACHE_TTL_SECONDS`` 선언값이다(§4.5.1).
    그 값은 곧 **새 개찰이 빈도에 반영되기까지의 지연**이며, 표본이 수천 건 규모라
    기본값(15분)의 지연은 빈도를 의미 있게 바꾸지 않는다.

    ``as_of``(시간 누수 차단 기준일)를 받는 경로는 캐시 대상이 아니다 — 기준일마다 키가
    갈라져 캐시가 의미를 잃고, 운영자 대면 라이브 조회는 항상 "현재까지의 개찰"을 본다.
    백테스트처럼 ``as_of`` 가 필요한 호출부는 :func:`load_assessment_rate_samples` 를
    직접 쓴다.
    """

    def __init__(
        self,
        *,
        ttl_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_override = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Optional[str], tuple[float, AssessmentRateSamples]] = {}

    @property
    def ttl_seconds(self) -> float:
        """유효 TTL(초). 생성 시 주입값이 없으면 Settings 선언값을 따른다."""
        if self._ttl_override is not None:
            return float(self._ttl_override)
        return float(settings.FLOOR_SHORTFALL_SAMPLE_CACHE_TTL_SECONDS)

    def get(
        self, db: Session, *, category: Optional[str] = None
    ) -> AssessmentRateSamples:
        """캐시된 표본을 주거나, 만료/부재면 적재해 채운 뒤 준다.

        적재는 락 밖에서 수행한다 — 읽기 전용 쿼리라 같은 키가 드물게 두 번 적재될 수
        있지만(둘 다 유효한 표본), DB 조회 동안 다른 카테고리 요청까지 막지 않는 편이
        요청 경로에서 낫다.
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(category)
            if entry is not None and now - entry[0] < self.ttl_seconds:
                return entry[1]

        samples = load_assessment_rate_samples(db, category=category)
        with self._lock:
            self._entries[category] = (self._clock(), samples)
        return samples

    def clear(self) -> None:
        """캐시를 비운다(테스트·운영 진단용)."""
        with self._lock:
            self._entries.clear()


# 프로세스 전역 캐시. 표본이 공고 불변이라 인스턴스를 나눠 가질 이유가 없고, 호출부는
# 필요하면 자기 캐시를 주입할 수 있다(기본 인자 seam).
SAMPLE_CACHE: Final[AssessmentRateSampleCache] = AssessmentRateSampleCache()


def resolve_notice_floor_rate(project: Project) -> Optional[float]:
    """공고에 적용되는 낙찰하한율(예정가 기준 분수)을 해석한다. 못 하면 ``None``.

    우선순위 규칙은 :func:`~app.ai.holdout_quality.resolve_legal_floor_rate` 가 이미
    선언·검증한 것을 그대로 쓴다(게시값 → 산림 예규 → 공사 era-tier). import 를 함수
    안에 두는 것은 ``app/ai`` 가 ml-builder 소유라 **읽기 전용 소비**임을 경계에서
    분명히 하려는 것이며, 계약이 깨지면 조용히 값이 사라지지 않도록 traceback 을 남기고
    ``None``(=판정 불가)으로 떨어진다.
    """
    try:
        from app.ai.floor_applicability import resolve_floor_applicability
        from app.ai.holdout_quality import resolve_legal_floor_rate

        estimation_amount, reference_date = resolve_notice_legal_floor_inputs(project)
        resolution = resolve_legal_floor_rate(
            published_floor_rate=resolve_notice_legal_floor_bid_rate(project),
            category=getattr(project, "category", None),
            estimation_amount=estimation_amount,
            reference_date=reference_date,
            floor_applicability=resolve_floor_applicability(
                getattr(project, "demand_agency", None)
                or getattr(project, "issuing_agency", None)
            ),
        )
        return resolution.rate
    except Exception as exc:  # pragma: no cover - defensive: keep the response graceful
        logger.warning(
            "낙찰하한율 해석 실패 (project %s): %s",
            getattr(project, "id", None),
            exc,
            exc_info=True,
        )
        return None


def _sample_scope_candidates(category: Optional[str]) -> tuple[Optional[str], ...]:
    """표본 스코프 시도 순서 — 카테고리 우선, 부족하면 전 카테고리.

    좁은 스코프가 더 관련성 높지만 최소 표본에 못 미치기 쉽다. 넓혀서 재시도하는 것이
    조용한 basis 교체가 되지 않는 이유는, 어느 스코프로 셌는지가 결과의 ``scope`` 에
    실려 응답까지 그대로 나가기 때문이다(감사 가능).
    """
    return (category, None) if category else (None,)


def estimate_notice_floor_shortfall(
    db: Session,
    project: Project,
    *,
    recommended_amount: float,
    bid_base_amount: float,
    cache: Optional[AssessmentRateSampleCache] = None,
) -> FloorShortfallEstimate:
    """이 공고에서 추천 투찰가가 하한 미달이 됐을 **과거 빈도**를 추정한다.

    Args:
        db: 읽기 전용 세션(표본 적재에만 쓰인다).
        project: 대상 공고 — 카테고리·게시 하한율·발주기관을 읽는다.
        recommended_amount: 추천 투찰금액(원).
        bid_base_amount: 그 금액이 곱해진 기초금액(``describe_notice_bid_base`` 결과).
            추천율은 **반드시 기초금액 기준**이어야 한다 — 추정가격으로 나눈 율을 넣으면
            과세 공고에서 율이 약 10% 부풀어 임계 사정률이 잘못 나온다(#162).
        cache: 표본 캐시(기본값은 프로세스 전역 캐시).

    Returns:
        항상 :class:`~app.schemas.prediction.FloorShortfallEstimate` 를 준다. 빈도를 낼
        수 없으면 ``shortfall_frequency`` 가 ``None`` 이고 ``unmeasurable_reason`` 이
        채워진다 — **"위험 없음"이 아니라 "판정 불가"** 다(§2).
    """
    floor_rate = resolve_notice_floor_rate(project)
    recommended_rate = (
        recommended_amount / bid_base_amount if bid_base_amount > 0 else 0.0
    )
    if floor_rate is None or floor_rate <= 0 or recommended_rate <= 0:
        return FloorShortfallEstimate(
            minimum_sample_count=MIN_ASSESSMENT_SAMPLES,
            scope=_SCOPE_NOT_LOADED,
            unmeasurable_reason=_REASON_FLOOR_UNRESOLVED,
        )

    estimate = _first_measurable_estimate(
        db,
        project,
        recommended_rate=recommended_rate,
        floor_rate=floor_rate,
        cache=cache or SAMPLE_CACHE,
    )
    if estimate is None:
        return FloorShortfallEstimate(
            minimum_sample_count=MIN_ASSESSMENT_SAMPLES,
            scope=_SCOPE_NOT_LOADED,
            unmeasurable_reason=_REASON_EXPOSURE_FAILED,
        )
    return estimate


def _first_measurable_estimate(
    db: Session,
    project: Project,
    *,
    recommended_rate: float,
    floor_rate: float,
    cache: AssessmentRateSampleCache,
) -> Optional[FloorShortfallEstimate]:
    """스코프를 넓혀 가며 첫 측정 가능한 추정을 찾는다. 적재 실패면 ``None``.

    전 스코프가 표본 부족이면 마지막(가장 넓은) 스코프의 판정 불가 사유를 그대로
    돌려준다. 표본 적재 자체가 실패하면 요약 응답 전체를 500 으로 떨어뜨리는 대신
    ``None`` 을 주고, 호출부가 그것을 "판정 불가"로 표기한다(침묵 0% 금지).
    """
    estimate: Optional[FloorShortfallEstimate] = None
    try:
        for category in _sample_scope_candidates(getattr(project, "category", None)):
            samples = cache.get(db, category=category)
            estimate = build_floor_shortfall_estimate(
                recommended_rate, floor_rate, samples
            )
            if estimate.shortfall_frequency is not None:
                return estimate
    except Exception as exc:  # pragma: no cover - defensive: keep the summary graceful
        logger.warning(
            "하한 미달 빈도 산출 실패 (project %s): %s",
            getattr(project, "id", None),
            exc,
            exc_info=True,
        )
        return None
    return estimate
