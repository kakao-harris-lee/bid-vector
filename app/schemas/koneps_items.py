"""KONEPS 수집 파이프라인의 **내부** item 계약 (방어적 DTO Phase 3).

왜 별도 모델인가
----------------
``CrawlNoticeItem`` 은 ``CrawlResponse`` 를 통해 ``POST /operations/crawl`` 의
응답 스키마로 **공개**되어 있다(OpenAPI 표면). 수집 경로는 그보다 넓은 필드를 실어
persistence 까지 운반한다(``award_floor_rate`` / ``eligibility_raw`` /
``estimated_amount_source``). 공개 스키마에
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

import logging
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.constants import EstimatedAmountSource
from app.domain.published_floor_rate import plausible_published_floor_rate
from app.domain.rate_normalization import to_bid_rate_fraction
from app.schemas.crawl import CrawlNoticeItem
from app.utils.numeric import optional_float

logger = logging.getLogger(__name__)


def _as_text(value: str | int | float | bool | None) -> str | None:
    """관용 텍스트 정규화 — 문자/스칼라만 문자열로 접고 비스칼라는 ``None``.

    선언 타입은 **계약**(텍스트로 쓸 수 있는 스칼라)이고, 런타임에 pydantic 은 임의 값을
    넘길 수 있으므로 계약 밖 타입은 아래에서 명시적으로 접는다. ``object``/``Any`` 로 넓히지
    않는 이유: 설계 래칫이 그 둘을 "검증되지 않는 경계"로 세고 이 파일은 현재 0 이라, 넓은
    입력은 시그니처가 아니라 런타임 분기로 다룬다(baseline 갱신 없이 계약을 유지).

    KONEPS JSON 은 같은 필드를 문자/숫자로 섞어 보낼 수 있고 기존 소비자는
    ``str(item_metadata.get(...) or "")`` 로 받아 썼으므로 스칼라(``int``/``float``/
    ``bool``)의 문자열화는 동등하다. 빈 문자열은 그대로 두어 falsy 판정(개찰 결과 유무
    게이트)을 보존한다.

    반면 ``dict``/``list`` 같은 **비스칼라**는 문자열화하지 않고 ``None`` 으로 접는다.
    ``str({...})`` 는 항상 비어있지 않은 문자열이라 truthy 가 되어, 구조가 어긋난 값
    (예: ``winning_company`` 자리에 중첩 객체)이 ``has_award_signal`` 게이트를 통과시켜
    빈 ``TenderResult`` 를 만들고 ``"{'a': 1}"`` 같은 파이썬 repr 을 기관명/낙찰자명
    컬럼에 저장한다. 텍스트 계약을 만족하지 못하는 값은 "값 없음"으로 다루는 것이
    구조 계약(DTO)의 역할이다 — 관용은 스칼라까지만.
    """
    if value is None or isinstance(value, str):
        return value
    # bool 은 int 서브클래스라 이 검사에 포함된다(``str(True)`` -> "True", 기존과 동등).
    if isinstance(value, (int, float)):
        return str(value)
    return None


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

    ``extra="forbid"``: 부모(``CrawlNoticeItem``)를 그대로 상속하면 pydantic 기본값인
    ``extra="ignore"`` 라서, dict payload 승격 경로(``persistence._promote_items``)의
    **오타 키가 조용히 드롭**된다. 오늘의 생산자는 모두 DTO 를 배출하므로 이 경로를 타는
    것은 **앞으로 생길 dict payload 생산자**(손으로 만든 item 을 넘기는 스크립트·태스크·
    외부 호출부)이고, 그 첫 사용에서 오타가 드러나야 한다
    (``award_floor_rate`` 를 ``award_floor`` 로 쓰면 값이 사라진 채 통과). 영속화 승격은
    best-effort 가 아니므로(필수 필드 결손을 이미 ``ValidationError`` 로 거부한다) 미지
    키도 같은 기준으로 거부해 오타가 즉시 드러나게 한다. 모드별로 키가 다른 자유형 bag 은
    ``metadata`` 필드 **안**이며, 그 안쪽은 계속 자유형이다(``CrawlItemMetadataFacts`` 는
    읽기 투영이라 ``extra="ignore"`` 유지).
    """

    model_config = ConfigDict(extra="forbid")

    award_floor_rate: float | None = Field(
        default=None,
        description=(
            "공고 낙찰하한율(분수). percent 원값은 분수로 접히고, 하한으로 성립하지"
            " 않는 게시값은 None 으로 접힌다 — 없으면 None 이고, 기존 값을 지우지"
            " 않는 가드의 입력."
        ),
    )
    eligibility_raw: dict[str, Any] | None = Field(
        default=None,
        description="참가자격 원문. 수집 피드는 배출하지 않고 backfill 스크립트만 채운다.",
    )
    estimated_amount_source: EstimatedAmountSource | None = Field(
        default=None,
        description=(
            "``estimated_amount`` 자리에 실린 값의 출처(어휘는"
            " ``app.core.constants.EstimatedAmountSource``). ``None`` 은 '미신고' 이고,"
            " write 가드는 이를 파생/폴백과 같은 보수 취급으로 다룬다 — 구 dict payload"
            " 하위 호환을 위해 기본값이다."
        ),
    )

    @model_validator(mode="after")
    def _normalize_and_gate_award_floor_rate(self) -> "KonepsCollectedItem":
        """게시 낙찰하한율을 정규화한 뒤 **하한으로 성립하는 값만** 남긴다(구조 계약).

        두 단계이고 순서가 의미다:

        1. **스케일 정규화** (:func:`app.domain.rate_normalization.to_bid_rate_fraction`)
           — 오늘의 생산자(openapi/scsbid)는 이미 fraction 으로 정규화해 넘기므로
           no-op 이지만, 이 DTO 는 손으로 만든 dict payload 승격 경로도 받는다. 계약이
           "호출부가 먼저 정규화했다"는 선행 조건에 기대면 percent 원값(88)이 게이트에
           걸려 **정상 게시값이 드롭**된다 — 자족적 계약은 정규화부터 한다.
        2. **개연 게이트** (:func:`app.domain.published_floor_rate` 단일 출처) —
           ``sucsfbidLwltRate`` 는 발주기관 게시값의 충실한 전사인데 하한으로 성립하지
           않는 값이 섞여 온다(라이브 실측 47건이 ``1.00000`` = "예정가 전액 이상
           투찰"). 고치지 않고 "게시 하한 없음"(``None``)으로 접는다.

        여기가 게이트인 이유: 이 DTO 가 수집 두 경로(openapi / scsbid)와 dict payload
        승격이 **모두 통과하는 유일한 지점**이다. 소비 지점마다 게이트를 다시 걸면 한
        곳을 빠뜨렸을 때 조용히 새어 들어온다. ``_as_text`` 와 같은 축의 판단이다 —
        타입은 맞지만 계약을 만족하지 못하는 값은 "값 없음"으로 다룬다.

        왜 중요한가: 이 필드는 ``Project.award_floor_rate`` 가 되고, 라이브 가격 경로가
        추천가에 **예산 상한 초과 권한**을 줄지 판정할 때 읽는 입력이다(#356 V3). 성립
        불가한 하한이 통과하면 ``1.0 × 기초금액`` 이 하한으로 강제된다.

        필드 ``AfterValidator`` 가 아니라 model validator 인 이유: 거부 경고에
        ``notice_number`` 를 실어야 운영자가 어느 공고인지 특정해 조치할 수 있는데,
        필드 검증기는 이웃 필드를 읽지 못한다(침묵 스킵 금지 — 조치 가능한 로그).
        """
        raw = self.award_floor_rate
        if raw is None:
            return self
        accepted = plausible_published_floor_rate(to_bid_rate_fraction(raw))
        if accepted is None:
            logger.warning(
                "게시 낙찰하한율이 개연 범위 밖이라 버림: notice=%s rate=%s",
                self.notice_number,
                raw,
            )
        self.award_floor_rate = accepted
        return self

    @model_validator(mode="after")
    def _gate_estimated_amount_source(self) -> "KonepsCollectedItem":
        """추정가격이 실리지 않았으면 출처 신고를 남기지 않는다(자족적 계약).

        ``award_floor_rate`` 게이트와 같은 축이다: 이 DTO 는 생산자 두 경로(openapi /
        scsbid)와 dict payload 승격이 **모두 통과하는 유일한 지점**이라, "값 없이 출처만
        신고" 같은 모순은 여기서 접는다. 그러지 않으면 write 가드
        (``app/services/koneps/budget_fields.py``)가 존재하지 않는 값을 공고 게시값으로
        신뢰해, 폴백으로 도착한 기초금액이 저장된 추정가격을 덮을 수 있다.

        어휘 자체의 검증은 ``Literal`` 이 한다 — 오타는 여기 오기 전에 거부된다.

        ``award_floor_rate`` 게이트와 달리 **로그를 남기지 않는다**(비대칭은 의도적): 저쪽이
        거르는 값은 발주기관 게시값의 이상이라 조치 대상이지만, 여기서 접히는 조합(예정가를
        못 구한 개찰 행 등)은 정상 상태에서 상시 발생한다. 매 수집마다 경고를 찍으면 조치
        가능한 로그가 노이즈에 묻힌다.
        """
        if self.estimated_amount_source is None:
            return self
        amount = optional_float(self.estimated_amount)
        if amount is None or amount <= 0:
            self.estimated_amount_source = None
        return self

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


