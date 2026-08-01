"""Characterization tests for agency-name normalization.

``normalize_agency_name`` existed twice — in the historical predictor helpers
(``app/ai/predictors/historical/statistics.py``) and in the KONEPS parsing
primitives (``app/services/koneps/parsing.py``) — with byte-identical bodies.
The copies are consolidated onto the KONEPS parsing canonical.

The normalized token is not cosmetic: it is the **lookup key of the
agency-keyed guardrail band** (``PREDICTION_AGENCY_MINIMUM_BID_RATES`` /
``…_MAXIMUM_BID_RATES`` resolved through
:func:`app.domain.basis_conversion.resolve_agency_bid_rate`), so a change in
the normalized string moves recommended bid rates. The table below pins the
exact output of every whitespace / case / falsy-input shape that the two copies
produced, and the band cases pin the substring match that lets regional bureaus
inherit the headquarters band.
"""

from __future__ import annotations

import pytest

from app.ai.predictors.historical import normalize_agency_name
from app.core.config import settings
from app.domain.basis_conversion import resolve_agency_bid_rate
from app.services.koneps import parsing

# (raw value, normalized token)
AGENCY_CASES = [
    (None, ""),
    ("", ""),
    ("   ", ""),
    ("\t\n ", ""),
    # `str(value or "")` — falsy non-strings normalize to the empty token.
    (0, ""),
    (False, ""),
    (123, "123"),
    ("한국수산자원공단", "한국수산자원공단"),
    ("  한국수산자원공단  ", "한국수산자원공단"),
    ("한국수산자원공단 동해본부", "한국수산자원공단동해본부"),
    ("한국수산자원공단\t제주본부", "한국수산자원공단제주본부"),
    ("한국수산자원공단\n남해본부", "한국수산자원공단남해본부"),
    ("한국수산자원공단　서해본부", "한국수산자원공단서해본부"),
    ("부산지방해양수산청  부산항건설사무소", "부산지방해양수산청부산항건설사무소"),
    ("울산광역시 울주군(항만)", "울산광역시울주군(항만)"),
    ("Korea Fisheries RESOURCES Agency", "koreafisheriesresourcesagency"),
]


@pytest.mark.parametrize("raw,expected", AGENCY_CASES)
def test_predictor_path_normalization(raw, expected):
    assert normalize_agency_name(raw) == expected


@pytest.mark.parametrize("raw,expected", AGENCY_CASES)
def test_koneps_path_normalization(raw, expected):
    assert parsing.normalize_agency_name(raw) == expected


def test_shipped_agency_band_keys_are_already_normalized():
    """The configured band keys must survive normalization unchanged.

    ``resolve_agency_bid_rate`` normalizes both sides and matches by substring;
    a key that normalizes to something else would silently stop matching.
    """
    configured_keys = {
        *settings.PREDICTION_AGENCY_MINIMUM_BID_RATES,
        *settings.PREDICTION_AGENCY_MAXIMUM_BID_RATES,
    }
    for key in configured_keys:
        assert normalize_agency_name(key) == key


AGENCY_BAND_MAP = {"한국수산자원공단": 0.8806}


@pytest.mark.parametrize(
    "agency_name,expected",
    [
        ("한국수산자원공단", 0.8806),
        ("한국수산자원공단 동해본부", 0.8806),
        ("한국수산자원공단제주본부", 0.8806),
        (" 한국수산자원공단\t남해본부 ", 0.8806),
        ("한국해양과학기술원", None),
        ("", None),
        (None, None),
    ],
)
def test_agency_band_lookup_is_pinned(agency_name, expected):
    assert resolve_agency_bid_rate(agency_name, AGENCY_BAND_MAP) == expected


def test_agency_band_lookup_prefers_the_longest_matching_key():
    rate_map = {"한국수산자원공단": 0.8806, "한국수산자원공단동해본부": 0.8712}
    assert resolve_agency_bid_rate("한국수산자원공단 동해본부", rate_map) == 0.8712
