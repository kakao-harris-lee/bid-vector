"""Cross-worker singleton lease failure handling."""

import pytest

from app.services.task_singleton import AdvisorySingletonLease


class _FailingConnection:
    def __init__(self) -> None:
        self.closed = False

    def execute(self, statement, parameters):
        del statement, parameters
        raise RuntimeError("lock query failed")

    def close(self) -> None:
        self.closed = True


class _PostgresBind:
    class _Dialect:
        name = "postgresql"

    dialect = _Dialect()

    def __init__(self, connection: _FailingConnection) -> None:
        self.engine = self
        self.connection = connection

    def connect(self) -> _FailingConnection:
        return self.connection


def test_advisory_lease_closes_owned_connection_when_acquire_fails():
    connection = _FailingConnection()
    lease = AdvisorySingletonLease(_PostgresBind(connection), "pipeline")

    with pytest.raises(RuntimeError, match="lock query failed"):
        lease.acquire()

    assert connection.closed is True
    assert lease.acquired is False
    assert lease._connection is None
