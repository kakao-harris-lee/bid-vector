"""수집 시점 ``base_amount`` provenance 태깅 — 분류기와 수집 write 경로의 얇은 경계.

``persistence`` 에서 떼어냈다: 저쪽은 "수집된 item 을 행에 반영한다"가 책임이고, 여기는
"저장된 base 가 무엇인지 판정해 라벨을 남긴다"가 책임이다. 판정 규칙 자체는
``app/services/base_amount_basis.py`` 가 단일 출처로 갖고, 이 모듈은 그 순수 함수에
**어떤 입력을 실어 보낼지**만 정한다.

지속성 주장의 실제 범위 (재리뷰 D1 — 과대 주장 축소)
----------------------------------------------------
태깅은 신규 행뿐 아니라 **기존 행에도 매 수집 주기 다시** 일어난다. 그래서 백필의 재태깅이
유지되려면 이 경로가 같은 판정을 내야 하는데, 그것이 성립하는 것은 **다음 패스가 실제
추정가격을 실은 공고 수집일 때뿐이다.** ``update_project_from_item`` 이 태깅보다 **먼저**
``project.budget_estimate`` 를 ``matching.resolve_budget_estimate(item) or 이전값`` 으로
덮으므로, 그 한 홉에서 분모가 바뀌면 여기서 읽는 값도 함께 바뀐다:

- **scsbid 개찰 패스(6h)**: item 이 ``base_amount=0.0`` + ``estimated_amount=예정가``
  (``scsbid.py`` 의 ``planned_price`` 또는 ``winning ÷ success_rate``)를 싣는다. 분모가
  추정가격 → 예정가(약 +10%)로 바뀌어 임계 바로 위 밴드가 clean 으로 복귀한다. 실측:
  base/추정가격 **1.16·1.20·1.24 복귀, 1.28·1.408 생존**. settled 코호트가 곧 캘리브레이션
  corpus 라 영향이 크다.
- **추정가격 미공급 재수집**: ``resolve_budget_estimate`` 가 ``base_amount`` 로 폴백해 두
  금액이 같은 값의 두 사본이 되고(비율 1.0), 규칙이 볼 수 없는 ``est_equals_base`` 코호트로
  떨어진다. 저장된 진짜 추정가격도 함께 덮여 복구 불가하게 사라진다(선재 동작).

따라서 "``matching.resolve_budget_estimate`` 를 쓰지 않는다"는 서술은 **직접 호출**에만
해당하며 독립성을 뜻하지 않는다 — 그 값은 한 홉 뒤로 도착한다. 두 시퀀스는
``tests/test_koneps_persistence.py`` 가 현재 동작으로 고정해 두었다.

후속 PR: scsbid/미공급 패스가 기존 **양수** ``Project.budget_estimate`` 를 덮지 못하게
가드한다(``update_project_from_item`` 의 ``award_floor_rate``/``eligibility_raw`` 가드를
미러). 그 가드는 ``budget_cap``(#356 게이트 입력)도 함께 움직이므로 별도 실측이 선행돼야
한다. 그때까지의 보상 통제는 ``--reclassify-clean`` 주기 재실행이다
(``docs/operations/base-amount-basis-backfill.md`` §6).
"""

from __future__ import annotations

from datetime import datetime

from app.models.models import HistoricalData, Project
from app.schemas.koneps_items import CrawlItemMetadataFacts
from app.services.base_amount_basis import classify_base_basis, normalize_winning_rate
from app.services.koneps import parsing
from app.utils.numeric import optional_float


def tag_base_provenance(
    historical_record: HistoricalData,
    *,
    facts: CrawlItemMetadataFacts,
    project: Project | None,
    stamp: datetime,
) -> None:
    """최종 저장 base 를 분류해 라벨/검사시각을 남긴다(원본 금액은 건드리지 않는다).

    추정가격 출처는 **백필과 동일한** ``project.budget_estimate`` 다 — 두 경로가 같은 입력을
    봐야 판정이 갈리지 않는다. 이 태깅이 기존 행에도 매 주기 다시 일어난다는 점과, 그래서
    재태깅 지속이 **어느 패스에서만** 성립하는지는 모듈 docstring 참조(그 범위 밖 시퀀스는
    ``tests/test_koneps_persistence.py`` 가 현재 동작으로 고정한다).

    ``stamp`` 은 주입받는다(§4.7-3): 시간 출처를 여기서 직접 부르면 ``persistence.utc_now``
    하나만 얼리는 기존 특성화 테스트의 seam 이 둘로 갈라진다.
    """
    winning_amount = parsing.coerce_amount(facts.winning_amount)
    winning_rate = normalize_winning_rate(facts.winning_rate)
    historical_record.base_amount_basis = classify_base_basis(
        historical_record.base_amount,
        winning_amount,
        winning_rate,
        notice_budget_estimate(project),
    )
    historical_record.basis_checked_at = stamp


def notice_budget_estimate(project: Project | None) -> float | None:
    """공고 추정가격(양수)만 통과 — 미확보(None/0/음수/비수치)는 ``None``.

    비율 규칙은 양수 추정가격에서만 켜지므로, 확보 실패를 0.0 이 아니라 ``None`` 으로
    넘겨 "확보 못 함"과 "0원"을 같은 뜻으로 다룬다.
    """
    if project is None:
        return None
    estimate = optional_float(project.budget_estimate)
    return estimate if estimate is not None and estimate > 0 else None
