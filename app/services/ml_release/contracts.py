"""ml_release 가 읽고 쓰는 JSON 문서의 계약.

종전에는 manifest / predictor backtest report 를 4개 모듈이 각각 ``json.loads`` +
``isinstance(payload, dict)`` 로 읽었다(manifest.py · preflight.py · gate.py ·
analytics_reporting/ml_release_report.py). 같은 검사가 네 벌이면 한 곳만 고쳐지고,
"객체가 아닌 문서"의 처리도 조용히 갈라진다. 복원 경로를 이 모듈 하나로 모은다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel, ValidationError

__all__ = [
    "MLReleaseJsonDocument",
    "MLReleaseJsonValueDocument",
    "ReleaseStorageProbeObject",
    "is_json_decode_error",
    "json_document_error_detail",
]

# object storage write probe 본문의 고정 표식(두 스토리지 구현이 같은 값을 쓴다).
RELEASE_STORAGE_PROBE_MARKER = "ml-release-rollout"


class MLReleaseJsonDocument(RootModel[dict[str, JsonValue]]):
    """원문 매핑을 **그대로** 써야 하는 문서(release manifest · backtest report).

    왜 필드를 선언하지 않는가 — manifest 는 **서명 대상**이기 때문이다. 검증은 저장된
    매핑에서 ``signature`` 만 제외한 나머지를 canonical JSON(``sort_keys`` + compact,
    :mod:`app.services.ml_release.signing`)으로 다시 직렬화해 HMAC 을 비교한다. 필드를
    선언한 모델로 복원하면 (a) 선언되지 않은 키가 사라지고 (b) 파일에 없던 필드가
    기본값으로 채워져 canonical 바이트가 달라진다 = **이미 발행된 릴리스의 서명 검증이
    전부 깨진다**. 그래서 이 계약이 보장하는 범위는 종전 ``json.loads`` +
    ``isinstance(payload, dict)`` 와 정확히 같다: (1) 본문이 유효한 JSON, (2) 최상위가
    객체. 값은 한 비트도 바꾸지 않는다(:data:`~pydantic.JsonValue` 는 JSON 원시값을
    그대로 담는다).

    ``root`` 를 프로퍼티로 감싸지 않고 그대로 노출한다 — 감싸면 "원문 그대로"라는 이
    모듈의 유일한 성질이 한 겹 숨는다.
    """


class MLReleaseJsonValueDocument(RootModel[JsonValue]):
    """JSON 값 **하나**를 원문 그대로 담는 문서(최상위가 객체가 아닐 수도 있다).

    원격 rebuild 응답처럼 "객체를 기대하지만 아니어도 증적으로 남겨야 하는" 본문에 쓴다
    (그 시점엔 원격 작업이 이미 트리거된 뒤라, 여기서 예외를 던지면 트리거 사실이 기록에서
    사라진다). :class:`MLReleaseJsonDocument` 와 달리 최상위 타입을 강제하지 않으므로 이
    계약이 보장하는 것은 "본문이 유효한 JSON 이다" 하나다.
    """


class ReleaseStorageProbeObject(BaseModel):
    """object storage write probe 가 올리는 임시 객체 본문.

    두 스토리지 구현(로컬 파일 / S3)이 같은 본문을 써야 해서 계약을 한 곳에 둔다. 이
    객체는 기록 직후 삭제되고 아무도 되읽지 않으므로 **바이트 표현이 계약이 아니다**
    (직렬화가 compact 로 바뀌면서 probe 크기가 몇 바이트 줄어들 뿐이고, 크기는
    ``created_at`` 타임스탬프 길이에 따라 이미 가변이다).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    probe: str = RELEASE_STORAGE_PROBE_MARKER
    created_at: str


def is_json_decode_error(exc: ValidationError) -> bool:
    """본문 자체가 JSON 이 아닌가(= 종전 ``json.JSONDecodeError`` 자리).

    최상위가 객체가 아닌 경우(``[]``, ``"x"``)와 구분하기 위한 순수 판정 함수다. 호출부는
    이 둘을 서로 다른 문구/체크 코드로 보고해 왔고, 그 구분을 유지한다.
    """
    return any(error.get("type") in {"json_invalid", "json_type"} for error in exc.errors())


def json_document_error_detail(exc: ValidationError) -> str:
    """운영 리포트에 남길 짧은 실패 사유(pydantic 다중 오류 덤프 대신 첫 메시지)."""
    errors = exc.errors()
    return str(errors[0].get("msg")) if errors else "invalid JSON document"
