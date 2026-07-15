"""Self-healing celery beat scheduler.

The default PersistentScheduler stores its schedule in a shelve/dbm file. On
this host that file recurrently corrupts (``_dbm.error: cannot add item to
database``). Celery only recovers corruption on the *open* path, not on writes
(``sync``/``set_schedule``/``update``), so a corrupt-on-write file crash-loops
beat and halts the whole scheduled pipeline until the file is deleted by hand.

This scheduler catches dbm/corruption errors on the write paths, purges the
corrupt file, and rebuilds the schedule from ``app.conf.beat_schedule`` (the
code-defined schedule), turning the crash-loop into automatic in-process
recovery.
"""
from __future__ import annotations

import importlib

from celery.beat import PersistentScheduler
from celery.utils.log import get_logger

logger = get_logger(__name__)


def _corruption_error_types() -> tuple[type[BaseException], ...]:
    """Collect the concrete dbm backend error types plus ``OSError``.

    Subtlety: ``_dbm.error`` (the ndbm backend used by the default shelve db on
    this host) is NOT a subclass of the generic ``dbm.error``. ``dbm.error`` is
    itself the tuple ``(dbm.error, OSError)``. To reliably catch the real
    ``_dbm.error: cannot add item to database`` we must gather each backend's
    ``.error`` type explicitly.
    """
    errors: set[type[BaseException]] = {OSError}
    import dbm

    if isinstance(dbm.error, tuple):
        errors.update(dbm.error)
    else:
        errors.add(dbm.error)
    for backend in ("dbm.gnu", "dbm.ndbm", "dbm.dumb"):
        try:
            errors.add(importlib.import_module(backend).error)
        except ImportError:
            continue
    return tuple(errors)


CORRUPTION_ERRORS = _corruption_error_types()


class SelfHealingScheduler(PersistentScheduler):
    """PersistentScheduler that auto-recovers from a corrupt schedule db."""

    _healing = False

    def _rebuild_schedule(self, exc: BaseException) -> None:
        if self._healing:
            # Corruption during recovery itself — fail fast instead of looping.
            raise exc
        self._healing = True
        try:
            logger.warning(
                "Schedule db %r corrupted (%r); purging and rebuilding from "
                "beat_schedule.",
                self.schedule_filename,
                exc,
            )
            self._remove_db()
            super().setup_schedule()
        finally:
            self._healing = False

    def setup_schedule(self) -> None:
        try:
            super().setup_schedule()
        except CORRUPTION_ERRORS as exc:
            self._rebuild_schedule(exc)

    def sync(self) -> None:
        try:
            super().sync()
        except CORRUPTION_ERRORS as exc:
            self._rebuild_schedule(exc)
