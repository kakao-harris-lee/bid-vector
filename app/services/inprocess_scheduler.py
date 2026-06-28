"""Shared lifecycle for in-process periodic schedulers.

Both :class:`PaperBiddingForwardScheduler` and :class:`OperatorStrategyScheduler`
run a periodic job through an in-process asyncio loop when the configured Celery
broker is in-memory and therefore cannot back Celery beat/worker. The lifecycle
(``start``/``stop``/``_run_loop``/``_run_once``/``should_run_inprocess``) is
identical between them; only configuration sources, log strings, the request
payload, and the synchronous body differ. This base class captures the common
lifecycle so the concrete schedulers only override the differing hooks.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class BaseInProcessScheduler(ABC):
    """Run a periodic job on a fixed interval via an in-process asyncio loop."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Hooks (subclass-provided)
    # ------------------------------------------------------------------
    @abstractmethod
    def is_enabled(self) -> bool:
        """Return whether the periodic job is enabled."""

    @abstractmethod
    def _task_name(self) -> str:
        """Return the ``asyncio.create_task`` name for the run loop."""

    @abstractmethod
    def _interval_seconds(self) -> int:
        """Return the loop sleep interval in seconds.

        Implementations preserve their original configuration source and
        coercion exactly (e.g. ``max(1, int(...)) * 60``).
        """

    @abstractmethod
    def _run_on_startup(self) -> bool:
        """Return whether to run one cycle immediately before the sleep loop."""

    @abstractmethod
    def _enabled_log_message(self) -> str:
        """Return the printf-style message logged when enabled but not in-process.

        The single ``%s`` argument is ``settings.CELERY_BROKER_URL``.
        """

    @abstractmethod
    def _started_log_message(self) -> str:
        """Return the printf-style message logged when the loop starts.

        The single ``%s`` argument is the configured interval in minutes (the
        raw setting value, preserved per scheduler).
        """

    @abstractmethod
    def _started_log_interval(self) -> Any:
        """Return the interval value substituted into the started-log message."""

    @abstractmethod
    def build_payload(self) -> Any:
        """Build the request payload passed to :meth:`_run_once_sync`."""

    @abstractmethod
    def _run_once_sync(self, payload: Any) -> None:
        """Execute one scheduled cycle in a plain synchronous DB session."""

    # ------------------------------------------------------------------
    # Shared lifecycle
    # ------------------------------------------------------------------
    def should_run_inprocess(self) -> bool:
        """Use an in-process loop when the broker is in-memory and cannot back Celery beat."""
        return self.is_enabled() and settings.uses_in_memory_celery

    async def start(self) -> None:
        """Start the in-process scheduler when the current runtime mode requires it."""
        if not self.should_run_inprocess():
            if self.is_enabled():
                logger.info(
                    self._enabled_log_message(),
                    settings.CELERY_BROKER_URL,
                )
            return

        if self._task and not self._task.done():
            return

        self._task = asyncio.create_task(self._run_loop(), name=self._task_name())
        logger.info(
            self._started_log_message(),
            self._started_log_interval(),
        )

    async def stop(self) -> None:
        """Stop the in-process scheduler cleanly during app shutdown."""
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        interval_seconds = self._interval_seconds()

        if self._run_on_startup():
            await self._run_once()

        while True:
            await asyncio.sleep(interval_seconds)
            await self._run_once()

    async def _run_once(self) -> None:
        payload = self.build_payload()
        await asyncio.to_thread(self._run_once_sync, payload)
