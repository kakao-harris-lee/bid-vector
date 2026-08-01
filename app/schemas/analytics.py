"""Analytics, document-analysis and notification response schemas."""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from app.core.constants import ClientAnalyticsEventType
from app.schemas.analytics_events import enforce_client_payload_caps

# ``POST /analytics/event`` payload 상한(요청 검증 계약 — 이 경계에서 선언한다).
#
# 이 엔드포인트는 클라이언트 텔레메트리 싱크이고 ``event_data`` 의 키 집합을 서버가 좁히지
# 않으므로(envelope 통과), 상한이 없으면 한 요청이 ``analytics.event_data`` Text 컬럼에
# 임의 크기 문자열을 넣을 수 있다. 다른 요청 DTO 의 범위 제약(``Field(ge=…, le=…)`` /
# ``Query(le=…)``)과 같은 성격이라 환경 설정(``Settings``)이 아니라 스키마 경계에
# 선언하고 순수 판정 함수(``enforce_client_payload_caps``)에 주입한다(§4.5-1/§4.7-3).
# 실제 프론트 payload 는 키 3개 · 100자 미만이라 두 값은 여유를 크게 둔 상한이다.
ANALYTICS_EVENT_MAX_PAYLOAD_KEYS = 32
ANALYTICS_EVENT_MAX_PAYLOAD_CHARS = 4096


class DocumentAnalysisRequest(BaseModel):
    project_id: int
    document_content: str
    document_type: str


class DocumentAnalysisResponse(BaseModel):
    key_requirements: List[str]
    complexity_score: float
    estimated_effort: float
    risks: List[str]


class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AnalyticsEventRequest(BaseModel):
    # 클라이언트 텔레메트리 1건. 방어선은 셋이고 모두 선언적으로 둔다:
    #
    # 1. ``event_type`` — 프론트가 실제로 올리는 이벤트 어휘로 좁힌다
    #    (``ClientAnalyticsEventType``). 미지 타입은 422 이며, 그런 타입은 어차피 복원
    #    레지스트리에 계약이 없어 아무 소비처도 읽지 못한다.
    # 2. ``event_data`` 의 키 *집합* 은 열어 둔다 — 새 키가 거부되면 프론트 배포가 서버
    #    배포에 묶인다. 대신 위에 선언한 *크기* 상한(키 수/직렬화 길이)을 강제한다.
    # 3. 인증은 라우터(``app/api/analytics.py``)의 bearer 의존성이 담당하고, 적재 행은
    #    토큰 소유자에게 귀속된다. 그 분리의 **범위는 좁다**: 별도 계정으로 로그인한
    #    운영자(예: 자체 토큰을 쓰는 ``synthetic-*``)만 자기 행으로 갈리고, canonical
    #    토큰으로 ``?operator_id=`` 만 바꿔 다른 회사를 보는 흐름은 토큰이 그대로라
    #    여전히 canonical 귀속이다(쓰기 경로의 operator 컨텍스트 전달은 후속 과제).
    event_type: ClientAnalyticsEventType
    # NOTE: 필드는 bare ``dict`` 를 유지한다. ``dict[str, JsonValue]`` 로 좁히면 pydantic 이
    # ``additionalProperties: {$ref: JsonValue}`` 와 **빈 ``JsonValue`` 컴포넌트**를 스펙에
    # 추가해 공개 OpenAPI 산출이 바뀐다(프론트 타입 이득 없음). 값 타입 계약은 아래
    # validator 시그니처가 들고, 실제 방어는 크기 상한이 한다.
    event_data: dict

    @field_validator("event_data")
    @classmethod
    def _cap_event_data(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """저장 전 payload 크기 상한을 강제한다(초과 시 422).

        상한은 모듈 상수를 **호출 시점에** 읽어 주입한다 — 판정 함수는 상한을 모르는
        순수 함수이고(값 테이블 검증), 테스트는 이 모듈 상수만 바꿔 경계를 옮긴다.
        """
        return enforce_client_payload_caps(
            value,
            max_keys=ANALYTICS_EVENT_MAX_PAYLOAD_KEYS,
            max_chars=ANALYTICS_EVENT_MAX_PAYLOAD_CHARS,
        )


class AnalyticsSummaryResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_projects: int
    total_events: int
    mode: Literal["single_operator"] = "single_operator"


class OperatorStatsResponse(BaseModel):
    operator_id: int
    period_days: int
    total_bids: int
    total_events: int
    bids_count: int
    requested_user_id: Optional[int] = None
    mode: Literal["single_operator"] = "single_operator"
