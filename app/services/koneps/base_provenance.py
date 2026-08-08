"""수집 시점 ``base_amount`` provenance 태깅 — 분류기와 수집 write 경로의 얇은 경계.

``persistence`` 에서 떼어냈다: 저쪽은 "수집된 item 을 행에 반영한다"가 책임이고, 여기는
"저장된 base 가 무엇인지 판정해 라벨을 남긴다"가 책임이다. 판정 규칙 자체는
``app/services/base_amount_basis.py`` 가 단일 출처로 갖고, 이 모듈은 그 순수 함수에
**어떤 입력을 실어 보낼지**만 정한다.

지속성 주장의 실제 범위 (출처 인지 가드 이후)
--------------------------------------------
태깅은 신규 행뿐 아니라 **기존 행에도 매 수집 주기 다시** 일어난다. 그래서 백필의 재태깅이
유지되려면 이 경로가 같은 판정을 내야 하고, 그 판정은 ``update_project_from_item`` 이
태깅보다 **먼저** 쓰는 ``project.budget_estimate`` (여기서 읽는 분모)에 달려 있다.

한때 그 write 는 값이 양수이기만 하면 무조건 덮었고, 두 프로덕션 패스가 분모를 바꿔 재태깅을
되돌렸다: **scsbid 개찰(6h)** 은 예정가(약 +10%)를, **추정가격 미공급 재수집**은 예산 키·
기초금액 사본을 실어 왔다(실측 복귀 밴드 1.16·1.20·1.24). 지금은
``budget_fields.apply_budget_amounts`` 의 출처 인지 가드가 그 자리를 지킨다 — 공고가
추정가격으로 게시한 값(``notice``)만 덮고, 파생·예산 폴백·기초금액 사본·미신고는 빈 자리
(NULL/0)만 채운다. 그래서 두 패스에서 분모가 보존되고 재태깅도 유지된다
(``tests/test_koneps_persistence.py`` 가 고정).

가드는 **전망적 보호**다. 그 이전에 세탁된 분모 위에서 ``clean`` 으로 굳은 행은
``--reclassify-clean`` 으로도 회복되지 않는다 — 백필이 같은 분모를 읽고, ``clean`` 규칙이
``derived-yega`` 판정보다 앞서기 때문이다. 애초에 추정가격을 한 번도 얻지 못한 행도 사본이
자리를 채운 채 남는다(``est_equals_base`` 사각지대). 잔여 규모·영구성·corpus leakage caveat 과
가드가 지키는 전망 코호트 실측은 ``docs/operations/base-amount-basis-backfill.md`` §6/§7 이
단일 출처로 갖는다.
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
    봐야 판정이 갈리지 않는다. 이 태깅이 기존 행에도 매 주기 다시 일어나므로 그 분모를
    누가 쓸 수 있는지가 재태깅 지속을 좌우한다(모듈 docstring 의 출처 인지 가드 참조).

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
