"""예정가↔기초금액 basis 변환 — 단일 출처(#195 재도입 위험 차단).

E[사정률] = E[예정가/기초금액] 변환의 **적용(곱셈)**을 한 함수로 모은다.

배경: 발주처 밴드(PREDICTION_AGENCY_*_BID_RATES)는 낙찰가/예정가로 캘리브레이션됐지만
price guardrail은 이를 공고 사업금액(기초금액, #162)에 곱한다. 예정가는 기초금액보다
몇 %의 수십분의 일 낮으므로, 예정가-basis 율을 기초금액에 곱하면 ~+0.5%p 높게 뜬다
(postmortem 52위/25위). 그래서 밴드에 E[사정률](통상 < 1)을 곱해 예정가-basis 목표로
되돌린다:

    기초금액-기준 목표율 = 예정가-기준 밴드율 × E[사정률]

이 곱셈은 guardrail_core에서 3곳(construction tier floor · agency floor · agency
ceiling)에 반복됐고, "4번째 site를 잊으면 조용한 +0.5%p"(deep-reasoner, #195 재도입
위험)가 실재하는 함정이었다. :func:`convert_yega_band_to_base`가 곱셈+clamp를 단일
진입점으로 통합해 그 함정을 구조적으로 차단한다. 의미·clamp·경계는 기존 인라인과
100% 동일하다(단순 추출, golden diff 0).

Red line(guardrail 우회 금지) 불변: 이 변환은 밴드 edge를 균일하게 이동시킬 뿐,
guardrail의 max(카테고리/그룹/법정 낙찰하한) 하드 바운드를 바꾸지 않는다. 변환 결과가
하드 하한을 하회할 수 없다 — ``resolve_floor_bid_rate``가 category/group/legal floor를
max()로 재적용하기 때문이다.

의존 방향: guardrail_core → 이 모듈(정방향)만 허용. 이 모듈은 guardrail_core를 import
하지 않는다(역방향/순환 금지). config 표면은 :class:`BandAssessmentConfig` Protocol로
구조적으로만 선언해 ``GuardrailConfig`` 직접 import을 피한다.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from app.ai.predictors.historical import clamp_bid_rate, normalize_agency_name


class BandAssessmentConfig(Protocol):
    """E[사정률] 변환이 읽는 config 표면만 선언(구조적 타이핑).

    ``app.ai.guardrail_core.GuardrailConfig``(frozen dataclass)가 이 표면을 구조적으로
    만족한다. 여기서 GuardrailConfig를 직접 import하면 guardrail_core→basis_conversion
    정방향 의존과 충돌하는 역방향 import가 되므로, 계약(Protocol)만 선언한다.
    """

    agency_band_assessment_rates: Mapping[str, float] | None
    default_band_assessment_rate: float


def resolve_agency_bid_rate(
    agency_name: str | None,
    rate_map: Mapping[str, float] | None,
) -> float | None:
    """Look up an agency-keyed bid-rate band via normalized substring match.

    Keys are normalized agency tokens (whitespace-stripped, lowercased — see
    normalize_agency_name). A notice's issuing agency matches a key when the
    normalized key is a substring of the normalized agency name, so regional
    bureaus inherit the headquarters band (e.g. "한국수산자원공단동해본부" matches
    the "한국수산자원공단" key). When several keys match, the most specific
    (longest) key wins.
    """
    if not agency_name or not rate_map:
        return None
    normalized_agency = normalize_agency_name(agency_name)
    if not normalized_agency:
        return None
    best_rate: float | None = None
    best_key_len = -1
    for raw_key, raw_rate in rate_map.items():
        normalized_key = normalize_agency_name(raw_key)
        if not normalized_key or normalized_key not in normalized_agency:
            continue
        if len(normalized_key) > best_key_len:
            best_key_len = len(normalized_key)
            best_rate = max(0.0, float(raw_rate or 0.0))
    return best_rate


def resolve_band_assessment_rate(
    agency_name: str | None,
    config: BandAssessmentConfig,
) -> float:
    """E[예정가/기초금액] — converts a 예정가-basis agency band to a 기초금액 basis.

    The agency bands (PREDICTION_AGENCY_*_BID_RATES) were calibrated as 낙찰가/예정가,
    but the guardrail multiplies them by the notice 사업금액(기초금액, #162). 예정가 is a
    few tenths of a percent BELOW 기초금액, so a 예정가-basis rate applied to 기초금액
    lands ~+0.5%p too high. Multiply the agency band by E[사정률] (< 1) to recover the
    intended 예정가-basis target.

    Resolution mirrors the band lookup: per-agency empirical rate via normalized
    substring match, else the global default (1.0 == no-op). Any missing / non-positive
    value collapses to 1.0. With the shipped rates (all ≤ 1) the conversion only LOWERS
    the band; a configured 사정률 > 1 is legitimate (복수예비가격 추첨 can put 예정가
    ABOVE 기초금액 for some agencies) and would raise it — either way the red line is
    unaffected: resolve_floor_bid_rate re-applies max(category/group floor, converted
    agency floor) plus the legal 낙찰하한, so no value here can undercut the hard floor.
    """
    rate = resolve_agency_bid_rate(agency_name, config.agency_band_assessment_rates)
    if rate is not None and rate > 0:
        return rate
    default_rate = config.default_band_assessment_rate
    return default_rate if default_rate and default_rate > 0 else 1.0


def convert_yega_band_to_base(
    rate: float,
    agency_name: str | None,
    config: BandAssessmentConfig,
) -> float:
    """예정가-basis 밴드율을 기초금액 basis로 변환(+clamp) — guardrail 3곳 단일 진입점.

    = ``clamp_bid_rate(rate × E[사정률])``. guardrail_core의 construction tier floor·
    agency floor·agency ceiling이 모두 이 함수를 거친다. 곱셈 site를 하나 잊어 +0.5%p
    새는 함정(#195 재도입)을 구조적으로 차단하는 것이 목적이며, clamp/경계/의미는 기존
    인라인 ``clamp_bid_rate(X * resolve_band_assessment_rate(...))``와 바이트 동일하다.

    clamp_bid_rate가 결과를 hard [0.7, 1.4] 밴드로 묶으므로 이 함수 단독으로는 법정
    하한을 보장하지 않는다(우회 금지). 최종 floor 보장은 호출부 resolve_floor_bid_rate의
    max(category/group/legal floor)가 담당한다.
    """
    return clamp_bid_rate(rate * resolve_band_assessment_rate(agency_name, config))
