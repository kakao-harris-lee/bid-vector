"""투찰 보고서 메일의 라벨/값 표 — 무엇을 어떤 이름으로 싣는지의 단일 출처.

메일 본문(HTML·텍스트)은 이 표를 그대로 렌더링만 한다. 표를 별도 모듈로 둔 이유는
금액 라벨이 곧 정직성 계약이기 때문이다 — 기초금액(투찰 기준금액)과 추정가격은 서로
다른 금액이라 한 라벨로 묶이면 안 되고, 투찰률은 낙찰하한율과 basis 가 같은 기초금액
기준 율이어야 한다.

입력은 원시 dict 가 아니라 검증된 :class:`~app.schemas.bid_summary.BidSummaryResponse`
다 — 오타 키가 조용히 빈 칸으로 렌더링되면 "금액이 안 보이는" 형태로 다시 혼동을 만든다.
값 포매팅은 초안 서비스의 포매터를 주입받아, 메일과 투찰서 초안이 같은 숫자를 같은
모양으로 보여 준다(§4.7.3).
"""

from __future__ import annotations

from app.schemas.bid_summary import BidSummaryResponse
from app.services.bid_form_draft import BidFormDraftService

# 판단(action) → 한국어 라벨(telegram build_bid_decision_message 미러, 선언적 룩업).
ACTION_LABELS: dict[str, str] = {
    "bid_now": "즉시 투찰",
    "review": "추가 검토",
    "skip": "보류",
}


def build_report_rows(
    report: BidSummaryResponse, *, formatter: BidFormDraftService
) -> list[tuple[str, str]]:
    """검증된 요약을 메일 본문 행(라벨, 값) 목록으로 옮긴다(순수).

    Args:
        report: 투찰 의사결정 요약(요약 API 가 내보내는 것과 같은 계약).
        formatter: 금액·비율·시각·빈도 표시 포매터(투찰서 초안과 동일 구현).
    """
    notice = report.notice
    recommendation = report.recommendation
    return [
        ("공고명", notice.title or "(제목 없음)"),
        ("공고번호", notice.notice_number or ""),
        ("수요기관", notice.demand_agency or ""),
        ("분류(카테고리)", notice.category or ""),
        ("업종", notice.business_type_label or ""),
        ("기초금액(사업금액)", formatter.format_currency(notice.bid_base_amount)),
        ("추정가격(부가세 별도)", formatter.format_currency(notice.budget_estimate)),
        ("추천 투찰가", formatter.format_currency(recommendation.recommended_amount)),
        # 낙찰하한율과 basis 가 같은 율만 싣는다 — 추정가격 기준 율을 하한 옆에 두면
        # 과세 공고에서 하한 여유가 부풀어 보인다(#162).
        (
            "투찰률(%) — 기초금액 기준",
            formatter.format_percent(recommendation.recommended_bid_rate_on_base),
        ),
        (
            "하한 미달 빈도(과거 표본)",
            formatter.format_shortfall(report.floor_shortfall),
        ),
        (
            "판단",
            ACTION_LABELS.get(recommendation.action, recommendation.action),
        ),
        ("결정 상태", recommendation.decision_status),
        ("투찰 마감일시", formatter.format_datetime(notice.deadline)),
    ]
