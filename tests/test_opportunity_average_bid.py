"""Characterization: ``_build_user_historical_data`` average_bid via ``domain.average``.

#266 unified the arithmetic mean into ``app.domain.aggregates.average``. These
guards lock that the migrated ``average_bid`` computation preserves the prior
inline value space (``round(sum(...) / len(...), 2)``), including the rounding
boundary, and that the ``if bids:`` guard keeps the empty-history branch off the
``average`` None path (so ``average_bid`` is simply absent, never ``None``).

The target method reads only ``db`` (no ``self`` state), so it is exercised on an
instance built via ``__new__`` to avoid the heavy port/model construction in
``__init__``, driven by a fake session double.
"""

from __future__ import annotations

from app.services.opportunity_analysis import OpportunityAnalysisService


class _FakeBid:
    def __init__(self, bid_amount: float | None, status: str = "submitted") -> None:
        self.bid_amount = bid_amount
        self.status = status


class _FakeQuery:
    def __init__(self, bids: list[_FakeBid]) -> None:
        self._bids = bids

    def filter(self, *args, **kwargs):  # noqa: ANN002, ANN003 - test double
        return self

    def all(self) -> list[_FakeBid]:
        return self._bids


class _FakeDBSession:
    def __init__(self, bids: list[_FakeBid]) -> None:
        self._bids = bids

    def query(self, *args, **kwargs):  # noqa: ANN002, ANN003 - test double
        return _FakeQuery(self._bids)


def _service() -> OpportunityAnalysisService:
    # Bypass __init__ (heavy port construction); the target method uses only db.
    return OpportunityAnalysisService.__new__(OpportunityAnalysisService)


def _build(bids: list[_FakeBid], request_data: dict | None = None) -> dict:
    return _service()._build_user_historical_data(
        _FakeDBSession(bids), operator_id=1, request_data=request_data
    )


def test_average_bid_matches_inline_round_sum_len():
    """Representative multi-value mean rounds to 2 digits exactly as before."""
    payload = _build([_FakeBid(100.0), _FakeBid(200.0), _FakeBid(301.0)])
    # round((100 + 200 + 301) / 3, 2) == round(200.333..., 2) == 200.33
    assert payload["average_bid"] == 200.33
    assert payload["bid_count"] == 3


def test_average_bid_preserves_rounding_boundary():
    """A mean whose 3rd decimal is on a ``.xx5`` boundary stays bit-identical."""
    amounts = [1.0, 1.005]
    bids = [_FakeBid(amount) for amount in amounts]
    payload = _build(bids)
    assert payload["average_bid"] == round(sum(amounts) / len(amounts), 2)


def test_empty_history_leaves_average_bid_absent():
    """The ``if bids:`` guard keeps the empty branch off ``average``'s None path."""
    payload = _build([])
    assert "average_bid" not in payload
    assert payload == {}
