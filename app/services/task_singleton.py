"""PostgreSQL session advisory leases for cross-worker singleton tasks."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine


def singleton_lock_id(key: str) -> int:
    raw = hashlib.sha256(key.encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


class AdvisorySingletonLease:
    """Hold a PostgreSQL session lock on a dedicated pooled connection."""

    def __init__(self, bind: Engine | Connection, key: str) -> None:
        self._bind = bind
        self._key = singleton_lock_id(key)
        self._connection: Connection | None = None
        self._owns_connection = False
        self.acquired = False

    def acquire(self) -> bool:
        dialect = self._bind.dialect.name
        if dialect != "postgresql":
            self.acquired = True
            return True
        if isinstance(self._bind, Engine):
            self._connection = self._bind.connect()
            self._owns_connection = True
        else:
            self._connection = self._bind.engine.connect()
            self._owns_connection = True
        try:
            self.acquired = bool(
                self._connection.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": self._key},
                ).scalar()
            )
        except Exception:
            self.release()
            raise
        if not self.acquired:
            self.release()
        return self.acquired

    def release(self) -> None:
        try:
            if self.acquired and self._connection is not None:
                self._connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": self._key},
                )
        finally:
            self.acquired = False
            if self._owns_connection and self._connection is not None:
                self._connection.close()
            self._connection = None
            self._owns_connection = False

    def __enter__(self) -> "AdvisorySingletonLease":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.release()
