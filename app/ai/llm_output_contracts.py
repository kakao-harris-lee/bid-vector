"""LLM 이 돌려주는 JSON 출력의 계약.

LLM 출력은 이 시스템이 받는 입력 중 **가장 신뢰할 수 없는 것**이다. 프롬프트가 JSON 을
요구해도 모델은 산문, 코드펜스, 키 누락, 타입이 어긋난 값(문자열로 온 점수, 리스트 대신
문자열)을 언제든 돌려줄 수 있다. 종전에는 ``json.loads`` 로 읽어 검증되지 않은 ``Mapping``
으로 흘렸고, 뒤쪽 정규화 함수가 ``.get()`` 으로 짐작했다. 여기서는 기대 스키마를 선언하고
복원은 ``model_validate_json`` 한 곳만 쓴다.

degrade 정책(종전 동작 보존):

* **필드 단위 관용.** 타입이 어긋난 필드만 기본값(빈 리스트 / ``0.0``)으로 떨어지고, 같은
  응답의 나머지 정상 필드는 살린다. 응답 하나를 통째로 버리면 "점수 하나가 문자열"인 흔한
  실패에서 요구사항 목록까지 사라진다.
* **문서 전체 실패**(JSON 아님 / 최상위가 객체 아님)는 호출부가 잡아 빈 결과로 degrade
  하고 경고를 남긴다. **LLM 응답 원문은 로그에 남기지 않는다**(문서 본문·개인정보가 섞여
  들어올 수 있고 크기도 예측할 수 없다).
* ``extra="ignore"`` — 모델이 덧붙이는 부가 키(설명, 근거)는 계약 위반이 아니다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

__all__ = ["LLMDocumentAnalysisOutput"]


class LLMDocumentAnalysisOutput(BaseModel):
    """문서 분석 LLM 출력의 기대 스키마(``DocumentAnalysisPort`` 반환 필드와 1:1)."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    key_requirements: list[JsonValue] = Field(default_factory=list)
    complexity_score: float = 0.0
    estimated_effort: float = 0.0
    risks: list[JsonValue] = Field(default_factory=list)

    @field_validator("key_requirements", "risks", mode="before")
    @classmethod
    def _coerce_list_field(cls, value: JsonValue) -> JsonValue:
        """리스트가 아니면 빈 리스트 — 종전 ``_coerce_list`` 와 동일한 판정."""
        return value if isinstance(value, list) else []

    @field_validator("complexity_score", "estimated_effort", mode="before")
    @classmethod
    def _coerce_float_field(cls, value: JsonValue) -> float:
        """숫자로 해석되지 않으면 ``0.0`` — 종전 ``_coerce_float`` 와 동일한 판정.

        ``bool`` 을 배제하는 것도 종전 동작이다(``True`` 를 1.0 짜리 점수로 승격하면
        "복잡도 1.0" 이라는 없는 근거가 생긴다).
        """
        if value is None or isinstance(value, bool):
            return 0.0
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
