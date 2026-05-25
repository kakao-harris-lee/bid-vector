"""Tests for the business_group resolver + config defaults."""

from app.core.config import settings


def test_business_group_code_prefixes_default():
    prefixes = settings.BUSINESS_GROUP_CODE_PREFIXES
    assert prefixes["construction"] == ["04"]
    assert prefixes["service"] == ["06"]
    assert "goods" in prefixes


def test_business_type_coverage_gate_default():
    assert settings.BUSINESS_TYPE_COVERAGE_GATE == 0.95


def test_business_group_calibration_enabled_default():
    assert settings.BUSINESS_GROUP_CALIBRATION_ENABLED is True
