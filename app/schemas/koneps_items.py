"""KONEPS 수집 파이프라인의 **내부** item 계약 (방어적 DTO Phase 3).

왜 별도 모델인가
----------------
``CrawlNoticeItem`` 은 ``CrawlResponse`` 를 통해 ``POST /operations/crawl`` 의
응답 스키마로 **공개**되어 있다(OpenAPI 표면). 수집 경로는 그보다 넓은 필드를 실어
persistence 까지 운반한다(``award_floor_rate`` / ``eligibility_raw``). 공개 스키마에
필드를 추가하면 OpenAPI 스펙과 프론트 타입이 함께 흔들리므로, 수집 내부 계약은
``CrawlNoticeItem`` 을 **확장**한 이 모델로 둔다. 어떤 라우터도 이 모델을 참조하지
않으므로 OpenAPI 스펙은 불변이고, HTTP 경계에서는 부모 스키마로 좁혀 나간다.

역할 분리
--------
* **구조 계약** = 여기(Pydantic). 필드 존재/타입/기본값을 강제한다.
* **의미 계약** = ``app/services/koneps/field_contract.py`` (관찰 전용). KONEPS 원시
  응답의 키가 실측 계약과 어긋나는지 **경고만** 하고 수집을 막지 않는다.

``metadata`` 는 의도적으로 자유형 dict 로 남긴다: 수집 모드별(openapi / scsbid /
live / mock)로 실리는 키가 다르고 celery/HTTP payload 로 그대로 나가므로, 여기서
타입을 강제하면 직렬화 산출이 바뀐다(산출 불변 원칙). 대신 **ORM 대입에 쓰이는
필드만** ``CrawlItemMetadataFacts`` 읽기 투영으로 검증해 소비한다.

레이어 규칙: 이 모듈은 ``app/services/`` 를 import 하지 않는다(스키마는 leaf). 그래서
금액/시각의 관용 파싱(``koneps.parsing.coerce_amount`` / ``coerce_datetime``)은 여기서
수행하지 않고 **원시 토큰 타입**으로 선언해 소비 지점에 남긴다 — 기존 산출을 한 비트도
바꾸지 않기 위한 의도적 선택이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app.schemas.crawl import CrawlNoticeItem


def _as_text(value: str | int | float | bool | None) -> str | None:
    """관용 텍스트 정규화 — None 은 유지하고 비문자는 문자열화한다.

    KONEPS JSON 은 같은 필드를 문자/숫자로 섞어 보낼 수 있고 기존 소비자는
    ``str(item_metadata.get(...) or "")`` 로 받아 썼으므로 문자열화가 동등하다.
    빈 문자열은 그대로 두어 falsy 판정(개찰 결과 유무 게이트)을 보존한다.
    """
    if value is None or isinstance(value, str):
        return value
    return str(value)


Text = Annotated[str | None, BeforeValidator(_as_text)]
# KONEPS 원시 금액 토큰. 콤마/단위가 붙은 문자열이 그대로 올 수 있고, 소비자가
# ``parsing.coerce_amount`` 로 해석한다. 여기서 float 로 강제하면 (a) 파싱 규칙이
# 두 곳으로 갈라지고 (b) 소비자가 원시값을 그대로 대입하던 경로의 산출이 바뀐다.
RawAmount = float | int | str | None
# KONEPS 원시 시각 토큰. ``coerce_datetime`` 을 여기서 적용하지 않는 이유: 소비자
# (``_persist_tender_result_for_item``)가 "값이 있는가"를 truthy 로 판정하므로, 파싱
# 실패를 None 으로 접으면 게이트 판정이 바뀐다. 정규화는 소비 지점에 남긴다.
RawInstant = datetime | str | int | float | None


class KonepsCollectedItem(CrawlNoticeItem):
    """KONEPS 한 공고/개찰 행의 수집 산출 — 생산자부터 ORM 대입까지 이 모델로 흐른다.

    ``CrawlNoticeItem`` (공개 표면) + 수집 내부 전용 필드. HTTP 응답은 부모 스키마로
    좁혀 나가므로 여기 추가 필드는 스펙에 노출되지 않는다.
    """

    award_floor_rate: float | None = Field(
        default=None,
        description="공고 낙찰하한율(분수). 없으면 None — 기존 값을 지우지 않는 가드의 입력.",
    )
    eligibility_raw: dict[str, Any] | None = Field(
        default=None,
        description="참가자격 원문. 수집 피드는 배출하지 않고 backfill 스크립트만 채운다.",
    )

    def opening_facts(self) -> CrawlItemMetadataFacts:
        """``metadata`` 를 ORM 대입용 읽기 투영으로 검증해 돌려준다.

        ``metadata`` 는 모드별로 키가 다른 자유형 bag 이므로 소비 지점에서 필요한
        필드만 투영한다(미지 키는 무시). 캐시하지 않는다 — 개찰결과 merge 가
        ``metadata`` 를 교체하므로 캐시는 낡은 값을 줄 수 있다.
        """
        return CrawlItemMetadataFacts.model_validate(self.metadata or {})


class CrawlItemMetadataFacts(BaseModel):
    """수집 item ``metadata`` 중 **ORM 대입/판정에 쓰이는 필드만** 선언한 읽기 투영.

    ``extra="ignore"``: metadata 는 provenance(원시 응답, 페이지 번호, 오퍼레이션명
    …)를 함께 싣는 bag 이고 그 값들은 그대로 payload 로 나간다. 여기서는 소비 대상만
    좁혀 읽으므로 나머지는 무시한다. 이 모델은 **읽기 전용 투영**이라 원본 bag 을
    바꾸지 않는다(직렬화 산출 불변).
    """

    model_config = ConfigDict(extra="ignore")

    # --- 기관 / 상태 텍스트 ---------------------------------------------------
    issuing_agency: Text = None
    demand_agency: Text = None
    opening_demand_agency: Text = None
    opening_status: Text = None
    status: Text = None
    opening_bid_classification: Text = None
    opening_bid_progress_order: Text = None
    contract_method: Text = None

    # --- 개찰/낙찰 결과 -------------------------------------------------------
    winning_company: Text = None
    winning_amount: RawAmount = None
    winning_rate: RawAmount = None
    bid_rate: RawAmount = None
    base_amount_estimated: RawAmount = None

    # --- 개찰 시각(원시 토큰 보존) --------------------------------------------
    opening_announced_at: RawInstant = None
    opening_scheduled_at: RawInstant = None

    # --- 복수예비가격 / 추첨번호 ----------------------------------------------
    # 해석하지 않고 JSON blob 그대로 ``HistoricalData`` 컬럼에 릴레이한다. 원소 타입을
    # 좁히면 ``json.dumps`` 산출(int vs float)이 흔들려 저장 문자열이 바뀌므로
    # 의도적으로 원소는 검증하지 않는다.
    reserve_prices: list[Any] = Field(default_factory=list)
    selected_numbers: list[Any] = Field(default_factory=list)

    def resolved_agency_name(self) -> str:
        """``HistoricalData.agency_name`` 해석 순서(개찰수요기관 > 수요기관 > 공고기관)."""
        return (
            self.opening_demand_agency
            or self.demand_agency
            or self.issuing_agency
            or ""
        )

    def resolved_demand_agency(self) -> str | None:
        """프로젝트 수요기관 해석 순서(개찰수요기관 우선)."""
        return self.opening_demand_agency or self.demand_agency

    def has_award_signal(self) -> bool:
        """개찰/낙찰 흔적이 하나라도 있는가(TenderResult 생성 게이트)."""
        return any(
            (
                self.opening_status,
                self.winning_company,
                self.winning_amount,
                self.winning_rate,
                self.opening_announced_at,
            )
        )


class ScsbidReserveDetail(BaseModel):
    """복수예비가격 상세 조회 산출 — scsbid 개찰 item 빌더의 입력 계약.

    ``KonepsCollectorService._fetch_scsbid_reserve_detail`` 은 celery backfill
    (``app/tasks/jobs.py``)과 공유되는 dict 를 돌려주므로, 승격은 그 결과를 소비하는
    지점(``_process_scsbid_raw_item``)에서 한 번 한다. 미조회(빈 dict) / 실패
    (``reserve_detail_error`` 만) / 정상 요약 세 형태를 모두 같은 모델로 표현한다.
    """

    model_config = ConfigDict(extra="ignore")

    # 상세 rows 는 provenance 로 그대로 metadata 에 실린다(원소 검증 없음 — 산출 불변).
    reserve_prices: list[Any] = Field(default_factory=list)
    selected_numbers: list[Any] = Field(default_factory=list)
    raw_reserve_detail_items: list[Any] = Field(default_factory=list)
    planned_price: RawAmount = None
    base_amount: RawAmount = None
    reserve_detail_error: Text = None


__all__ = [
    "CrawlItemMetadataFacts",
    "KonepsCollectedItem",
    "RawAmount",
    "RawInstant",
    "ScsbidReserveDetail",
    "Text",
]
