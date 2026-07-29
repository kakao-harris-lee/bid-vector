"""Process-local single-flight + short-TTL cache for the strategy preview read.

``preview_candidates`` runs inline ML analysis (price prediction + pgvector
similarity) for every surviving candidate, which costs tens of seconds. A
browser reload or an impatient re-click abandons the HTTP response but **not**
the server-side work, so every duplicate click stacks another full scan onto the
same CPU and every in-flight scan gets slower — the stampede observed live on
``/dashboard/strategy``.

Two mechanisms, both deliberately best-effort:

* **single-flight** — at most one computation per key is in flight; concurrent
  callers wait for it and share its result instead of starting their own scan.
* **short TTL** — a stored result is reused for ``ttl_seconds`` so an immediate
  reload/re-click returns instantly. ``ttl_seconds <= 0`` disables reuse while
  keeping single-flight.

State is **process-local**: with several API workers each worker keeps its own
cache, and nothing is shared with celery workers. That is intentional — this is
a CPU-stampede damper, not a coherence mechanism. Correctness never depends on a
hit; a miss simply recomputes. Freshness after an operator edits the strategy is
handled explicitly by :meth:`PreviewResultCache.invalidate`.

Callers receive their own deep copy of the payload, because the API layer
mutates the preview response (it adds operator-context fields), and a waiter may
be handed a payload another request computed.
"""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

# Sentinel for "this operator was never invalidated"; monotonic clocks are
# always greater, so the comparison in _store stays total.
_NEVER = float("-inf")


@dataclass(frozen=True)
class PreviewCacheKey:
    """Everything that changes a preview payload: who asked, with which knobs.

    The two knobs are the *resolved* runtime options (after strategy defaults
    have been applied), so two requests that resolve to the same scan share an
    entry even when one of them left the query parameters out.
    """

    operator_id: int
    limit: int
    high_priority_only: bool


@dataclass
class _CacheEntry:
    """A stored preview payload plus the clock reading when it was stored."""

    payload: dict
    stored_at: float


class PreviewResultCache:
    """Single-flight + TTL cache keyed by :class:`PreviewCacheKey`."""

    def __init__(self) -> None:
        # Guards the maps below. Held only for dict operations, never across a
        # computation, so a slow preview cannot block an unrelated key.
        self._state_lock = threading.Lock()
        self._entries: dict[PreviewCacheKey, _CacheEntry] = {}
        self._compute_locks: dict[PreviewCacheKey, threading.Lock] = {}
        self._invalidated_at: dict[int, float] = {}

    def get_or_compute(
        self,
        key: PreviewCacheKey,
        compute: Callable[[], dict],
        ttl_seconds: float,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> dict:
        """Return a cached payload, an in-flight one, or the result of ``compute``.

        ``compute`` runs at most once per key at a time. A caller that finds a
        computation already in flight waits for it and shares its payload — even
        with the TTL disabled — which is the whole point of the guard: a reload
        must not start a second scan. Exceptions from ``compute`` propagate to
        every waiter's own retry and are never cached.
        """
        cached = self._read_fresh(key, ttl_seconds=ttl_seconds, now=now)
        if cached is not None:
            return cached

        compute_lock = self._compute_lock(key)
        waited_from = now()
        entered_without_waiting = compute_lock.acquire(blocking=False)
        if not entered_without_waiting:
            compute_lock.acquire()
        try:
            if not entered_without_waiting:
                shared = self._read_stored_since(key, waited_from)
                if shared is not None:
                    return shared
            cached = self._read_fresh(key, ttl_seconds=ttl_seconds, now=now)
            if cached is not None:
                return cached

            started_at = now()
            payload = compute()
            self._store(key, payload, started_at=started_at, ttl_seconds=ttl_seconds, now=now)
            return payload
        finally:
            compute_lock.release()

    def entry_count(self) -> int:
        """Number of stored payloads (memory-growth assertion surface for tests)."""
        with self._state_lock:
            return len(self._entries)

    def invalidate(
        self,
        operator_id: int,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Drop every cached preview for one operator (call after a strategy edit).

        The invalidation instant is remembered so a scan that is *already* in
        flight — started before the edit and therefore blind to it — is still
        returned to its own caller but never stored for later readers.
        """
        target = int(operator_id)
        with self._state_lock:
            self._invalidated_at[target] = now()
            for key in [key for key in self._entries if key.operator_id == target]:
                del self._entries[key]

    def clear(self) -> None:
        """Forget every entry (used by tests; the cache is process-local state).

        Per-key compute locks are intentionally kept: one may be held by an
        in-flight computation, and dropping it would let a second caller compute
        the same key concurrently. The set of keys is small and bounded.
        """
        with self._state_lock:
            self._entries.clear()
            self._invalidated_at.clear()

    def _read_fresh(
        self,
        key: PreviewCacheKey,
        *,
        ttl_seconds: float,
        now: Callable[[], float],
    ) -> dict | None:
        """Return a copy of the entry when it is still inside the TTL."""
        if ttl_seconds <= 0:
            return None
        with self._state_lock:
            entry = self._entries.get(key)
            if entry is None or now() - entry.stored_at > ttl_seconds:
                return None
            return deepcopy(entry.payload)

    def _read_stored_since(self, key: PreviewCacheKey, waited_from: float) -> dict | None:
        """Return the payload produced by the computation this caller waited on.

        Only used by a caller that actually blocked on the per-key lock: an
        entry stored at or after the moment it started waiting can only have
        come from the computation it was waiting for, so it is shared regardless
        of the TTL.
        """
        with self._state_lock:
            entry = self._entries.get(key)
            if entry is None or entry.stored_at < waited_from:
                return None
            return deepcopy(entry.payload)

    def _store(
        self,
        key: PreviewCacheKey,
        payload: dict,
        *,
        started_at: float,
        ttl_seconds: float,
        now: Callable[[], float],
    ) -> None:
        """Store a copy of ``payload`` unless the operator was invalidated mid-flight."""
        with self._state_lock:
            if self._invalidated_at.get(key.operator_id, _NEVER) >= started_at:
                return
            stored_at = now()
            self._entries[key] = _CacheEntry(payload=deepcopy(payload), stored_at=stored_at)
            self._drop_unusable_entries(before=stored_at - ttl_seconds)

    def _drop_unusable_entries(self, *, before: float) -> None:
        """Evict entries too old to ever be served again (caller holds the state lock).

        Keys are per operator x limit x priority flag, so a client walking the
        ``limit`` range would otherwise leave a payload behind for every value it
        tried. An entry older than the TTL can only be recomputed, never served —
        the single-flight share path looks solely at entries stored *after* a
        waiter started waiting — so dropping it is free.
        """
        for key in [key for key, entry in self._entries.items() if entry.stored_at < before]:
            del self._entries[key]

    def _compute_lock(self, key: PreviewCacheKey) -> threading.Lock:
        with self._state_lock:
            return self._compute_locks.setdefault(key, threading.Lock())


# Process-local singleton used by the preview path and its invalidation hook.
preview_cache = PreviewResultCache()
