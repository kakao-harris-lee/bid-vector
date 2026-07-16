"""Session-factory injection for StrategyMonitoringService.

The recovery path (``_write_run_failure_on_new_session``) opens a fresh session
when the live one is poisoned. It must do so through the injected
``session_factory`` (default ``SessionLocal``) rather than importing the global
directly, so failure finalization can be exercised without a real DB.
"""

from __future__ import annotations

from app.services.opportunity_monitoring import StrategyMonitoringService


class _FakeQuery:
    """Query double whose lookup yields no matching run."""

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return None


class _FakeDBSession:
    def __init__(self) -> None:
        self.closed = False

    def query(self, *args, **kwargs):
        return _FakeQuery()

    def close(self) -> None:
        self.closed = True


def test_write_run_failure_on_new_session_uses_injected_factory():
    session = _FakeDBSession()
    factory_calls = []

    def factory():
        factory_calls.append(1)
        return session

    service = StrategyMonitoringService(session_factory=factory)

    # No matching run row -> _write_run_failure returns False, but the injected
    # session must still have been created, queried, and closed.
    result = service._write_run_failure_on_new_session(run_id=1, error_message="boom")

    assert result is False
    assert factory_calls == [1]
    assert session.closed is True
