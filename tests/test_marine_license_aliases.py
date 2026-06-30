"""License-axis tests for the 해양엔지니어링협회 게이트 (Phase 1) aliases.

Adds three marine engineering / technical-service license groups to
``NoticeClassifierService.LICENSE_ALIASES`` so a 해양 firm whose
``license_codes`` hold Korean license names normalize to canonical codes:

    PORT001   ← 항만및해안 / 항만및해안기술사 / 항만설계 / 해안기술사
    MAR001    ← 해양엔지니어링 / 해양기술사
    HYDRO001  ← 수로조사 / 수로측량 / 해양조사

These are technical-service (설계·감리·조사·측량) licenses, not construction
(시공) licenses. ``_extract_license_tokens`` needs no DB session, so these are
plain unit tests built from in-memory text / ``CompanyProfile`` instances.
"""

from __future__ import annotations

import pytest

from app.core.single_user import join_multi_value_text
from app.models.models import CompanyProfile
from app.services.classifier import NoticeClassifierService


# ---------------------------------------------------------------------------
# Notice-side extraction (require_context=True) — canonical-code mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("항만및해안 면허 필요", {"PORT001"}),
        ("항만설계 면허 필요", {"PORT001"}),
        ("수로조사 면허 필요", {"HYDRO001"}),
        ("수로측량 면허 필요", {"HYDRO001"}),
        ("해양조사 면허 필요", {"HYDRO001"}),
    ],
)
def test_extract_marine_license_tokens(text, expected):
    """Marine names WITHOUT '기술사'/'엔지니어링' map cleanly to a single code."""
    service = NoticeClassifierService()
    assert service._extract_license_tokens(text, require_context=True) == expected


@pytest.mark.parametrize(
    "text, marine_code",
    [
        ("항만및해안기술사 면허 필요", "PORT001"),
        ("해안기술사 면허 필요", "PORT001"),
        ("해양기술사 면허 필요", "MAR001"),
    ],
)
def test_marine_engineer_names_cofire_eng001(text, marine_code):
    """'…기술사' marine names also fire ENG001 via the legacy '기술사' alias.

    Intended: a 기술사 credential IS an engineering credential, mirroring the
    '해양엔지니어링' → ENG001+MAR001 behaviour. The marine code is still present.
    """
    service = NoticeClassifierService()
    tokens = service._extract_license_tokens(text, require_context=True)
    assert tokens == {marine_code, "ENG001"}


def test_marine_engineering_matches_both_eng_and_mar():
    """'해양엔지니어링' intentionally maps to BOTH ENG001 and MAR001.

    A 해양엔지니어링 firm is an 엔지니어링 firm too; '엔지니어링' is a substring of
    '해양엔지니어링' so the legacy ENG001 alias also fires. Documented behaviour.
    """
    service = NoticeClassifierService()
    assert service._extract_license_tokens("해양엔지니어링 면허 필요", require_context=True) == {
        "ENG001",
        "MAR001",
    }


def test_bare_marine_terms_do_not_match_licenses():
    """Bare '해양'/'항만'/'측량' must NOT be aliases (over-match guard)."""
    service = NoticeClassifierService()
    for bare in ("해양 면허", "항만 면허", "측량 면허", "해안 면허"):
        assert service._extract_license_tokens(bare, require_context=True) == set()


# ---------------------------------------------------------------------------
# Profile-side extraction (require_context=False) — what the seed script stores
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "license_name, expected_code",
    [
        ("해양조사", "HYDRO001"),
        ("항만및해안기술사", "PORT001"),
        ("수로측량", "HYDRO001"),
        ("해양기술사", "MAR001"),
    ],
)
def test_profile_marine_license_normalizes(license_name, expected_code):
    service = NoticeClassifierService()
    profile = CompanyProfile(
        business_type="technical-service",
        license_codes=license_name,
        region_codes="전국",
    )
    tokens = service._extract_license_tokens(profile.license_codes)
    assert expected_code in tokens


def test_seed_license_codes_normalize_to_four_marine_groups():
    """The exact license_codes the gate stores extract ENG001/PORT001/MAR001/HYDRO001."""
    service = NoticeClassifierService()
    stored = join_multi_value_text(
        ["엔지니어링", "항만및해안", "해양엔지니어링", "수로조사"]
    )
    assert service._extract_license_tokens(stored) == {
        "ENG001",
        "PORT001",
        "MAR001",
        "HYDRO001",
    }


# ---------------------------------------------------------------------------
# Regression: legacy construction/engineering aliases unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "license_name, canonical",
    [
        ("엔지니어링", "ENG001"),
        ("건축공사업", "ARC001"),
        ("전기공사업", "ELE001"),
    ],
)
def test_legacy_aliases_unaffected_by_marine_additions(license_name, canonical):
    service = NoticeClassifierService()
    assert service._extract_license_tokens(f"{license_name} 면허 필요", require_context=True) == {
        canonical
    }
