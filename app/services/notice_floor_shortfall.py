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
아니라 사유가 붙은 "판정 불가"다. 판정을 포기하는 경우는 서로 다른 사유로 갈라 둔다 —
투찰율을 만들 수 없음(기초금액/추천금액 무효) · 하한 모델 적용 대상이 아님(기관 유형) ·
하한율 미해석(게시값 없음 + 공사 tier 도 해석 불가) · 산출 오류. 어느 쪽도 위험이 없는
것이 아니라 측정이 불가능한 것이므로, 표본을 읽지도 않고 사유를 붙여 돌려준다.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Final, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.floor_shortfall import MIN_ASSESSMENT_SAMPLES
from app.models.models import Project
from app.schemas.prediction import FloorShortfallEstimate
from app.services.bid_base import (
    resolve_notice_legal_floor_inputs,
    resolve_notice_published_floor_bid_rate,
)
from app.services.floor_shortfall import (
    AssessmentRateSamples,
    build_floor_shortfall_estimate,
    load_assessment_rate_samples,
)

logger = logging.getLogger(__name__)

# 표본을 아예 읽지 않고 판정을 포기할 때의 scope/사유(선언 — 호출부가 문자열을 조립하지
# 않는다). 사유는 **왜 못 쟀는지에 따라 갈린다** — "하한율을 못 구했다"와 "투찰율을 못
# 구했다"와 "이 기관에는 하한 모델이 없다"는 운영자가 취할 다음 행동이 서로 다르다.
# 어느 것도 "위험 없음"이 아니라 "측정 불가"임을 문구 자체가 말하게 둔다.
_SCOPE_NO_FLOOR: Final[str] = "표본 미조회(낙찰하한율 미해석)"
_SCOPE_FLOOR_NOT_APPLICABLE: Final[str] = "표본 미조회(적격심사 하한 모델 미적용)"
_SCOPE_INVALID_INPUTS: Final[str] = "표본 미조회(투찰율 산출 불가)"
_SCOPE_EXPOSURE_FAILED: Final[str] = "표본 미조회(산출 오류)"

_REASON_FLOOR_UNRESOLVED: Final[str] = (
    "이 공고의 낙찰하한율을 해석할 수 없어(게시값 없음·공사 tier 미해석) 하한 미달 "
    "여부의 경계를 정의하지 못합니다. 위험이 없다는 뜻이 아닙니다."
)
_REASON_FLOOR_NOT_APPLICABLE: Final[str] = (
    "이 발주기관 유형에는 적격심사 낙찰하한 모델이 적용되지 않거나 적용 여부가 "
    "불확실해 하한 미달 여부를 판정하지 않습니다. 위험이 없다는 뜻이 아닙니다."
)
_REASON_INVALID_BID_INPUTS: Final[str] = (
    "추천 투찰금액 또는 기초금액이 유효하지 않아 투찰율을 산출할 수 없습니다. "
    "위험이 없다는 뜻이 아닙니다."
)
_REASON_EXPOSURE_FAILED: Final[str] = (
    "하한 미달 빈도를 산출하는 중 오류가 발생해 값을 발표하지 않습니다. "
    "위험이 없다는 뜻이 아닙니다."
)


@dataclass(frozen=True)
class NoticeFloorRate:
    """해석된 낙찰하한율, 또는 왜 해석하지 못했는지(사유 + 스코프).

    ``rate`` 가 ``None`` 이면 판정 불가이며, 그 이유가 ``reason`` 에 담긴다 — 호출부가
    "하한을 못 구했다"는 한 가지 문구로 뭉뚱그리지 못하게 사유를 값과 함께 옮긴다.
    """

    rate: Optional[float]
    reason: Optional[str] = None
    scope: str = _SCOPE_NO_FLOOR


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


def _agency_floor_applicability(project: Project) -> str:
    """적용 범위 판정의 기관 축 — **발주기관 우선, 없으면 수요기관**.

    :func:`~app.ai.holdout_grouping.resolve_agency_group` 컨벤션을 따른다. 조달청 경유
    공고처럼 발주≠수요인 건에서 하한 모델의 적용 여부를 가르는 것은 계약 주체인
    발주기관이기 때문이다.
    """
    from app.ai.floor_applicability import resolve_floor_applicability

    return resolve_floor_applicability(
        getattr(project, "issuing_agency", None)
        or getattr(project, "demand_agency", None)
    )


