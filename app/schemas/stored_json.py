"""아직 키 계약이 없는 저장 JSON payload 의 **최소 계약** 모델.

방어적 DTO 규율 Phase 5. 이 저장소의 여러 ``Text`` 컬럼(``latest_evaluation`` /
``summary_json`` / ``metrics_json`` / ``phases`` …)은 생산자마다 자유 형식 dict·list 를
``json.dumps`` 로 밀어 넣고, 소비자마다 ``json.loads`` + ``isinstance`` 검사 + ``{}``
폴백을 **각자 복제**해 왔다. 같은 방어 코드가 파일마다 조금씩 다르게 적혀 있으면
"해석 실패 시 무엇을 돌려주는가"가 파일마다 갈라진다.

여기서 선언하는 것은 키 계약이 아니라 **모양 계약**이다: "이 컬럼은 JSON 객체(또는
배열)를 담는다". 키까지 계약된 payload 는 각 도메인 스키마(예:
:mod:`app.schemas.g2_evidence`, :mod:`app.schemas.analytics_events`)에 선언하고 그
모델로 직접 되읽는 것이 맞다. 이 모듈은 **키 계약 승격이 아직 안 된 payload** 가 최소한
``json`` 모듈 직접 호출과 파일별 폴백 복제 없이 한 경로로 흐르게 하는 징검다리다
(승격되면 여기서 빠진다).

``JsonValue`` 는 pydantic 이 제공하는 재귀 JSON 타입이라 ``Any`` 와 달리 "JSON 원시값
트리"라는 제약이 타입에 남는다 — 되읽은 값에 datetime 같은 비 JSON 잎이 있을 수 없음이
호출부에서 보인다.
"""

from __future__ import annotations

from pydantic import Field, JsonValue, RootModel

__all__ = ["StoredJsonArray", "StoredJsonObject", "StoredJsonValue"]


class StoredJsonObject(RootModel[dict[str, JsonValue]]):
    """JSON **객체**를 담는 저장 컬럼의 모양 계약.

    빈 기본값을 둔 이유: 컬럼이 ``"{}"`` 로 초기화되는 생산자가 있어(예:
    ``DecisionExperimentRun.latest_evaluation``) 빈 객체가 정상 상태다.
    """

    root: dict[str, JsonValue] = Field(default_factory=dict)


class StoredJsonArray(RootModel[list[JsonValue]]):
    """JSON **배열**을 담는 저장 컬럼의 모양 계약(예: ``SmokeTestRun.phases``)."""

    root: list[JsonValue] = Field(default_factory=list)


class StoredJsonValue(RootModel[JsonValue]):
    """모양조차 한 가지로 고정되지 않은 저장 컬럼 (객체 **또는** 배열).

    가장 약한 계약이라 신규 코드에서 쓰지 않는다. 한 헬퍼가 객체 컬럼과 배열 컬럼을
    함께 읽는 기존 경로(``synthetic_experiment`` 의 ``params_json`` 과
    ``operator_slugs_json``)를 ``json`` 직접 호출 없이 한 경로로 모으기 위한 것이고,
    호출부가 컬럼별로 객체/배열로 갈라지면 그때 위의 두 모델로 승격한다.
    """

    root: JsonValue = None
