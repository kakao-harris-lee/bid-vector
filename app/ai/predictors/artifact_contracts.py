"""저장된 predictor 아티팩트(JSON)의 읽기 계약.

종전에는 아티팩트를 ``json.loads`` 로 읽어 원시 ``dict`` 로 들고 다녔다. 그래서 키가
바뀌거나 타입이 어긋나도 **읽는 쪽에서 아무도 실패하지 않고** ``.get()`` 기본값이 조용히
대신 쓰였다(사정률 산출이 조용히 달라지는 경로). 여기서는 각 아티팩트의 **필드 집합을
선언**하고, 복원은 ``model_validate_json``/``model_validate`` 한 곳만 쓴다.

설계 판단(관용성):

* ``extra="ignore"`` — 학습이 아티팩트에 ``summary.*`` 블록(group/probability
  calibration)을 덧붙이고, 미래 필드도 추가될 수 있다. ``forbid`` 로 두면 정상 학습
  산출물이 "계약 위반"으로 거부되어 predictor 가 조용히 unavailable 이 된다.
* 스칼라는 :data:`ArtifactScalar` 로 느슨하게 받는다. 종전 코드가
  ``str(raw.get(k) or default)`` / ``int(... or default)`` / ``float(... or default)`` 로
  **문자열로 저장된 숫자까지 관용적으로 강제 변환**했기 때문이다. 그 강제 변환식은 호출부에
  그대로 남기고(산출 불변), 이 모델은 "필드가 스칼라다"까지만 보장한다.
* ``component_weights`` 는 종전 ``isinstance(x, dict)`` 검사를 그대로 옮긴
  before-validator 로 **매핑이 아니면 부재로** 떨어뜨린다. 예외로 바꾸면 과거 산출물이
  거부된다.

읽기 전용 계약이라 모두 ``frozen=True`` 다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from app.ai.predictors.artifact_provider import (
    ArtifactSource,
    VersionAwareArtifactProvider,
)

__all__ = [
    "ArtifactSource",
    "ArtifactScalar",
    "CalibrationValue",
    "PersistedArtifactCalibrationBlocks",
    "PersistedArtifactSummaryDocument",
    "PersistedEnsembleArtifact",
    "StrictArtifactCalibrationBlocks",
    "StrictArtifactSummaryDocument",
    "VersionAwareArtifactProvider",
    "artifact_contract_error_kind",
    "artifact_contract_error_location",
    "read_persisted_artifact",
]

logger = logging.getLogger(__name__)

# 아티팩트에 저장될 수 있는 스칼라. 숫자를 문자열로 적어 둔 과거 산출물도 통과시키고,
# 실제 형변환은 호출부의 기존 ``int()``/``float()``/``str()`` 식이 그대로 담당한다.
ArtifactScalar = str | int | float | bool | None


# calibration 표의 항목 값(중첩 컨테이너 없음). 학습이 숫자·문자열(``method``,
# ``label_source``)·정수 카운트를 함께 넣는다.
CalibrationValue = float | int | str | bool | None

# 위 유니온의 런타임 판정용 튜플(entry 단위 관용에서 leaf 를 검사한다). ``None`` 은
# isinstance 로 볼 수 없어 호출부에서 따로 허용한다.
_CALIBRATION_LEAF_TYPES = (str, int, float, bool)

_JSON_DECODE_ERROR_TYPES = frozenset({"json_invalid", "json_type"})

class _ArtifactReadModel(BaseModel):
    """저장 아티팩트 읽기 베이스 — 미지 필드 무시 + 불변.

    ``protected_namespaces=()`` 는 ``model_version`` 이 이 도메인의 용어이기 때문이다
    (``app/schemas/_base.py`` 와 같은 이유).
    """

    model_config = ConfigDict(extra="ignore", frozen=True, protected_namespaces=())


def artifact_contract_error_kind(exc: ValidationError) -> str:
    """``"json"``(디코딩 실패) 또는 ``"contract"``(디코딩은 됐지만 계약 위반).

    호출부가 종전 오류 문구("... is not valid JSON: <path>")를 유지하면서, 새로 잡히는
    계약 위반을 다른 문구로 구분해 보고할 수 있게 하는 순수 판정 함수다.
    """
    return (
        "json"
        if any(error.get("type") in _JSON_DECODE_ERROR_TYPES for error in exc.errors())
        else "contract"
    )


def artifact_contract_error_location(exc: ValidationError) -> str:
    """첫 위반 지점의 경로(``summary.group_calibration.service.median_rate``).

    최상위 자체가 어긋난 경우(문서가 객체가 아님)는 빈 문자열이다. 게이트가 "무엇이
    위반인지"를 정확히 보고하도록(오진단 문구 방지) 쓰는 순수 함수다.
    """
    errors = exc.errors()
    location = errors[0].get("loc") or () if errors else ()
    return ".".join(str(part) for part in location)


def _mapping_or_none(value: JsonValue) -> JsonValue:
    """매핑이 아니면 부재(None) — 종전 ``isinstance(x, dict)`` 검사와 같은 관용."""
    return value if isinstance(value, Mapping) else None


def _mapping_or_empty(value: JsonValue) -> JsonValue:
    """매핑이 아니면 빈 표 — 종전 ``x if isinstance(x, dict) else {}`` 와 같은 관용."""
    return value if isinstance(value, Mapping) else {}


def _is_calibration_entry(entry: JsonValue) -> bool:
    """한 entry 가 "스칼라 leaf 만 가진 매핑"인가(표 계약의 최소 조건)."""
    return isinstance(entry, Mapping) and all(
        leaf is None or isinstance(leaf, _CALIBRATION_LEAF_TYPES)
        for leaf in entry.values()
    )


def _calibration_table_or_empty(value: JsonValue) -> JsonValue:
    """비매핑 표는 빈 표로, 계약을 어긴 **entry 만** 드롭한다(+경고).

    leaf 하나가 어긋났다고 표 전체(나아가 같은 문서의 다른 표까지)를 버리면 종전보다
    후퇴한다 — 두 보정 표는 원래 각각 따로 읽혔고, 한 그룹의 손상이 다른 그룹의 보정을
    없애지 않았다. 그래서 관용의 단위를 문서가 아니라 **entry** 로 둔다. 드롭은 조용해서는
    안 되므로 그룹 키만 경고에 남긴다(값은 남기지 않는다).
    """
    if not isinstance(value, Mapping):
        return {}
    kept: dict[str, JsonValue] = {}
    dropped: list[str] = []
    for key, entry in value.items():
        if _is_calibration_entry(entry):
            kept[str(key)] = entry
        else:
            dropped.append(str(key))
    if dropped:
        logger.warning(
            "아티팩트 calibration entry 계약 위반 — 해당 entry 만 제외 (keys=%s)",
            ",".join(sorted(dropped)),
        )
    return kept


class PersistedEnsembleArtifact(_ArtifactReadModel):
    """앙상블 아티팩트.

    은퇴 이전 산출물은 ``lstm_artifact``/``lstm_artifact_path`` 를 함께 들고 있지만,
    sequence-model 축이 2026-08-09 은퇴하면서 여기서 선언하지 않는다. 베이스가
    ``extra="ignore"`` 라 그 키는 **읽을 때 조용히 버려질 뿐 로딩을 깨뜨리지 않는다** —
    디스크의 과거 아티팩트를 지우지 않고도 계속 읽을 수 있게 하는 것이 이 관용의 목적이다.
    """

    artifact_version: ArtifactScalar = None
    model_version: ArtifactScalar = None
    sequence_length: ArtifactScalar = None
    momentum_window: ArtifactScalar = None
    scenario_spread_multiplier: ArtifactScalar = None
    confidence_bias: ArtifactScalar = None
    component_weights: dict[str, ArtifactScalar] | None = None

    @field_validator("component_weights", mode="before")
    @classmethod
    def _tolerate_non_mapping(cls, value: JsonValue) -> JsonValue:
        return _mapping_or_none(value)


class PersistedArtifactCalibrationBlocks(_ArtifactReadModel):
    """아티팩트 ``summary`` 안의 보정 표들(추론 경로 — 관용적으로 읽는다).

    관용의 단위는 **entry** 다: 부재·비매핑 표는 빈 표, 계약을 어긴 그룹 entry 는 그
    entry 만 드롭한다. 표 하나의 손상이 다른 표를, 그룹 하나의 손상이 다른 그룹의 보정을
    끌어내리지 않는다.
    """

    group_calibration: dict[str, dict[str, CalibrationValue]] = Field(
        default_factory=dict
    )
    probability_calibration: dict[str, dict[str, CalibrationValue]] = Field(
        default_factory=dict
    )

    @field_validator("group_calibration", "probability_calibration", mode="before")
    @classmethod
    def _tolerate_broken_entries(cls, value: JsonValue) -> JsonValue:
        return _calibration_table_or_empty(value)


class PersistedArtifactSummaryDocument(_ArtifactReadModel):
    """활성 아티팩트에서 ``summary`` 블록만 보는 뷰(예측 경로의 보정 표 읽기)."""

    summary: PersistedArtifactCalibrationBlocks = Field(
        default_factory=PersistedArtifactCalibrationBlocks
    )

    @field_validator("summary", mode="before")
    @classmethod
    def _tolerate_non_mapping(cls, value: JsonValue) -> JsonValue:
        return _mapping_or_empty(value)


class StrictArtifactCalibrationBlocks(_ArtifactReadModel):
    """보정 표들 — **관용 없이** 읽는다(게이트 전용).

    추론 경로의 관용(:class:`PersistedArtifactCalibrationBlocks`)을 게이트에 쓰면 손상된
    블록이 "블록 없음"으로 접혀 **거부가 통과로 바뀐다**. 게이트는 거부가 안전한 방향이므로
    위반을 접지 않고 그대로 드러낸다(호출부가 ``ok=False`` 로 fail-closed).
    """

    group_calibration: dict[str, dict[str, CalibrationValue]] = Field(
        default_factory=dict
    )
    probability_calibration: dict[str, dict[str, CalibrationValue]] = Field(
        default_factory=dict
    )


class StrictArtifactSummaryDocument(_ArtifactReadModel):
    """게이트가 읽는 ``summary`` 뷰. 블록 **부재는 허용**, 손상은 거부.

    ``summary`` 가 아예 없는 아티팩트는 정상이다(보정 블록은 선택적). 반면 ``summary`` 나
    그 안의 표가 매핑이 아니거나 entry leaf 가 계약을 어기면 검증이 실패하고, 호출부는 그
    위반 위치를 사유에 담아 승격을 거부한다.
    """

    summary: StrictArtifactCalibrationBlocks | None = None


_ArtifactModelT = TypeVar("_ArtifactModelT", bound=_ArtifactReadModel)


def read_persisted_artifact(
    model_source: ArtifactSource,
    *,
    model: type[_ArtifactModelT],
    label: str,
) -> _ArtifactModelT:
    """임베드된 매핑 또는 저장된 JSON 파일을 아티팩트 계약으로 복원한다.

    앙상블·summary 로더가 같은 절차(경로면 읽고 복원, 매핑이면 그대로 복원)를 쓰므로 한
    곳에 둔다. 실패는 모두 ``ValueError`` 로 통일한다 — predictor 의 ``check_availability`` 와
    release manifest 검증이 이미 그 형태를 기대하고, 예외 종류가 바뀌면 predictor 가
    degrade 대신 폭발한다. 디코딩 실패("파일이 깨졌다")와 계약 위반("필드 타입이
    다르다")은 서로 다른 문구로 보고한다.
    """
    if isinstance(model_source, dict):
        try:
            return model.model_validate(model_source)
        except ValidationError as exc:
            raise ValueError(
                f"Embedded {label} does not match the artifact contract "
                f"({exc.error_count()} error(s))"
            ) from exc

    path = Path(model_source)
    try:
        artifact_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"{label} was not found: {path}") from exc
    try:
        return model.model_validate_json(artifact_text)
    except ValidationError as exc:
        if artifact_contract_error_kind(exc) == "json":
            raise ValueError(f"{label} is not valid JSON: {path}") from exc
        raise ValueError(
            f"{label} does not match the artifact contract: {path}"
        ) from exc
