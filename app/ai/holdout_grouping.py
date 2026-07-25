"""기관(발주처/수요기관) 축 홀드아웃 분할 — 정규화 키·소표본 버킷 선언 해석기.

배경
----
로드맵 12번은 가격 레짐/selector 변경의 **필수 검증**으로 "기관/수요처 group holdout"을
요구하지만, 기존 최신 낙찰 홀드아웃(``scripts/backtest_latest_award_holdouts.py``)은
업종(공사/용역/물품) 축만 분할했다. 기관 축은 고카디널리티(로드맵 11번)라 raw 이름을
그대로 버킷 키로 쓰면 (a) 표기 흔들림으로 같은 기관이 여러 키로 갈라지고, (b) 1~2건짜리
꼬리 기관이 리포트를 채워 worst-case 판정이 표본 잡음에 지배된다.

이 모듈은 그 두 문제를 **데이터 규칙**으로 처리하는 순수 해석기다(§4.5.3 / §4.7.4).

* 표기 흔들림은 ``_AGENCY_NOISE_TOKENS`` 선언 테이블로 제거한다 — 토큰 추가 = 코드
  분기가 아니라 테이블 한 줄.
* 소표본 기관은 **침묵 제외하지 않고** 명시적 ``_etc`` 버킷으로 합산하며, 몇 개 기관
  / 몇 건이 그리로 갔는지 :class:`AgencyBucketing` 에 남긴다. 리포트 소비자가 "안 보이는
  표본"을 오해하지 않게 하려는 것(정직 명세 §2와 같은 축).

공백 제거·소문자화는 이미 예측 이력 매칭이 쓰는 :func:`normalize_agency_name` 에
있으므로 재구현하지 않고 위임한다(§4.5.6).

순수 함수(I/O 0) — 값 테이블로 단위 검증 가능하다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.ai.predictors.historical import normalize_agency_name

# 예약 버킷 키. ``_`` 접두는 정규화된 실제 기관명(공백 제거 후에도 ``_`` 로 시작하지
# 않음)과 충돌하지 않는 sentinel 이다.
AGENCY_UNKNOWN_KEY = "_unknown"
AGENCY_ETC_KEY = "_etc"
RESERVED_AGENCY_KEYS: tuple[str, ...] = (AGENCY_UNKNOWN_KEY, AGENCY_ETC_KEY)

# 기관 축 리포트에서 독립 버킷으로 남기기 위한 최소 표본 수. 미만은 ``_etc`` 로
# 합산된다(제외가 아니라 합산 — 건수는 리포트에 남는다).
DEFAULT_MIN_AGENCY_SAMPLES = 3

# 법인격/조직 표기 흔들림. 같은 기관이 서로 다른 키로 갈라지는 것을 막는 선언 테이블
# (새 표기 = 테이블 한 줄 추가). 지자체/공사/공단 같은 **조직 종류** 어미는 서로 다른
# 기관을 구분하는 정보라 제거하지 않는다 — 여기서 지우는 것은 법인격 접두/접미뿐이다.
_AGENCY_NOISE_TOKENS: tuple[str, ...] = (
    "주식회사",
    "(주)",
    "㈜",
    "재단법인",
    "(재)",
    "사단법인",
    "(사)",
    "유한회사",
    "(유)",
    "특수법인",
)


def normalize_agency_key(value: Any) -> str:
    """기관명을 그룹 분할 키로 정규화한다. 값이 비면 빈 문자열.

    공백 제거 + 소문자화는 :func:`normalize_agency_name` (예측 이력 매칭이 쓰는 기존
    헬퍼)에 위임하고, 그 위에 법인격 표기 토큰만 선언 테이블로 제거한다.
    """
    normalized = normalize_agency_name(value)
    for token in _AGENCY_NOISE_TOKENS:
        normalized = normalized.replace(token, "")
    return normalized


@dataclass(frozen=True)
class AgencyGroup:
    """기관 축 분할 키 + 사람이 읽을 원문 표기."""

    key: str
    display: str


def resolve_agency_group(
    issuing_agency: Any = None, demand_agency: Any = None
) -> AgencyGroup:
    """발주기관 우선, 없으면 수요기관으로 기관 그룹을 해석한다.

    둘 다 비면 ``_unknown`` 으로 떨어진다 — 미상 표본을 조용히 버리지 않고 명시
    버킷에 모아 리포트에 남기기 위한 것이다.
    """
    for raw in (issuing_agency, demand_agency):
        key = normalize_agency_key(raw)
        if key:
            return AgencyGroup(key=key, display=str(raw).strip())
    return AgencyGroup(key=AGENCY_UNKNOWN_KEY, display=AGENCY_UNKNOWN_KEY)


def resolve_agency_match_keys(*values: Any) -> frozenset[str]:
    """기관 동일성 판정용 정규화 키 **집합**(빈 값 제외).

    왜 단일 키가 아니라 집합인가 — ``HistoricalData.agency_name`` 은 수집 시점에
    ``opening_demand_agency or demand_agency or issuing_agency`` 순서로 **하나만**
    적재된다(``app/services/koneps/persistence.py``). 반면 그룹 분할 키
    (:func:`resolve_agency_group`)는 발주기관 우선이다. 발주≠수요 공고(조달청 경유
    등 흔함)에서는 그 둘이 갈리므로, 단일 키로 과거 이력을 필터하면 같은 기관의
    낙찰이 다른 이름으로 저장돼 필터를 통과한다 = 누수.

    그래서 타깃의 발주기관·수요기관에 더해 타깃 자신의 ``agency_name``(그 공고에
    실제 적재된 값 = opening 수요기관까지 반영된 값)을 모두 키로 만들어 집합으로
    비교한다.
    """
    return frozenset(
        key for key in (normalize_agency_key(value) for value in values) if key
    )


@dataclass(frozen=True)
class AgencyBucketing:
    """소표본 합산 결과 + 합산 규모(리포트 투명성용)."""

    min_samples: int
    counts: Mapping[str, int]  # 정규화 키 → 표본 수(합산 전)
    mapping: Mapping[str, str]  # 정규화 키 → 리포트 버킷 키(자기 자신 또는 ``_etc``)
    distinct_agency_count: int  # 합산 전 서로 다른 키 수(예약 키 포함)
    reported_agency_count: int  # 독립 버킷으로 남은 키 수
    etc_agency_count: int  # ``_etc`` 로 합산된 키 수
    etc_sample_count: int  # ``_etc`` 로 합산된 표본 수

    def bucket_for(self, key: Any) -> str:
        """정규화 키의 리포트 버킷을 돌려준다(미등록 키는 ``_unknown``)."""
        normalized = str(key or "") or AGENCY_UNKNOWN_KEY
        return self.mapping.get(normalized, AGENCY_UNKNOWN_KEY)


def bucket_small_agencies(
    keys: Iterable[Any], *, min_samples: int = DEFAULT_MIN_AGENCY_SAMPLES
) -> AgencyBucketing:
    """표본 수가 ``min_samples`` 미만인 기관을 ``_etc`` 버킷으로 합산한다.

    예약 키(``_unknown``/``_etc``)는 이미 명시적 잔여 버킷이므로 합산 대상에서
    제외한다 — ``_unknown`` 이 표본 부족으로 ``_etc`` 에 섞이면 "기관 미상"과
    "표본 부족"이 구분되지 않는다.
    """
    counts: dict[str, int] = {}
    for key in keys:
        normalized = str(key or "") or AGENCY_UNKNOWN_KEY
        counts[normalized] = counts.get(normalized, 0) + 1

    threshold = max(1, int(min_samples or 1))
    mapping: dict[str, str] = {}
    etc_agency_count = 0
    etc_sample_count = 0
    for key, count in counts.items():
        if key in RESERVED_AGENCY_KEYS or count >= threshold:
            mapping[key] = key
            continue
        mapping[key] = AGENCY_ETC_KEY
        etc_agency_count += 1
        etc_sample_count += count

    return AgencyBucketing(
        min_samples=threshold,
        counts=counts,
        mapping=mapping,
        distinct_agency_count=len(counts),
        reported_agency_count=sum(
            1 for key, bucket in mapping.items() if bucket == key
        ),
        etc_agency_count=etc_agency_count,
        etc_sample_count=etc_sample_count,
    )
