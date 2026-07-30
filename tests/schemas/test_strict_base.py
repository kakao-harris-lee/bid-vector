"""``app.schemas._base`` 방어적 베이스 모델 계약 테스트.

신규 DTO 는 ``StrictModel``/``FrozenStrictModel`` 을 상속해 **미지 필드를 거부**한다.
경계에서 조용히 흘러들어온 오타 키가 기본값으로 무시되는 사고(방어적 DTO 규율 Phase 0)
를 막는 것이 목적이므로, happy path 뿐 아니라 거부 경로를 함께 고정한다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas._base import FrozenStrictModel, StrictModel


class _Sample(StrictModel):
    name: str
    count: int
    ratio: float = 0.5


class _FrozenSample(FrozenStrictModel):
    name: str
    count: int


class TestStrictModel:
    def test_valid_payload_validates(self) -> None:
        model = _Sample.model_validate({"name": "a", "count": 3, "ratio": 1.5})
        assert model.name == "a"
        assert model.count == 3
        assert model.ratio == 1.5

    def test_default_is_applied_when_optional_field_absent(self) -> None:
        model = _Sample.model_validate({"name": "a", "count": 3})
        assert model.ratio == 0.5

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _Sample.model_validate({"name": "a", "count": 3, "typo": 1})
        assert "typo" in str(excinfo.value)

    def test_missing_required_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            _Sample.model_validate({"name": "a"})
        assert "count" in str(excinfo.value)

    def test_type_mismatch_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _Sample.model_validate({"name": "a", "count": "not-an-int"})

    def test_extra_forbid_applies_to_json_validation(self) -> None:
        with pytest.raises(ValidationError):
            _Sample.model_validate_json('{"name": "a", "count": 1, "typo": true}')


class TestFrozenStrictModel:
    def test_valid_payload_validates(self) -> None:
        model = _FrozenSample.model_validate({"name": "a", "count": 1})
        assert model.name == "a"

    def test_assignment_is_rejected(self) -> None:
        model = _FrozenSample(name="a", count=1)
        with pytest.raises(ValidationError):
            model.name = "b"

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _FrozenSample.model_validate({"name": "a", "count": 1, "typo": 1})

    def test_inherits_extra_forbid_from_strict_model(self) -> None:
        """``extra="forbid"`` 단일 출처 — frozen 베이스는 StrictModel 을 상속한다."""
        assert issubclass(FrozenStrictModel, StrictModel)
        assert FrozenStrictModel.model_config["extra"] == "forbid"
        assert FrozenStrictModel.model_config["frozen"] is True
        assert _FrozenSample.model_config["extra"] == "forbid"
