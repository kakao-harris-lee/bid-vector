"""Canonical text cleanup helpers for untyped values (stdlib-only).

These utilities centralize the previously duplicated ``_normalize_category`` /
``_clean_category_name`` / ``_clean_optional`` / ``_append_reasoning_note`` /
``_append_note`` logic that lived in ``app/services/backtest_cutoff.py``,
``app/services/prediction_dataset.py``,
``app/services/decision_experiments/application.py``,
``app/services/ml_training/helpers.py``, ``app/services/allocation.py`` and
``app/services/real_bid_track.py``. The behavior is identical to the original
implementations.

Scope boundary: value → text cleanup with **no domain knowledge**. The tables
being looked up (category aliases, 면허 별칭 …) and the records being written to
stay with their domain owners; this module only takes raw text in and hands
cleaned text back.

This module must not import any other application module (typing only) to keep
it importable from every layer, the same rule the sibling coercion hub
``app/utils/numeric.py`` follows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def clean_text(value: Any) -> str:
    """Return ``value`` as a stripped string, mapping falsy values to ``""``.

    Falsy non-strings collapse to ``""`` rather than to their ``str()`` form —
    ``clean_text(0)`` is ``""``, not ``"0"``. Every consolidated copy used the
    ``str(value or "")`` idiom, so that quirk is the contract.
    """
    return str(value or "").strip()


def optional_text(value: Any) -> str | None:
    """Return the cleaned text, or ``None`` when nothing survives the strip."""
    return clean_text(value) or None


def normalize_lookup_key(value: Any, aliases: Mapping[str, str]) -> str:
    """Casefold ``value`` into a lookup key, resolving ``aliases`` to canonical.

    Keys outside ``aliases`` pass through unchanged, so an unknown label stays
    visible to the caller instead of collapsing into a default bucket.
    """
    key = clean_text(value).lower()
    return aliases.get(key, key)


def append_unique_note(existing: str | None, note: str) -> str | None:
    """Return ``existing`` with ``note`` appended once, separated by a space.

    ``existing`` comes back identical when ``note`` is empty or already a
    substring of it, which is what makes repeated appends idempotent. Callers
    persisting the result should compare it against the current value and skip
    the write when unchanged — re-assigning an ORM attribute marks the row
    dirty even when the value did not change.
    """
    if not note:
        return existing
    if not existing:
        return note
    if note in existing:
        return existing
    return f"{existing} {note}".strip()