def resolve_notice_floor_rate(project: Project) -> NoticeFloorRate:
    """공고에 적용되는 낙찰하한율(예정가 기준 분수)을 해석한다.

    두 게이트를 순서대로 통과해야 값이 나온다.

    1. **적용 범위**(:func:`~app.ai.floor_applicability.is_floor_judgeable`, #274) —
       비국가기관(산학협력단·협동조합 등)이나 이름만으로 가릴 수 없는 부류(대학교)는
       어떤 하한으로 재야 하는지 근거가 없어 판정하지 않는다. 홀드아웃 품질 판정
       (``holdout_quality._is_floor_comparable``)이 쓰는 것과 **같은 술어**다 — 규칙이
       두 벌이 되면 분석과 표시가 서로 다른 공고를 판정하게 된다(§4.5.8).
    2. **하한 해석**(:func:`~app.ai.holdout_quality.resolve_legal_floor_rate`) —
       게시값 → 산림 예규 → 공사 era-tier 우선순위를 그대로 쓴다.

    ``app/ai`` import 를 함수 안에 두는 것은 그쪽이 ml-builder 소유라 **읽기 전용 소비**
    임을 경계에서 분명히 하려는 것이며, 계약이 깨지면 조용히 값이 사라지지 않도록
    traceback 을 남기고 판정 불가로 떨어진다.
    """
    try:
        from app.ai.floor_applicability import is_floor_judgeable
        from app.ai.holdout_quality import resolve_legal_floor_rate

        applicability = _agency_floor_applicability(project)
        if not is_floor_judgeable(applicability):
            return NoticeFloorRate(
                None, _REASON_FLOOR_NOT_APPLICABLE, _SCOPE_FLOOR_NOT_APPLICABLE
            )

        estimation_amount, reference_date = resolve_notice_legal_floor_inputs(project)
        resolution = resolve_legal_floor_rate(
            # 게이트를 걸지 않은 원값을 넘긴다: 개연 범위 판정은 아래 리졸버가 수행하고,
            # 범위 밖 값은 버려지는 게 아니라 published_floor_implausible 로 계수된다.
            published_floor_rate=resolve_notice_published_floor_bid_rate(project),
            category=getattr(project, "category", None),
            estimation_amount=estimation_amount,
            reference_date=reference_date,
            floor_applicability=applicability,
        )
        if resolution.rate is None or resolution.rate <= 0:
            return NoticeFloorRate(None, _REASON_FLOOR_UNRESOLVED, _SCOPE_NO_FLOOR)
        return NoticeFloorRate(float(resolution.rate))
    except Exception as exc:  # pragma: no cover - defensive: keep the response graceful
        logger.warning(
            "낙찰하한율 해석 실패 (project %s): %s",
            getattr(project, "id", None),
            exc,
            exc_info=True,
        )
        return NoticeFloorRate(None, _REASON_EXPOSURE_FAILED, _SCOPE_EXPOSURE_FAILED)


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
    # 투찰율을 못 만드는 것과 하한율을 못 구하는 것은 서로 다른 실패다 — 사유를 합치면
    # 운영자가 "공고에 하한이 안 붙었나 보다"로 잘못 읽는다.
    if bid_base_amount <= 0 or recommended_amount <= 0:
        return _unmeasurable(_REASON_INVALID_BID_INPUTS, _SCOPE_INVALID_INPUTS)

    floor = resolve_notice_floor_rate(project)
    if floor.rate is None:
        return _unmeasurable(floor.reason or _REASON_FLOOR_UNRESOLVED, floor.scope)

    estimate = _first_measurable_estimate(
        db,
        project,
        recommended_rate=recommended_amount / bid_base_amount,
        floor_rate=floor.rate,
        cache=cache or SAMPLE_CACHE,
    )
    if estimate is None:
        return _unmeasurable(_REASON_EXPOSURE_FAILED, _SCOPE_EXPOSURE_FAILED)
    return estimate


def _unmeasurable(reason: str, scope: str) -> FloorShortfallEstimate:
    """표본을 읽지 않고 판정을 포기한 결과 — 빈도는 ``None``, 사유는 반드시 붙는다."""
    return FloorShortfallEstimate(
        minimum_sample_count=MIN_ASSESSMENT_SAMPLES,
        scope=scope,
        unmeasurable_reason=reason,
    )


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
