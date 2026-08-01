"""Characterization of the persisted strengths/risk_flags extraction.

Two independent readers parse the same ``score_breakdown`` blob:
``app.schemas.opportunity._extract_decision_reasons`` (response schema path) and
``BidSummaryService._extract_decision_reasons`` (summary service path). They wrap
different JSON-restore paths but share the string-list coercion, so this table
pins the behavior both must keep — non-string items and empty strings drop out,
and a non-list value short circuits to an empty list.
"""

from __future__ import annotations

import pytest

from app.schemas.opportunity import _extract_decision_reasons as schema_extract
from app.services.bid_summary import BidSummaryService

_service_extract = BidSummaryService()._extract_decision_reasons

EXTRACTORS = (schema_extract, _service_extract)

# (score_breakdown, expected strengths, expected risk_flags)
CASES = [
    (None, [], []),
    ("", [], []),
    ("not json", [], []),
    ("[1, 2]", [], []),
    ("42", [], []),
    ({}, [], []),
    ([1, 2], [], []),
    ({"score": 0.7}, [], []),
    (
        {"strengths": ["실적 충족"], "risk_flags": ["공기 촉박"]},
        ["실적 충족"],
        ["공기 촉박"],
    ),
    (
        '{"strengths": ["실적 충족"], "risk_flags": ["공기 촉박"]}',
        ["실적 충족"],
        ["공기 촉박"],
    ),
    ({"strengths": [], "risk_flags": []}, [], []),
    # 비문자열 항목과 빈 문자열은 탈락한다.
    ({"strengths": ["a", 1, None, "", "b", True]}, ["a", "b"], []),
    # 필터는 빈 문자열만 떨어뜨린다. "0" 은 non-empty 라 남고 숫자 0 은 str 이 아니라 탈락.
    ({"strengths": ["0", 0]}, ["0"], []),
    # 공백 문자열은 truthy 라 그대로 남는다.
    ({"risk_flags": ["  "]}, [], ["  "]),
    # 리스트가 아니면 통째로 빈 목록.
    ({"strengths": "abc", "risk_flags": {"a": 1}}, [], []),
    ({"strengths": None, "risk_flags": None}, [], []),
]


@pytest.mark.parametrize("extract", EXTRACTORS, ids=["schema", "bid_summary"])
@pytest.mark.parametrize("blob, strengths, risk_flags", CASES)
def test_extract_decision_reasons_contract(extract, blob, strengths, risk_flags):
    assert extract(blob) == (strengths, risk_flags)


@pytest.mark.parametrize("extract", EXTRACTORS, ids=["schema", "bid_summary"])
def test_extract_decision_reasons_returns_new_lists(extract):
    original = ["a"]
    result_strengths, _ = extract({"strengths": original})
    assert result_strengths == original
    assert result_strengths is not original
