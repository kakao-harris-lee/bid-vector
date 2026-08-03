"""Process-safe, bounded cache for normalized released predictor artifacts."""

from __future__ import annotations

import os
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Generic, TypeVar
from weakref import ref

from pydantic import JsonValue

ArtifactSource = str | Path | dict[str, JsonValue]

_ARTIFACT_STABLE_LOAD_ATTEMPTS = 3
_DEFAULT_ARTIFACT_CACHE_ENTRIES = 8

_ArtifactValueT = TypeVar("_ArtifactValueT")


@dataclass(frozen=True)
class _ArtifactFileIdentity:
    """Filesystem identity fields that change when a released file changes."""

    resolved_path: str
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


def _artifact_file_identity(path: Path) -> _ArtifactFileIdentity:
    """Return a cheap identity for cache lookup and replacement detection."""
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    return _ArtifactFileIdentity(
        resolved_path=str(resolved),
        device=int(stat.st_dev),
        inode=int(stat.st_ino),
        size=int(stat.st_size),
        modified_ns=int(stat.st_mtime_ns),
        changed_ns=int(stat.st_ctime_ns),
    )


class _ArtifactProcessState(Generic[_ArtifactValueT]):
    """Cache and synchronization primitives owned by exactly one process."""

    def __init__(self, process_id: int) -> None:
        self.process_id = process_id
        self.entries: OrderedDict[
            str, tuple[_ArtifactFileIdentity, _ArtifactValueT]
        ] = OrderedDict()
        self.lock = RLock()


class VersionAwareArtifactProvider(Generic[_ArtifactValueT]):
    """Cache normalized artifacts once per stable identity and process.

    An after-fork child hook replaces inherited cache and synchronization state
    before application threads can run, avoiding deadlock on a lock held by a
    vanished parent thread. Where ``register_at_fork`` is unavailable, an atomic
    process-state publication elects one child-created lock without waiting on any
    inherited synchronization object. The internal cached value is never returned
    directly: callers receive detached copies so mutable dictionaries and numpy
    arrays cannot poison later predictions. LRU eviction keeps the number of
    cached releases bounded.
    """

    def __init__(
        self,
        loader: Callable[[ArtifactSource], _ArtifactValueT],
        *,
        max_entries: int = _DEFAULT_ARTIFACT_CACHE_ENTRIES,
    ) -> None:
        self._loader = loader
        self._max_entries = max(1, int(max_entries))
        process_id = os.getpid()
        self._state = _ArtifactProcessState[_ArtifactValueT](process_id)
        self._fallback_states_by_pid = {process_id: self._state}
        self._process_transition_lock = RLock()
        self._fork_hook_registered = self._register_after_fork_hook()

    def load(self, model_source: ArtifactSource) -> _ArtifactValueT:
        """Load one artifact, reusing only an unchanged path-backed version."""
        if isinstance(model_source, dict):
            return self._loader(model_source)

        path = Path(model_source)
        state = self._state_for_current_process()
        with state.lock:
            try:
                identity = _artifact_file_identity(path)
            except FileNotFoundError:
                # Never serve the last good version after a release path
                # disappears. The loader preserves its label-specific error.
                state.entries.pop(str(path.resolve(strict=False)), None)
                return self._loader(model_source)
            cached = state.entries.get(identity.resolved_path)
            if cached is not None and cached[0] == identity:
                state.entries.move_to_end(identity.resolved_path)
                return deepcopy(cached[1])

            # Invalidate before loading so an invalid replacement cannot fall
            # back to stale normalized data.
            state.entries.pop(identity.resolved_path, None)
            loaded, stable_identity = self._load_stable(model_source, path)
            state.entries[stable_identity.resolved_path] = (
                stable_identity,
                loaded,
            )
            state.entries.move_to_end(stable_identity.resolved_path)
            while len(state.entries) > self._max_entries:
                state.entries.popitem(last=False)
            return deepcopy(loaded)

    def clear(self) -> None:
        """Drop cached values, primarily for explicit lifecycle/test control."""
        state = self._state_for_current_process()
        with state.lock:
            state.entries.clear()

    def _state_for_current_process(
        self,
    ) -> _ArtifactProcessState[_ArtifactValueT]:
        """Return state that cannot contain a lock inherited from another PID."""
        process_id = os.getpid()
        state = self._state
        if state.process_id == process_id:
            return state
        if not self._fork_hook_registered:
            return self._publish_hookless_process_state(process_id)
        with self._process_transition_lock:
            state = self._state
            if state.process_id != process_id:
                state = _ArtifactProcessState[_ArtifactValueT](process_id)
                self._state = state
        return state

    def _publish_hookless_process_state(
        self,
        process_id: int,
    ) -> _ArtifactProcessState[_ArtifactValueT]:
        """Elect one child-created state without touching inherited locks."""
        candidate = _ArtifactProcessState[_ArtifactValueT](process_id)
        registry = self._fallback_states_by_pid
        state = registry.setdefault(process_id, candidate)
        # ``dict.setdefault`` is the atomic publication point. Every contender
        # receives the same winner, whose fresh lock serializes the first load.
        self._fallback_states_by_pid = {process_id: state}
        self._state = state
        return state

    def _register_after_fork_hook(self) -> bool:
        """Reset inherited locks in fork children without retaining this provider."""
        register_at_fork = getattr(os, "register_at_fork", None)
        if not callable(register_at_fork):
            return False
        provider_ref = ref(self)

        def _reset_provider_in_child() -> None:
            provider = provider_ref()
            if provider is not None:
                provider._reset_after_fork()

        register_at_fork(after_in_child=_reset_provider_in_child)
        return True

    def _reset_after_fork(self) -> None:
        """Install entirely child-local state without touching inherited locks."""
        process_id = os.getpid()
        state = _ArtifactProcessState[_ArtifactValueT](process_id)
        self._process_transition_lock = RLock()
        self._fallback_states_by_pid = {process_id: state}
        self._state = state

    def _load_stable(
        self,
        model_source: str | Path,
        path: Path,
    ) -> tuple[_ArtifactValueT, _ArtifactFileIdentity]:
        for _ in range(_ARTIFACT_STABLE_LOAD_ATTEMPTS):
            before = _artifact_file_identity(path)
            loaded = self._loader(model_source)
            after = _artifact_file_identity(path)
            if before == after:
                return loaded, after
        raise ValueError(f"Artifact changed while it was being loaded: {path}")
