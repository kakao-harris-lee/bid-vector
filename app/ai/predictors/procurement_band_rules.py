"""Ordered procurement band keyword rules + a single first-match interpreter.

The service/goods rate-band keyword resolvers previously inlined their Korean
keyword tuples and title-vs-body scope checks directly inside long ``if any(...)``
chains in ``historical.py``. This module lifts those keyword sets into an
*order-preserving* rule table and reduces the resolvers to one shared interpreter,
mirroring the marine gate's ``required_keywords`` OR-list pattern (CLAUDE.md
§4.5.3) — the rule ORDER is the priority, so a rule table reads as data.

Behavior must stay byte-identical: rule order == the original first-match order,
keyword strings are moved verbatim, and the title-scope distinction
(수의계약/(수의)/[수의] match the title line only, 수의시담 matches the whole text)
is expressed by each rule's ``scope`` field.

stdlib-only (no ``app`` imports) so ``historical`` can import it without a cycle.
Non-token compound conditions (goods deep-discount / narrow-control) are NOT
data-ized into keyword lists; they stay as named predicate references injected by
the caller via :func:`build_goods_band_rules`, keeping their correct order slot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional


@dataclass(frozen=True)
class BandKeywordRule:
    """One first-match rule mapping a keyword set (or predicate) to a band.

    ``keywords`` are OR-matched against the ``scope``-selected text: ``"text"``
    scans the whole (lowercased) notice, ``"title"`` scans only the first line.
    A rule may instead carry a ``predicate`` (a named non-token condition such as
    the goods deep-discount / narrow-control checks) evaluated against the whole
    text; when ``predicate`` is set the ``keywords``/``scope`` fields are unused.
    """

    band: str
    scope: Literal["text", "title"] = "text"
    keywords: tuple[str, ...] = ()
    predicate: Optional[Callable[[str], bool]] = None


def title_line(text: str) -> str:
    """Return the first line of the text (its title row), or the text itself.

    Matches the original ``splitlines()[0] if splitlines() else text`` expression
    so title-scoped rules see exactly the same span as before.
    """
    lines = text.splitlines()
    return lines[0] if lines else text


def resolve_band(
    rules: tuple[BandKeywordRule, ...],
    *,
    text: str,
    title: str,
) -> Optional[str]:
    """Return the band of the first matching rule (first-match priority)."""
    for rule in rules:
        if rule.predicate is not None:
            if rule.predicate(text):
                return rule.band
            continue
        scoped_text = title if rule.scope == "title" else text
        if any(keyword in scoped_text for keyword in rule.keywords):
            return rule.band
    return None


# ---------------------------------------------------------------------------
# Service band rules — order == original first-match priority:
#   direct (rule 1) > engineering (rule 2) > low_tail (rule 3) >
#   price_competitive (rule 4) > exception (rule 5) > high_negotiated (rule 6).
# Rule 1 is split into two adjacent same-band entries so the whole-text vs
# title-only scopes are expressed by ``scope`` without changing the result.
# ``환경영향`` intentionally appears in BOTH the engineering and price_competitive
# sets (duplicate membership preserved verbatim; both resolve to the same band).
# ---------------------------------------------------------------------------
SERVICE_BAND_RULES: tuple[BandKeywordRule, ...] = (
    # 1. explicit 수의시담 — whole-text scope.
    BandKeywordRule(
        band="service_direct_negotiated",
        scope="text",
        keywords=(
            "수의시담",
            "수의 시담",
        ),
    ),
    # 1b. 수의계약/(수의)/[수의] — title-line scope only.
    BandKeywordRule(
        band="service_direct_negotiated",
        scope="title",
        keywords=(
            "수의계약",
            "수의 계약",
            "(수의)",
            "[수의]",
        ),
    ),
    # 2. engineering competitive.
    BandKeywordRule(
        band="service_price_competitive",
        scope="text",
        keywords=(
            "해양이용협의",
            "간이해양이용협의",
            "해양환경영향",
            "환경영향",
            "사전영향조사",
            "사후영향조사",
            "사전·사후영향조사",
            "영향조사",
            "적지조사",
            "해저지형조사",
            "수심측량",
            "지형 및 수심측량",
            "실시설계",
            "기본 및 실시설계",
            "기본설계",
            "서식환경",
            "바다숲",
            "인공어초",
            "어초어장",
            "해상풍력",
            "pq 후 지명경쟁",
        ),
    ),
    # 3. low-tail competitive.
    BandKeywordRule(
        band="service_price_competitive",
        scope="text",
        keywords=(
            "건축기획",
            "정밀안전진단",
            "정밀안전점검",
            "내진성능평가",
            "석면조사",
            "성능점검",
            "시설 및 안전관리",
            "경비용역",
            "예초 용역",
            "비용분석",
        ),
    ),
    # 4. price competitive.
    BandKeywordRule(
        band="service_price_competitive",
        scope="text",
        keywords=(
            "폐기물",
            "기술지도",
            "사후환경",
            "환경영향",
            "pq",
            "가격입찰",
            "설계용역",
            "감리",
            "건설사업관리",
            "처리용역",
            "재활용",
        ),
    ),
    # 5. exception competitive.
    BandKeywordRule(
        band="service_price_competitive",
        scope="text",
        keywords=(
            "2단계",
            "규격·가격",
            "규격 가격",
            "규격가격",
            "가격분리",
            "가격 분리",
            "동시입찰",
            "수학여행",
            "현장체험학습",
            "해외문화체험",
            "버스",
            "차량임차",
            "차량 임차",
            "임차용역",
            "보험",
            "자동차보험",
            "재산종합보험",
        ),
    ),
    # 6. high negotiated.
    BandKeywordRule(
        band="service_high_negotiated",
        scope="text",
        keywords=(
            "협상에 의한 계약",
            "협상",
            "제안",
            "위탁",
            "운영",
            "홍보",
            "영상",
            "콘텐츠",
            "시스템",
            "개발",
            "출판",
            "제작",
            "마스터플랜",
            "플랫폼",
            "sns",
            "서포터",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Goods band rules — the two leading slots are non-token compound predicates
# (deep-discount, narrow-control) kept as named references; the trailing slot is
# the verbatim goods price-competitive keyword set. ``2단계`` intentionally lives
# both here and in the service exception set (cross-category duplicate preserved).
# ---------------------------------------------------------------------------
GOODS_PRICE_COMPETITIVE_KEYWORDS: tuple[str, ...] = (
    "소액수의 견적",
    "견적 제출",
    "견적제출",
    "2단계",
    "규격·가격",
    "규격 가격",
    "규격가격",
    "가격분리",
    "가격 분리",
    "동시입찰",
    "국내도서",
    "도서 구매",
    "운영장비 구입",
    "데이터로거 구매",
    "모니터 확장",
    "상토 구입",
    "원액주입설비",
    "인명구조함",
    "주방기구",
)


def build_goods_band_rules(
    *,
    deep_discount_predicate: Callable[[str], bool],
    narrow_control_predicate: Callable[[str], bool],
) -> tuple[BandKeywordRule, ...]:
    """Assemble the goods rule table with caller-owned predicates in order.

    The predicates live where they are defined (``historical``) so the rule table
    stays a plain reference holder and no import cycle is introduced.
    """
    return (
        BandKeywordRule(band="goods_deep_discount", predicate=deep_discount_predicate),
        BandKeywordRule(band="goods_price_competitive", predicate=narrow_control_predicate),
        BandKeywordRule(
            band="goods_price_competitive",
            scope="text",
            keywords=GOODS_PRICE_COMPETITIVE_KEYWORDS,
        ),
    )
