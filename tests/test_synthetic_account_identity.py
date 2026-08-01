"""Characterization tests for synthetic account identity strings.

The address of a synthetic account is its username at the synthetic domain.
That rule was implemented twice — ``_email_for`` (custom companies, prefix
``synthetic-custom-``) and ``_email`` (preset archetypes seeded by
``scripts/seed_synthetic_operators.py``, prefix ``synthetic-``) — each pasting
its own prefix next to a locally declared domain constant.

The addresses below are what both copies produced; they are pinned because the
``synthetic-`` prefix is the predicate that keeps synthetic companies out of
the canonical operator workflow, and the stored ``User.email`` is what the
seeding path upserts on.
"""

from __future__ import annotations

import pytest

from app.services.synthetic_custom_operator import (
    _username_for,
    synthetic_email,
)
from scripts.seed_synthetic_operators import _username

SLUGS = ["lab-alpha", "marine", "해양엔지니어링", "a", "ops-9"]


@pytest.mark.parametrize("bare_slug", SLUGS)
def test_custom_company_address(bare_slug):
    assert (
        synthetic_email(_username_for(bare_slug))
        == f"synthetic-custom-{bare_slug}@synthetic.bid-vector.local"
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_preset_archetype_address(slug):
    assert (
        synthetic_email(_username(slug))
        == f"synthetic-{slug}@synthetic.bid-vector.local"
    )


@pytest.mark.parametrize("slug", SLUGS)
def test_address_local_part_is_exactly_the_username(slug):
    """username 과 email 은 갈라질 수 없다 — 주소 로컬파트가 곧 username 이다."""
    for username in (_username(slug), _username_for(slug)):
        assert synthetic_email(username).split("@")[0] == username
