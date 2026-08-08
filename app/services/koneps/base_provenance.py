"""수집 시점 ``base_amount`` provenance 태깅 — 분류기와 수집 write 경로의 얇은 경계.

``persistence`` 에서 떼어냈다: 저쪽은 "수집된 item 을 행에 반영한다"가 책임이고, 여기는
"저장된 base 가 무엇인지 판정해 라벨을 남긴다"가 책임이다. 판정 규칙 자체는
``app/services/base_amount_basis.py`` 가 단일 출처로 갖고, 이 모듈은 그 순수 함수에
**어떤 입력을 실어 보낼지**만 정한다.
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

    이 태깅은 신규 행뿐 아니라 **기존 행에도 매 수집 주기 다시** 일어난다. 그래서 공고
    추정가격을 넘기지 않으면 비율 규칙이 꺼진 채 재분류되어, 백필이 ``suspect-ratio`` 로
    교정해 둔 행이 다음 수집에서 ``clean`` 으로 되돌아간다 — 재태깅이 지속되지 않는다
    (회귀 가드: ``test_recollection_does_not_revert_a_suspect_ratio_tag``).

    추정가격 출처는 **백필과 동일하게** ``project.budget_estimate`` 다 — 두 경로가 같은
    입력을 봐야 판정이 갈리지 않는다. ``matching.resolve_budget_estimate`` 는 쓰지 않는다:
    추정가격이 없으면 ``base_amount`` 로 폴백하므로 비율이 1.0 으로 자기충족해 규칙이
    조용히 무력화된다. 운영 절차는 ``docs/operations/base-amount-basis-backfill.md``.

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
    estimate = optional_float(getattr(project, "budget_estimate", None))
    return estimate if estimate is not None and estimate > 0 else None
