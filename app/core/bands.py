"""Declarative band-ladder resolution shared across scoring services.

A "band ladder" maps a scalar (budget, deadline hours, a bid rate, ...) to a
payload by walking an ordered list of ``(threshold, payload)`` rungs and
returning the first rung the value satisfies. Two shapes recur in this codebase:

* descending ``>=`` ladders — e.g. budget: ``>=500M`` -> ..., ``>=200M`` -> ...
* ascending ``<=`` ladders — e.g. deadline hours: ``<=6`` -> ..., ``<=24`` -> ...

Both are the same first-match walk; only the comparison differs. Collapsing the
open-coded ``if/elif`` cascades into ONE resolver removes the off-by-one risk of
re-deriving each boundary by hand and lets the rungs live as declarative data.

The final rung is the fallback: give it a sentinel threshold that always matches
(``float("-inf")`` for a ``>=`` ladder, ``float("inf")`` for a ``<=`` ladder) so
a payload is always returned.

stdlib-only and payload-generic, so other band-based scorers (classifier bands,
rate bands, ...) can reuse it without importing app internals.
"""

from __future__ import annotations

import operator
from typing import Callable, TypeVar

T = TypeVar("T")


def resolve_band(
    value: float,
    bands: tuple[tuple[float, T], ...],
    *,
    compare: Callable[[float, float], bool] = operator.ge,
) -> T:
    """Return the payload of the first rung whose threshold ``value`` satisfies.

    ``bands`` is walked in order; the first rung where ``compare(value, threshold)``
    is True wins (first-match). ``compare`` defaults to ``operator.ge`` (descending
    ``>=`` ladder); pass ``operator.le`` for an ascending ``<=`` ladder — the
    comparison is pluggable precisely so a ``>=`` and a ``<=`` ladder share one
    resolver with identical boundary semantics (no ``>`` vs ``>=`` drift).

    The last rung should carry a sentinel threshold that always matches, acting
    as the fallback. If nothing matches (an empty or malformed ladder) the last
    rung's payload is returned as a safety net.
    """
    for threshold, payload in bands:
        if compare(value, threshold):
            return payload
    return bands[-1][1]
