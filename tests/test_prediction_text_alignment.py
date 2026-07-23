"""Characterization + regression tests for the shared predictor-input text
assembler (``build_prediction_text``) and the live-path title alignment.

WHY THESE MATTER
----------------
The price predictor reads regulatory mechanism keywords (2단계/가격입찰/협상/수의)
directly out of its ``description`` text (``resolve_procurement_rate_band`` → price
band, ``_detect_price_regime_signals`` → regime label). Those cues frequently live
in the notice TITLE, so the assembled text is a load-bearing predictor input.

This file locks three things:
  1. the pure assembly contract (order, single-space separator, None/empty drop);
  2. that the helper reproduces the EXACT string the backtest/smoke paths joined
     inline before the extraction (byte-identity → their outputs stay unchanged);
  3. that the live opportunity path now feeds the title into the predictor text
     (the asymmetry fix — CLAUDE.md §8: no predictor-input change without a test).
"""

import pytest

from app.core.single_user import ensure_operator_account
from app.models.models import Project
from app.schemas.schemas import OpportunityAnalysisRequest
from app.services.bid_base import build_prediction_text
from app.services.opportunity_analysis import OpportunityAnalysisService


def _project(title=None, description=None, requirements=None, **kwargs) -> Project:
    return Project(
        title=title, description=description, requirements=requirements, **kwargs
    )


# --------------------------------------------------------------------------- #
# 1. Pure assembly contract
# --------------------------------------------------------------------------- #


def test_build_prediction_text_order_and_single_space():
    """title → description → requirements, joined by a single space."""
    assert build_prediction_text(_project("T", "D", "R")) == "T D R"


@pytest.mark.parametrize(
    "title, description, requirements, expected",
    [
        ("T", None, "R", "T R"),  # missing description dropped
        (None, "D", "R", "D R"),  # missing title dropped
        ("T", "D", None, "T D"),  # missing requirements dropped
        ("T", "", "R", "T R"),  # empty-string field dropped (not "T  R")
        (None, None, None, ""),  # all missing → empty
        ("", "", "", ""),  # all empty → empty
    ],
)
def test_build_prediction_text_drops_none_and_empty(
    title, description, requirements, expected
):
    assert build_prediction_text(_project(title, description, requirements)) == expected


def test_build_prediction_text_carries_title_regulatory_keyword():
    """The whole point of the alignment: a 2-stage bidding cue in the title reaches
    the predictor text (drives the price band / regime detection)."""
    text = build_prediction_text(
        _project("수학여행 위탁용역 2단계(규격·가격 동시) 입찰", "본문", "")
    )
    assert "2단계" in text
    assert "규격·가격" in text


# --------------------------------------------------------------------------- #
# 2. Byte-identity: the helper reproduces the exact prior backtest/smoke joins.
#    If a future edit changes the join shape, these fail loudly instead of
#    silently re-diverging the three predict paths.
# --------------------------------------------------------------------------- #


def _old_backtest_join(p: Project) -> str:
    # paper_bidding_backtest.py (pre-helper) inline join.
    return " ".join(
        part for part in [p.title, p.description, p.requirements] if part
    )


def _old_smoke_join(p: Project) -> str:
    # smoke_test.py (pre-helper) inline join.
    return " ".join(
        x for x in [p.title, p.description or "", p.requirements or ""] if x
    )


@pytest.mark.parametrize(
    "title, description, requirements",
    [
        ("T", "D", "R"),
        ("T", None, "R"),
        (None, "D", None),
        ("T", "", ""),
        (None, None, None),
        ("2단계 규격·가격 동시", "적격심사 대상", "가격입찰"),
    ],
)
def test_helper_matches_prior_backtest_and_smoke_joins(title, description, requirements):
    p = _project(title, description, requirements)
    assert build_prediction_text(p) == _old_backtest_join(p)
    assert build_prediction_text(p) == _old_smoke_join(p)


# --------------------------------------------------------------------------- #
# 3. Live-path regression: the opportunity (monitor/telegram) predict path now
#    feeds title+description+requirements into the predictor, not just body.
# --------------------------------------------------------------------------- #


class _RecordingPricePredictionPort:
    """Duck-typed price port that records the description it was called with and
    returns a minimal payload (no agency band → no menu branch)."""

    def __init__(self) -> None:
        self.captured_description = None
        self.call_count = 0

    def predict_price(self, **kwargs):
        self.call_count += 1
        self.captured_description = kwargs.get("description")
        return {"floor_from_agency": None, "ceiling_from_agency": None}


def test_live_opportunity_path_feeds_title_into_predictor(test_db):
    """opportunity_analysis._build_price_prediction assembles the predictor input
    via build_prediction_text, so the title (holding the regulatory cue) is no
    longer dropped — the live path now matches the backtest/smoke/holdout input."""
    operator = ensure_operator_account(test_db)
    project = Project(
        title="한강대교 성능평가 용역 2단계(규격·가격 동시)",
        description="본문 설명",
        requirements="자격 요건",
        budget_estimate=50_000_000.0,
        category="service",
    )
    test_db.add(project)
    test_db.commit()

    port = _RecordingPricePredictionPort()
    service = OpportunityAnalysisService(price_prediction_port=port)
    request = OpportunityAnalysisRequest(project_id=project.id)

    service._build_price_prediction(
        test_db, project=project, request=request, operator_id=operator.id
    )

    assert port.call_count == 1
    # Title + description + requirements all reach the predictor input, in order.
    assert (
        port.captured_description
        == "한강대교 성능평가 용역 2단계(규격·가격 동시) 본문 설명 자격 요건"
    )
    # The regulatory cue that used to be dropped (title-only) now reaches the model.
    assert "2단계" in port.captured_description
    assert "한강대교" in port.captured_description
