"""Guards for UTC/KST boundary handling.

The app computes internally in UTC, but two boundaries must use the Korean
calendar day: KONEPS queries (their opening dates are KST) and any operator
"today" semantics. These tests lock that in, plus that timestamps written to
aware columns use the aware ``utc_now`` helper (so they serialize WITH an
offset and the frontend parses them as UTC, not local).
"""

from datetime import UTC, datetime

from app.core.time import KST, kst_now, to_kst, utc_now
from app.models.models import SyntheticExperiment, SyntheticExperimentRun
from app.schemas.schemas import CrawlRequest
from app.services.koneps.collector import KonepsCollectorService


def test_utc_now_and_kst_now_are_aware_and_9h_apart():
    u = utc_now()
    k = kst_now()
    assert u.tzinfo is not None and k.tzinfo is not None
    assert k.utcoffset().total_seconds() == 9 * 3600
    # Same instant, just different frames.
    assert abs((u - k).total_seconds()) < 5


def test_to_kst_converts_to_korean_calendar_day():
    """to_kst shifts an aware/naive datetime into the KST frame (for day cohorts)."""
    aware = datetime(2026, 6, 26, 20, 0, tzinfo=UTC)  # == 2026-06-27 05:00 KST
    k = to_kst(aware)
    assert k.utcoffset().total_seconds() == 9 * 3600
    assert (k.year, k.month, k.day, k.hour) == (2026, 6, 27, 5)
    # naive is assumed UTC, then shifted to KST (next day).
    assert to_kst(datetime(2026, 6, 26, 20, 0)).day == 27


def test_scsbid_date_window_uses_kst_day_not_utc(monkeypatch):
    """At 2026-06-26T20:00Z it is already 2026-06-27 in KST. KONEPS opening dates
    are KST, so the rolling window's "today" must be 06-27, not the UTC 06-26."""
    service = KonepsCollectorService()
    instant_utc = datetime(2026, 6, 26, 20, 0, tzinfo=UTC)  # == 06-27 05:00 KST
    monkeypatch.setattr(
        "app.services.koneps.collector.kst_now",
        lambda: instant_utc.astimezone(KST),
    )
    begin, end = service._scsbid_date_window(
        CrawlRequest(source="scsbid-openapi", lookback_days=1)
    )
    assert end == "202606272359"  # KST day — NOT 202606262359 (the UTC day)
    assert begin == "202606260000"


def test_normalize_request_target_date_defaults_to_kst_day(monkeypatch):
    service = KonepsCollectorService()
    instant_utc = datetime(2026, 6, 26, 20, 0, tzinfo=UTC)  # == 06-27 KST
    monkeypatch.setattr(
        "app.services.koneps.collector.kst_now",
        lambda: instant_utc.astimezone(KST),
    )
    normalized = service._normalize_request(CrawlRequest(source="koneps-openapi"))
    assert normalized.target_date == "2026-06-27"  # KST day, not 2026-06-26


def test_synthetic_mark_functions_use_aware_utc(test_db, monkeypatch):
    """mark_running/mark_failed must stamp aware UTC (via utc_now), not naive
    datetime.utcnow() — naive values serialize without an offset and the frontend
    then mis-reads them as local (KST), a 9h display error."""
    from app.services import synthetic_experiment as se

    produced = []
    real_utc_now = se.utc_now

    def spy():
        value = real_utc_now()
        produced.append(value)
        return value

    monkeypatch.setattr(se, "utc_now", spy)

    experiment = SyntheticExperiment(
        name="tz-guard", params_json="{}", operator_slugs_json="[]"
    )
    test_db.add(experiment)
    test_db.flush()
    run = SyntheticExperimentRun(experiment_id=experiment.id, status="queued")
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)

    service = se.SyntheticExperimentService(test_db)
    service.mark_running(run.id)
    service.mark_failed(run.id, "boom")

    assert produced, "mark_* should stamp time via the aware utc_now helper"
    assert all(v.tzinfo is not None for v in produced)
