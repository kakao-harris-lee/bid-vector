"""공고 금액 축(추정가격 · 최소 · 최대) write 정책 — 출처 인지 덮어쓰기 가드.

``persistence`` 에서 떼어냈다: 저쪽은 "수집된 item 을 행에 반영한다"가 책임이고, 여기는
"금액 축에 무엇을 쓸지 판정한다"가 책임이다. ``base_provenance`` 가 base 축에서 하는 일의
추정가격 축 짝이다.

왜 가드가 필요한가
------------------
``Project.budget_estimate`` 는 표시값이 아니다. 두 판정의 입력이다:

* base provenance 분류(#358 ``suspect-ratio``)의 **분모** — ``base ÷ 추정가격`` 비가
  1.15 를 넘으면 저장 base 를 오염으로 본다.
* #356 V3 게이트의 ``budget_cap`` — 게시 하한이 예산 상한을 넘을 권한을 줄지 판정한다.

그런데 세 수집 경로가 **같은 자리(``item.estimated_amount``)에 성격이 다른 금액**을 싣는다:
공고 피드의 게시 추정가격, 개찰 피드의 예정가(≈ 기초금액 × 사정률, 약 +10%), 그리고
추정가격 미공급 시 기초금액 사본. 종전 write 는 ``budget_estimate or 이전값`` 이라 **양수면
무조건** 덮었으므로, 6시간 주기 개찰 패스와 추정가격 미공급 재수집이 분모를 예정가/자기사본
으로 바꿔 오염 판정을 조용히 되돌렸다(#358 이 known gap 으로 고정한 두 시퀀스).

왜 "무차별 양수 불변" 이 아닌가
--------------------------------
KONEPS 는 정정공고·재공고로 추정가격을 **바꿔 게시**한다. 양수면 무조건 지키는 가드는 그
정당한 갱신까지 막아 저장값을 첫 게시 시점에 얼린다. 그래서 값이 아니라 **출처**를 본다:
공고 피드가 게시한 값(``notice``)은 정정으로 받아들이고, 파생(``derived``)·폴백
(``base-fallback``)·미신고(``None``)는 **빈 자리(NULL/0)만** 채운다
(``persistence`` 의 ``award_floor_rate``/``eligibility_raw`` anti-clobber 가드 미러).

스코프 밖(의도적)
-----------------
* ``budget_min``/``budget_max`` 는 종전 그대로 유입 금액 전부를 반영한다. 두 컬럼은 분모도
  게이트 입력도 아니고, 좁히면 다른 소비자의 산출이 바뀐다.
* ``matching.resolve_budget_estimate`` 의 ``base_amount`` 폴백도 유지한다 — 그 값은 min/max
  가 함께 보고, 폴백을 없애면 이 PR 밖의 산출이 움직인다. 폴백은 남기고 **그 값이 무엇인지**
  를 생산자가 신고하게 한 것이 이 PR 의 경계다.
* 이미 저장된 ``est == base`` 행(활성 991건)은 이 가드로 바뀌지 않는다. 가드는 전망적
  보호이고, 기존 값의 수리는 원본 추정가격이 남아 있지 않아 별도 문제다.
"""

from __future__ import annotations

from app.core.constants import ESTIMATE_SOURCE_NOTICE, EstimatedAmountSource
from app.models.models import Project
from app.schemas.koneps_items import KonepsCollectedItem
from app.services.koneps import matching
from app.utils.numeric import optional_float


def should_write_budget_estimate(
    current: float | None,
    incoming: float | None,
    source: EstimatedAmountSource | None,
) -> bool:
    """유입 추정가격을 저장할 것인가 — 값이 아니라 **출처**로 판정한다.

    세 갈래뿐이라 조건 사다리를 두지 않는다:

    1. 유입이 값이 아니면(None/0/음수/비수치) 쓰지 않는다. 종전 ``or 이전값`` 과 같은
       결과이고, "값 없음"이 저장값을 지우지 않는다는 뜻이다.
    2. 공고 게시값(``notice``)은 쓴다 — 정정공고·재공고의 갱신을 반영한다.
    3. 나머지(파생 · 기초금액 폴백 · 미신고)는 **빈 자리일 때만** 쓴다.

    ``source`` 는 DTO 계약이 이미 정규화해 넘긴다(값이 없으면 ``None`` 으로 접힌다), 그래서
    여기서 "값 없이 notice 신고" 같은 모순을 다시 방어하지 않는다. 반면 ``current`` 는 ORM
    컬럼에서 오므로 숫자 해석을 한 번 거친다(레거시 행의 Decimal/문자열 방어).
    """
    incoming_amount = optional_float(incoming)
    if incoming_amount is None or incoming_amount <= 0:
        return False
    if source == ESTIMATE_SOURCE_NOTICE:
        return True
    current_amount = optional_float(current)
    return current_amount is None or current_amount <= 0


def stored_budget_estimate(
    current: float | None,
    incoming: float | None,
    source: EstimatedAmountSource | None,
) -> float:
    """저장될 추정가격 — 쓰지 않기로 하면 기존 값을 float 로 정규화해 그대로 둔다.

    정규화(``float(current or 0.0)``)는 종전 write 의 동작을 그대로 이어받는다: 이 컬럼은
    NOT NULL 계약이 아니라 코드 관례로 0.0 을 "미확보"로 써 왔고, 분류기·게이트 모두
    ``> 0`` 으로만 켜지므로 None 과 0.0 의 의미가 같다.
    """
    if should_write_budget_estimate(current, incoming, source):
        return float(optional_float(incoming) or 0.0)
    return float(optional_float(current) or 0.0)


def apply_budget_amounts(project: Project, *, item: KonepsCollectedItem) -> None:
    """수집 item 의 금액 축을 공고 행에 반영한다(추정가격은 가드, min/max 는 종전대로)."""
    budget_estimate = matching.resolve_budget_estimate(item)
    budget_values = [
        float(amount)
        for amount in (item.base_amount, item.estimated_amount, budget_estimate)
        if amount not in (None, "", 0, 0.0)
    ]
    project.budget_estimate = stored_budget_estimate(
        project.budget_estimate, budget_estimate, item.estimated_amount_source
    )
    project.budget_min = min(budget_values) if budget_values else project.budget_min
    project.budget_max = max(budget_values) if budget_values else project.budget_max
