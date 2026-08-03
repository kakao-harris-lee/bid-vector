from app.core.schema_bootstrap import startup_schema_bootstrap_enabled


def test_production_lifespan_never_runs_schema_bootstrap():
    assert startup_schema_bootstrap_enabled("production") is False
    assert startup_schema_bootstrap_enabled("staging") is False


def test_development_keeps_first_boot_schema_bootstrap():
    assert startup_schema_bootstrap_enabled("development") is True
    assert startup_schema_bootstrap_enabled("test") is True
