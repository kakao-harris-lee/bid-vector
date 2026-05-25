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


def test_resolve_business_group_by_prefix():
    from app.ai.business_group import resolve_business_group

    assert resolve_business_group("0411") == "construction"
    assert resolve_business_group("0621") == "service"
    assert resolve_business_group("0101") == "goods"
    assert resolve_business_group("9999") is None
    assert resolve_business_group(None) is None
    assert resolve_business_group("") is None


def test_resolve_business_group_uses_config_overrides(monkeypatch):
    from app.ai.business_group import resolve_business_group

    override = {
        "construction": ["07"],
        "service": ["08"],
    }
    monkeypatch.setattr(
        "app.core.config.settings.BUSINESS_GROUP_CODE_PREFIXES",
        override,
        raising=False,
    )
    assert resolve_business_group("0711") == "construction"
    assert resolve_business_group("0411") is None
