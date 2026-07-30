"""신규 DTO 의 방어적 베이스 모델.

경계(HTTP 요청/응답, celery payload, 파일 산출물)를 원시 ``dict`` 로 흘리면 오타 키가
조용히 무시되고 기본값이 대신 쓰인다. 그래서 **신규 DTO 는 이 베이스를 상속**해
미지 필드를 즉시 거부한다(``extra="forbid"``).

기존 모델의 일괄 전환은 하지 않는다. ``extra`` 기본값(``ignore``)에 의존하는 호출부가
어디까지인지 검증되지 않았기 때문이다. 전환은 **경계를 승격할 때 그 모델만 개별로**
한다(해당 DTO 의 happy/sad 테스트를 함께 추가).

이 모듈은 ``app/schemas/schemas.py`` re-export barrel 에 넣지 않는다. barrel 은 과거
``from app.schemas.schemas import X`` 경로를 유지하기 위한 것이고, 베이스 클래스는
공개 스키마 표면이 아니라 구현 기반이므로 도메인 모듈에서 직접 import 한다::

    from app.schemas._base import StrictModel

    class MyRequest(StrictModel):
        ...
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["FrozenStrictModel", "StrictModel"]


class StrictModel(BaseModel):
    """extra 필드 거부 — 신규 DTO 의 기본 베이스.

    ``protected_namespaces=()`` 는 이 도메인의 필드 이름이 pydantic 의 예약 접두사
    ``model_`` 와 겹치기 때문이다(``model_version`` — 예측 모델 버전은 도메인 용어라
    개명하지 않는다). 기본값이면 그런 필드를 가진 DTO 마다 UserWarning 이 쌓여 진짜
    경고를 묻어 버린다. 예약 접두사 정책은 여기 한 곳에서만 정한다(단일 출처).
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class FrozenStrictModel(StrictModel):
    """extra 필드 거부 + 불변 — 값 객체/스냅샷용 베이스.

    검증 후 변형되면 안 되는 계약(설정 스냅샷, 산출물 레코드)에 쓴다.
    ``extra="forbid"`` 는 ``StrictModel`` 에서 상속한다(pydantic v2 는 부모의
    ``model_config`` 를 병합한다). 같은 값을 두 곳에 적어 두면 한쪽만 바뀌어
    갈라지므로 단일 출처를 유지한다.
    """

    model_config = ConfigDict(frozen=True)