class OpeningResultRow(BaseModel):
    """개찰결과 그리드(개찰결과분류조회) 한 행의 **정규화 산출** — 17키 dict 를 대체한다.

    생산자는 ``koneps.html_parsing.normalize_opening_result_row`` (WebSquare 원시 키 →
    내부 이름) 이고, 소비자는 ``merge_opening_result_rows`` (수집 item metadata 로 병합)
    다. 브라우저 경로(``browser_crawl.read_opening_result_rows``)도 이 모델을 실어 나른다.

    ``extra="ignore"``: 이 모델은 원시 그리드 행이 아니라 **정규화된 행**의 계약이다.
    이미 내부 이름으로 정규화된 행(테스트/외부 호출부가 손으로 만든 payload)을 그대로
    승격할 때 남는 원시 키·부가 키는 무시한다(원본은 ``raw`` 로 보존).

    필드 기본값은 전부 "값 없음"(``None`` / 빈 리스트)이다: 정규화 생산자는 결측을 빈
    문자열로 명시해 싣고(``str(...).strip()``), 승격 경로는 키 부재를 ``None`` 으로 남겨야
    소비자의 falsy 판정이 dict 릴레이 시절(``row.get(...)``)과 동일하다(산출 불변).

    ``scheduled_at`` / ``announced_at`` 만 ``datetime`` 으로 좁힌다: 소비자가 ``.isoformat()``
    을 직접 부르므로 원시 토큰(str)을 그대로 흘리면 그 자리에서 ``AttributeError`` 로 죽는다
    (dict 릴레이 시절의 잠재 결함). 승격 경계에서 ISO 토큰은 pydantic 이 해석하고, 해석
    불가 토큰은 여기서 ``ValidationError`` 로 거부한다 — 정규화 경로는 이미
    ``parsing.coerce_datetime`` 으로 ``datetime``/``None`` 만 싣는다.
    """

    model_config = ConfigDict(extra="ignore")

    # --- 식별자 (제로패딩 문자열 — int 변환 금지) -----------------------------
    notice_number: Text = None
    notice_order: Text = None
    notice_full_number: Text = None

    # --- 행 텍스트 -----------------------------------------------------------
    title: Text = None
    bid_classification: Text = None
    bid_progress_order: Text = None
    demand_agency: Text = None
    status: Text = None
    business_type: Text = None

    # --- 개찰/낙찰 결과 -------------------------------------------------------
    scheduled_at: datetime | None = None
    announced_at: datetime | None = None
    opening_amount: RawAmount = None
    winning_company: Text = None
    winning_amount: RawAmount = None
    winning_rate: RawAmount = None
    # 복수예비가격/추첨번호는 그대로 item metadata 로 릴레이된다(원소 검증 없음 — 산출 불변).
    reserve_prices: list[Any] = Field(default_factory=list)
    selected_numbers: list[Any] = Field(default_factory=list)

    # 원시 그리드 행(provenance). 소비자는 읽지 않고 진단/골든에만 쓰인다.
    raw: dict[str, Any] = Field(default_factory=dict)


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
    "OpeningResultRow",
    "RawAmount",
    "RawInstant",
    "ScsbidReserveDetail",
    "Text",
]
