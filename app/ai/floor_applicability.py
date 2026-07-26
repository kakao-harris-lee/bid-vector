"""법정 낙찰하한 모델의 **적용 범위** 판별 — 발주기관 유형 선언 패턴 테이블.

배경(라이브 기준선 2026-07-26)
------------------------------
``holdout_quality`` 의 ``below_legal_floor`` 플래그를 기관 축 홀드아웃 2,798건에
처음 돌렸더니 49건이 잡혔는데, 상당수가 **실제 위법 낙찰이 아니라 하한 모델의 적용
범위 밖 공고에 국가계약 era-tier 를 일괄 적용해 생긴 오탐**이었다.

* 산학협력단·농업협동조합 등 비국가기관 발주(낙찰률 0.436~0.739)에 국가계약 공사
  적격심사 tier(0.89745)가 그대로 적용됐다. 이들은 국가계약법 적격심사 낙찰하한율
  적용 대상이 아니다.
* 일부 공고의 ``published_award_floor_rate`` 가 ``1.00000`` 으로 적재돼 있어
  "예정가 전액 이상 투찰"이라는 성립 불가능한 하한으로 하회 판정이 났다.

이 모듈은 그 두 축을 **선언 데이터**로 처리하는 순수 판별기다(§4.5.3 / §4.7.4).
패턴 추가 = 코드 분기가 아니라 :data:`_AGENCY_PATTERNS` 한 줄.

정직 명세(§2)와의 관계
----------------------
판별 결과는 tri-state 다. 이름만으로 국공립/사립을 가를 수 없는 부류(대학교 등)를
``applicable`` 로 단정하면 오탐이 남고, ``not_applicable`` 로 단정하면 "비국가기관이
확인됐다"는 없는 근거를 주장하게 된다. 그래서 ``uncertain`` 을 별도 상태로 두고,
판정만 생략하되 **그 사실을 리포트에 남긴다**(침묵 스킵 금지).

경계
----
이 모듈은 **분석(홀드아웃 품질 판정) 전용**이다. predictor·guardrail·라이브 예측이
쓰는 하한(``legal_floor_spec`` / ``guardrail_core``)에는 관여하지 않는다 — 라이브는
공고가 게시한 하한을 ``max()`` 로만 접어 올리므로 여기서 "신뢰 못 함"으로 본 값도
라이브에서는 그대로 안전 방향으로만 작동한다.

순수 함수(I/O 0).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.ai.predictors.historical import normalize_agency_name

# ── tri-state 라벨 ────────────────────────────────────────────────────────────
FLOOR_APPLICABLE = "applicable"
FLOOR_NOT_APPLICABLE = "not_applicable"
FLOOR_APPLICABILITY_UNCERTAIN = "uncertain"

# 리포트가 0 건까지 포함해 고정 순서로 세기 위한 전체 라벨 집합.
ALL_FLOOR_APPLICABILITIES: tuple[str, ...] = (
    FLOOR_APPLICABLE,
    FLOOR_NOT_APPLICABLE,
    FLOOR_APPLICABILITY_UNCERTAIN,
)

# ── 패턴 매칭 모드(디스패치 맵) ───────────────────────────────────────────────
# ``contains`` 만으로는 짧은 약칭이 위험하다: "수협" 은 "농업용수협의체" 같은 무관한
# 기관명 안에 substring 으로 들어간다. 약칭은 기관명 **말미**에서만 조합을 뜻하므로
# ``endswith`` 모드를 따로 둔다(모드 추가 = 이 맵 한 줄).
MATCH_CONTAINS = "contains"
MATCH_ENDSWITH = "endswith"
_MATCHERS: dict[str, Callable[[str, str], bool]] = {
    MATCH_CONTAINS: lambda name, token: token in name,
    MATCH_ENDSWITH: lambda name, token: name.endswith(token),
}


@dataclass(frozen=True)
class _AgencyPattern:
    """기관명 토큰 → 하한 적용 범위 판정 한 줄."""

    token: str
    applicability: str
    mode: str = MATCH_CONTAINS

    def matches(self, normalized_name: str) -> bool:
        return _MATCHERS[self.mode](normalized_name, self.token)


# 선언 테이블 — **위에서부터 첫 매칭**이 이긴다. 그래서 ``not_applicable`` 항목을
# ``uncertain`` 보다 먼저 둔다: "울산대학교산학협력단" 은 두 패턴에 모두 걸리지만
# 산학협력단이라는 구체 근거가 "대학교라 판별 불가"보다 강하다.
#
# 보수적으로 **명백한 것만** 넣는다. 조직 종류 어미(공사/공단/청/시/군)는 국가·지자체
# 기관을 뜻하므로 여기 없고 기본값 ``applicable`` 로 남는다.
_AGENCY_PATTERNS: tuple[_AgencyPattern, ...] = (
    # ── 비국가기관(국가계약법 적격심사 낙찰하한율 적용 대상 아님) ──
    # 대학 부설 비영리법인. 라이브 실측 10건(한동대·울산대·인제대·조선이공대 등).
    _AgencyPattern("산학협력단", FLOOR_NOT_APPLICABLE),
    # 농업/축산업/수산업/신용/인삼 협동조합을 모두 포괄한다. 라이브 실측 12건.
    _AgencyPattern("협동조합", FLOOR_NOT_APPLICABLE),
    _AgencyPattern("학교법인", FLOOR_NOT_APPLICABLE),
    # 약칭 중앙회 표기는 substring 충돌 위험이 없어 contains 로 둔다.
    _AgencyPattern("농협중앙회", FLOOR_NOT_APPLICABLE),
    _AgencyPattern("수협중앙회", FLOOR_NOT_APPLICABLE),
    _AgencyPattern("축협중앙회", FLOOR_NOT_APPLICABLE),
    _AgencyPattern("신협중앙회", FLOOR_NOT_APPLICABLE),
    # 단위조합 약칭("○○농협")은 말미에서만 조합을 뜻한다(위 모드 주석 참조).
    _AgencyPattern("농협", FLOOR_NOT_APPLICABLE, MATCH_ENDSWITH),
    _AgencyPattern("수협", FLOOR_NOT_APPLICABLE, MATCH_ENDSWITH),
    _AgencyPattern("축협", FLOOR_NOT_APPLICABLE, MATCH_ENDSWITH),
    _AgencyPattern("신협", FLOOR_NOT_APPLICABLE, MATCH_ENDSWITH),
    # ── 이름만으로 국공립/사립을 가를 수 없는 부류 ──
    # "대학"은 "대학교"/"전문대학"을 함께 덮는다. 국립대학도 법인이라 국가계약 tier
    # 적용 여부를 이름으로 단정할 수 없으므로 판정을 생략만 한다.
    _AgencyPattern("대학", FLOOR_APPLICABILITY_UNCERTAIN),
)

# ── 공고 게시 하한율 개연성 범위(선언 상수) ───────────────────────────────────
# 하한율 1.00000 은 "예정가 전액 이상 투찰"을 뜻해 낙찰하한의 의미가 성립하지 않는다
# (라이브 실측 3건). 상한 0.995 는 관측된 최대 실값 0.89995 위로 충분한 여유다.
PUBLISHED_FLOOR_MAX_PLAUSIBLE = 0.995
# 하한 0.30 은 백분율/분수 스케일 오적재(예: 0.88 대신 0.0088)를 걸러내는 선이다.
# 0.5 로 올리지 않는 이유: 라이브에 0.47995 가 3건 있고 .995 로 끝나는 표기 형태가
# 다른 실값(0.87745/0.89745)과 같아 진짜 게시값으로 보인다. 이 값을 버리고 era-tier
# (0.89745)로 폴백하면 오히려 새 오탐을 만든다.
PUBLISHED_FLOOR_MIN_PLAUSIBLE = 0.30


def resolve_floor_applicability(agency_name: Any) -> str:
    """발주기관명으로 법정 낙찰하한 모델의 적용 범위를 판별한다(tri-state).

    이름이 비었으면 기본값 ``applicable`` — 기관을 모른다고 판정을 넓게 생략하면
    실제 이상치까지 조용히 사라진다(미상 표본은 이미 ``_unknown`` 버킷으로 별도
    집계된다).

    공백 제거·소문자화는 기존 헬퍼 :func:`normalize_agency_name` 에 위임한다(§4.5.6).
    법인격 토큰까지 지우는 ``normalize_agency_key`` 를 쓰지 않는 이유는 그쪽이
    "사단법인"/"주식회사" 같은 **유형 신호 자체를 제거**하기 때문이다.
    """
    normalized = normalize_agency_name(agency_name)
    if not normalized:
        return FLOOR_APPLICABLE
    for pattern in _AGENCY_PATTERNS:
        if pattern.matches(normalized):
            return pattern.applicability
    return FLOOR_APPLICABLE


def is_published_floor_plausible(rate: float | None) -> bool:
    """공고 게시 낙찰하한율이 하회 판정에 쓸 만한 개연 범위 안인가.

    범위 밖 값은 **버리는 게 아니라** 하한 해석에서만 제외하고, 그 사실을
    ``published_floor_implausible`` 로 리포트에 남긴다.
    """
    if rate is None:
        return False
    return PUBLISHED_FLOOR_MIN_PLAUSIBLE <= float(rate) <= PUBLISHED_FLOOR_MAX_PLAUSIBLE


def is_floor_judgeable(applicability: str) -> bool:
    """이 적용 범위 판정에서 하한 하회 판정을 수행해도 되는가."""
    return applicability == FLOOR_APPLICABLE
