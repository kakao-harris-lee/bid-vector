"""학습 시계열의 basis 명시 낙찰률 라벨 — ORM 행 → 순수 커널 어댑터.

판정은 전부 :mod:`app.domain.award_rate_label` (순수·strict 아일랜드)이 하고, 이 모듈은
``HistoricalData`` / ``TenderResult`` 에서 스칼라를 꺼내 넘기는 얇은 경계만 담당한다
(§4.7 순수 코어 / 얇은 경계). 도메인 커널이 ORM 을 import 하지 않게 하려는 분리다.

왜 별도 파일인가
----------------
``app/services/prediction_dataset.py`` 는 이미 설계 한도(§4.5-4, 500줄)를 넘겨 있고
설계 래칫이 그 파일의 LOC 밴드를 고정하고 있다. 라벨 조립을 그 안에 더 넣으면 밴드가
올라가므로, 새 책임은 새 파일로 낸다 — 래칫이 의도한 반응이 바로 이것이다.

**기존 라벨 해석 경로(``PredictionDatasetService._resolve_bid_rate`` 의 tier 우선순위)는
이 PR 에서 한 줄도 바뀌지 않았다.** 새 라벨은 그 옆에 나란히 실릴 뿐이고, 두 값은 축이
다를 수 있다. 같이 환산하지 않는 이유는 #195 가 발주처 밴드에 이미 E[사정률]을 곱하기
때문이다(``app/domain/basis_conversion``) — 라벨까지 환산하면 사정률이 두 번 반영된다.
"""

from __future__ import annotations

from app.domain.award_rate_label import build_award_rate_label
from app.models.models import HistoricalData, TenderResult


def award_rate_label_for(
    record: HistoricalData, tender_result: TenderResult | None
) -> dict[str, float | str | None]:
    """한 학습 행의 라벨 블록(값 + basis + 분모 출처)을 직렬화 형태로 낸다.

    Args:
        record: 학습 시계열의 원본 행. ``base_amount`` 와 그 오염 태그(#199), 복수예비가격
            복구 추정치를 제공한다.
        tender_result: 그 공고의 대표 개찰 결과. ``None`` 이면 분자가 없으므로 라벨은
            ``no-winning-amount`` 상태로 난다(값 없음, 사유 있음).

    Returns:
        ``AwardRateLabel.as_payload()`` — 값과 basis 를 한 블록에 묶은 평문 dict. 소비자가
        ``value`` 만 떼어 곱하면 분모 출처(``denominator_source``)를 잃는데, 그중
        ``base-fallback`` 은 그 값이 기초금액이라는 증거가 없는 경로다.
    """
    return build_award_rate_label(
        winning_amount=(
            tender_result.winning_amount if tender_result is not None else None
        ),
        base_amount=record.base_amount,
        base_amount_basis=record.base_amount_basis,
        base_amount_estimated=record.base_amount_estimated,
    ).as_payload()
