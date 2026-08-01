"""저장 JSON payload 복원 단일 경로 — 해석 실패 정책을 한 곳에서 정한다.

종전에는 소비처마다 ``json.loads`` + ``except JSONDecodeError`` + ``isinstance`` 검사 +
``{}``/``[]`` 폴백이 복제되어 있었다(analytics_reporting / opportunity_monitoring /
synthetic_experiment / decision_experiments …). 같은 방어를 여러 곳에 적어 두면 한쪽만
바뀌어 "해석 실패 시 무엇이 되는가"가 갈라지고, 그 차이가 대시보드 숫자로 조용히 나온다.

복원 정책(P1 ``paper_bidding_run_payload`` / P4.2 ``analytics_event_payload`` 와 동일):

* 해석 불가/모양 불일치는 ``None``(부재)이다. 빈 컨테이너로 degrade 할지, 부재로 남길지는
  **호출부가 명시**한다(``load_... or {}``) — 어느 쪽 정책인지를 이 모듈이 숨기지 않는다.
* degrade 는 조용해서는 안 된다: 어느 컬럼에서 몇 건의 오류로 실패했는지
  ``logger.warning`` 으로 남긴다. **payload 원문은 로그에 넣지 않는다**(§8 — 저장 payload
  에는 운영자 판단 근거가 들어 있고 길이도 통제되지 않는다).
* 값이 아예 없는 컬럼(``None``/빈 문자열/``"{}"``)은 정상적인 부재이므로 경고하지 않는다.

키 계약이 선언된 payload 는 이 모듈을 쓰지 않고 그 도메인 모델로 직접
``model_validate_json`` 한다(예: :mod:`app.schemas.g2_evidence`). 여기는 **아직 키 계약이
없는** payload 전용 경로다(:mod:`app.schemas.stored_json`).
"""

from __future__ import annotations

import logging

from pydantic import JsonValue, ValidationError

from app.schemas.stored_json import StoredJsonArray, StoredJsonObject, StoredJsonValue

logger = logging.getLogger(__name__)

__all__ = [
    "load_stored_json_array",
    "load_stored_json_object",
    "load_stored_json_value",
]


def _warn_degraded(context: str, shape: str, *, errors: int) -> None:
    """degrade 된 payload 를 추적 가능하게 남긴다(원문은 로그에 넣지 않는다)."""
    logger.warning(
        "저장 JSON payload 해석 실패 — 부재로 처리 (column=%s, shape=%s, errors=%d)",
        context or "unknown",
        shape,
        errors,
    )


def load_stored_json_object(
    raw_value: str | None, *, context: str = ""
) -> dict[str, JsonValue] | None:
    """저장된 JSON **객체**를 되읽는다. 해석 불가/객체가 아니면 ``None``.

    ``context`` 는 어느 컬럼이 degrade 됐는지 로그로 특정하기 위한 라벨이다
    (``"decision_experiment_run.latest_evaluation"`` 처럼 컬럼 이름을 넘긴다).
    """
    if not raw_value:
        return None
    try:
        return StoredJsonObject.model_validate_json(raw_value).root
    except ValidationError as exc:
        _warn_degraded(context, "object", errors=exc.error_count())
        return None


def load_stored_json_array(
    raw_value: str | None, *, context: str = ""
) -> list[JsonValue] | None:
    """저장된 JSON **배열**을 되읽는다. 해석 불가/배열이 아니면 ``None``."""
    if not raw_value:
        return None
    try:
        return StoredJsonArray.model_validate_json(raw_value).root
    except ValidationError as exc:
        _warn_degraded(context, "array", errors=exc.error_count())
        return None


def load_stored_json_value(
    raw_value: str | None, *, context: str = ""
) -> JsonValue | None:
    """모양을 고정하지 않고 저장된 JSON 값을 되읽는다. 해석 불가면 ``None``.

    한 헬퍼가 객체 컬럼과 배열 컬럼을 함께 읽는 기존 경로 전용이다
    (:class:`~app.schemas.stored_json.StoredJsonValue` 참고). 컬럼별로 모양이 갈라지면
    ``load_stored_json_object`` / ``load_stored_json_array`` 로 승격한다.
    """
    if not raw_value:
        return None
    try:
        return StoredJsonValue.model_validate_json(raw_value).root
    except ValidationError as exc:
        _warn_degraded(context, "value", errors=exc.error_count())
        return None
