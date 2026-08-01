"""Custom synthetic operator (가상 회사) CRUD service.

Phase 3 of the "가상 회사 낙찰 실험실" — lets the operator define, edit, clone,
and delete *custom* synthetic companies from the web, on top of the 12 fixed
preset archetypes seeded by :mod:`scripts.seed_synthetic_operators`.

Identity model (no new column / no migration required):

* Every synthetic company is a ``User`` (``username = synthetic-<slug>``) plus a
  ``CompanyProfile`` and an ``OperatorStrategy`` (both ``user_id`` unique).
* A **custom** company uses ``username = synthetic-custom-<slug>`` so its derived
  slug — ``user.username[len("synthetic-"):]`` (see
  :meth:`SyntheticBacktestService.list_operators`) — is ``custom-<slug>`` and
  therefore ``slug.startswith("custom-")`` is the sole ``is_custom`` predicate.
* Presets (slug WITHOUT the ``custom-`` prefix) and the canonical ``operator``
  account are protected: they can never be edited, cloned-over, or deleted here.

The upsert pattern mirrors ``scripts/seed_synthetic_operators.upsert_synthetic_operator``
but is reimplemented here so the HTTP path does not depend on the CLI script.
``CompanyProfile.user_id`` / ``OperatorStrategy.user_id`` uniqueness is honoured
(exactly one profile + one strategy row per company).
"""

from __future__ import annotations

import re
import secrets
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    DEFAULT_OPERATOR_USERNAME,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import CompanyProfile, OperatorStrategy, User

SYNTHETIC_USERNAME_PREFIX = "synthetic-"
CUSTOM_SLUG_PREFIX = "custom-"
CUSTOM_USERNAME_PREFIX = f"{SYNTHETIC_USERNAME_PREFIX}{CUSTOM_SLUG_PREFIX}"
SYNTHETIC_EMAIL_DOMAIN = "synthetic.bid-vector.local"

# Default strategy values applied when a custom company omits a field. Mirror the
# seed archetype defaults so a bare custom company behaves like a sensible preset.
DEFAULT_MINIMUM_MATCH_SCORE = 0.6
DEFAULT_MINIMUM_PROBABILITY_SCORE = 0.55
DEFAULT_MAX_RECOMMENDED_CANDIDATES = 10
DEFAULT_BUSINESS_TYPE = "service"


class CustomOperatorError(Exception):
    """Base error for custom operator operations (mapped to HTTP by the router)."""

    status_code = 400


class CustomOperatorConflictError(CustomOperatorError):
    """Slug/username already taken (HTTP 409)."""

    status_code = 409


class CustomOperatorNotFoundError(CustomOperatorError):
    """Requested custom company does not exist (HTTP 404)."""

    status_code = 404


class CustomOperatorProtectedError(CustomOperatorError):
    """Attempt to edit/delete a preset or the canonical operator (HTTP 400)."""

    status_code = 400


def is_custom_slug(slug: str) -> bool:
    """A slug belongs to a custom company iff it carries the ``custom-`` prefix."""
    return slug.startswith(CUSTOM_SLUG_PREFIX)


def _normalize_slug(raw: str) -> str:
    """Lowercase, hyphenate, strip to ``[a-z0-9-]`` for a stable username slug."""
    value = (raw or "").strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value


def _resolve_custom_slug(*, slug: Optional[str], name: str) -> str:
    """Pick the bare custom slug (without prefixes) from explicit slug or name.

    The returned value is the user-facing slug fragment; callers prepend
    ``custom-`` for the public slug and ``synthetic-custom-`` for the username.
    """
    candidate = _normalize_slug(slug) if slug else ""
    if not candidate:
        candidate = _normalize_slug(name)
    # Tolerate callers that pass an already-prefixed slug.
    if candidate.startswith(CUSTOM_SLUG_PREFIX):
        candidate = candidate[len(CUSTOM_SLUG_PREFIX) :]
    if not candidate:
        raise CustomOperatorError("slug 또는 name에서 유효한 slug를 생성할 수 없습니다.")
    return candidate


def _username_for(bare_slug: str) -> str:
    return f"{CUSTOM_USERNAME_PREFIX}{bare_slug}"


def synthetic_email(username: str) -> str:
    """Address for one synthetic account — custom companies here, preset
    archetypes in ``scripts/seed_synthetic_operators.py``. The local part is
    exactly the username, so the two can never drift apart."""
    return f"{username}@{SYNTHETIC_EMAIL_DOMAIN}"


def _public_slug_for(bare_slug: str) -> str:
    return f"{CUSTOM_SLUG_PREFIX}{bare_slug}"


def _unusable_password_hash() -> str:
    """A real bcrypt/pbkdf2 hash over a random secret never exposed anywhere.

    Synthetic accounts must never authenticate; we still store a valid hash so the
    column is non-null and the login path (constant-time compare) behaves normally.
    """
    return get_password_hash(secrets.token_urlsafe(32))


class SyntheticCustomOperatorService:
    """Create / read / update / clone / delete custom synthetic companies."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- internal lookups ------------------------------------------------------

    def _user_for_public_slug(self, public_slug: str) -> Optional[User]:
        username = f"{SYNTHETIC_USERNAME_PREFIX}{public_slug}"
        return self.db.query(User).filter(User.username == username).first()

    def _require_custom_user(self, public_slug: str) -> User:
        """Resolve a user that MUST be an editable custom company.

        Raises ``CustomOperatorProtectedError`` for the canonical operator or any
        preset (even one that exists), and ``CustomOperatorNotFoundError`` when no
        such synthetic user exists.
        """
        if public_slug == DEFAULT_OPERATOR_USERNAME or not is_custom_slug(public_slug):
            user = self._user_for_public_slug(public_slug)
            if user is not None or public_slug == DEFAULT_OPERATOR_USERNAME:
                raise CustomOperatorProtectedError("프리셋 또는 기본 운영자 회사는 편집/삭제할 수 없습니다.")
            raise CustomOperatorNotFoundError("커스텀 가상 회사를 찾을 수 없습니다.")

        user = self._user_for_public_slug(public_slug)
        if user is None:
            raise CustomOperatorNotFoundError("커스텀 가상 회사를 찾을 수 없습니다.")
        return user

    def _profile_for(self, user: User) -> Optional[CompanyProfile]:
        return (
            self.db.query(CompanyProfile)
            .filter(CompanyProfile.user_id == user.id)
            .first()
        )

    def _strategy_for(self, user: User) -> Optional[OperatorStrategy]:
        return (
            self.db.query(OperatorStrategy)
            .filter(OperatorStrategy.user_id == user.id)
            .first()
        )

    # --- create ----------------------------------------------------------------

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create a brand-new custom company. Raises on slug collision (409)."""
        bare_slug = _resolve_custom_slug(
            slug=payload.get("slug"), name=str(payload.get("name") or "")
        )
        username = _username_for(bare_slug)

        existing = self.db.query(User).filter(User.username == username).first()
        if existing is not None:
            raise CustomOperatorConflictError(
                f"slug '{_public_slug_for(bare_slug)}' 가상 회사가 이미 존재합니다."
            )

        display_name = str(payload.get("name") or "").strip() or bare_slug
        company_name = str(payload.get("company_name") or "").strip() or display_name

        user = User(
            username=username,
            email=synthetic_email(username),
            full_name=display_name,
            company=company_name,
            hashed_password=_unusable_password_hash(),
            is_active=True,
            is_admin=False,
        )
        self.db.add(user)
        self.db.flush()

        profile = CompanyProfile(user_id=user.id)
        self.db.add(profile)
        strategy = OperatorStrategy(user_id=user.id)
        self.db.add(strategy)
        self.db.flush()

        self._apply_profile(profile, payload, defaults=True)
        self._apply_strategy(strategy, payload, defaults=True)

        self.db.commit()
        self.db.refresh(user)
        return self.serialize(user)

    # --- read (single) ---------------------------------------------------------

    def get(self, public_slug: str) -> dict[str, Any]:
        """Fetch a single custom company's full profile+strategy (Phase 4 prefill).

        Presets and the canonical operator are protected (400) -- only custom
        companies (slug ``custom-*``) are returned. A missing custom company
        raises 404. The shape matches :meth:`serialize` (the create/update form
        shape) so the edit form can prefill without a second fetch.
        """
        user = self._require_custom_user(public_slug)
        return self.serialize(user)

    # --- update ----------------------------------------------------------------

    def update(self, public_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Partial-update a custom company. Presets/operator are protected (400)."""
        user = self._require_custom_user(public_slug)

        name = payload.get("name")
        if name is not None and str(name).strip():
            user.full_name = str(name).strip()
        company_name = payload.get("company_name")
        if company_name is not None and str(company_name).strip():
            user.company = str(company_name).strip()

        profile = self._profile_for(user) or self._ensure_profile(user)
        strategy = self._strategy_for(user) or self._ensure_strategy(user)
        self._apply_profile(profile, payload, defaults=False)
        self._apply_strategy(strategy, payload, defaults=False)

        self.db.commit()
        self.db.refresh(user)
        return self.serialize(user)

    # --- clone -----------------------------------------------------------------

    def clone(self, source_public_slug: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Clone any synthetic company (preset OR custom) into a new custom one.

        The source profile/strategy are copied, then the request body overrides any
        provided field. The source is never mutated. Cloning the canonical operator
        is rejected (only synthetic sources are cloneable).
        """
        if source_public_slug == DEFAULT_OPERATOR_USERNAME:
            raise CustomOperatorProtectedError("기본 운영자 계정은 복제할 수 없습니다.")
        source = self._user_for_public_slug(source_public_slug)
        if source is None:
            raise CustomOperatorNotFoundError("복제할 원본 가상 회사를 찾을 수 없습니다.")

        source_profile = self._profile_for(source)
        source_strategy = self._strategy_for(source)

        # Seed the create payload from the source, then layer the request overrides.
        merged: dict[str, Any] = self._snapshot(source, source_profile, source_strategy)
        for key, value in payload.items():
            if value is not None:
                merged[key] = value
        # A clone always gets a NEW name/slug from the body (fallbacks below).
        if not str(payload.get("name") or "").strip():
            merged["name"] = f"{source.full_name or source_public_slug} (복제)"
        merged["slug"] = payload.get("slug")  # explicit; resolved in create()
        if not merged.get("slug"):
            # Derive a fresh slug from the new name to avoid colliding with source.
            merged.pop("slug", None)

        return self.create(merged)

    # --- delete ----------------------------------------------------------------

    def delete(self, public_slug: str) -> dict[str, Any]:
        """Hard-delete a custom company (user + profile + strategy).

        Experiment results reference operators by slug *string*, so they remain
        intact after deletion. Presets and the canonical operator are protected.
        """
        user = self._require_custom_user(public_slug)
        user_id = int(user.id)
        username = user.username

        self.db.query(OperatorStrategy).filter(
            OperatorStrategy.user_id == user_id
        ).delete(synchronize_session=False)
        self.db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).delete(
            synchronize_session=False
        )
        self.db.delete(user)
        self.db.commit()
        return {"deleted": True, "slug": public_slug, "username": username}

    # --- field application -----------------------------------------------------

    def _ensure_profile(self, user: User) -> CompanyProfile:
        profile = CompanyProfile(user_id=user.id)
        self.db.add(profile)
        self.db.flush()
        return profile

    def _ensure_strategy(self, user: User) -> OperatorStrategy:
        strategy = OperatorStrategy(user_id=user.id)
        self.db.add(strategy)
        self.db.flush()
        return strategy

    def _apply_profile(
        self, profile: CompanyProfile, payload: dict[str, Any], *, defaults: bool
    ) -> None:
        if defaults:
            profile.business_type = (
                payload.get("business_type") or DEFAULT_BUSINESS_TYPE
            )
            profile.license_codes = join_multi_value_text(
                payload.get("license_codes") or []
            )
            profile.region_codes = join_multi_value_text(
                payload.get("region_codes") or []
            )
            profile.annual_revenue = float(payload.get("annual_revenue") or 0.0)
            profile.capacity_score = float(payload.get("capacity_score") or 0.0)
            return

        if "business_type" in payload and payload["business_type"] is not None:
            profile.business_type = payload["business_type"]
        if "license_codes" in payload and payload["license_codes"] is not None:
            profile.license_codes = join_multi_value_text(payload["license_codes"])
        if "region_codes" in payload and payload["region_codes"] is not None:
            profile.region_codes = join_multi_value_text(payload["region_codes"])
        if "annual_revenue" in payload and payload["annual_revenue"] is not None:
            profile.annual_revenue = float(payload["annual_revenue"])
        if "capacity_score" in payload and payload["capacity_score"] is not None:
            profile.capacity_score = float(payload["capacity_score"])

    def _apply_strategy(
        self, strategy: OperatorStrategy, payload: dict[str, Any], *, defaults: bool
    ) -> None:
        text_list_fields = (
            "focus_categories",
            "focus_regions",
            "exclude_regions",
            "required_keywords",
            "exclude_keywords",
        )
        float_fields = (
            "min_budget_estimate",
            "max_budget_estimate",
            "minimum_match_score",
            "minimum_probability_score",
            "bid_now_threshold",
            "review_threshold",
        )
        if defaults:
            for field_name in text_list_fields:
                setattr(
                    strategy,
                    field_name,
                    join_multi_value_text(payload.get(field_name) or []),
                )
            strategy.min_budget_estimate = float(
                payload.get("min_budget_estimate") or 0.0
            )
            strategy.max_budget_estimate = float(
                payload.get("max_budget_estimate") or 0.0
            )
            strategy.minimum_match_score = float(
                payload.get("minimum_match_score")
                if payload.get("minimum_match_score") is not None
                else DEFAULT_MINIMUM_MATCH_SCORE
            )
            strategy.minimum_probability_score = float(
                payload.get("minimum_probability_score")
                if payload.get("minimum_probability_score") is not None
                else DEFAULT_MINIMUM_PROBABILITY_SCORE
            )
            strategy.bid_now_threshold = float(
                payload.get("bid_now_threshold")
                if payload.get("bid_now_threshold") is not None
                else DEFAULT_OPERATOR_BID_NOW_THRESHOLD
            )
            strategy.review_threshold = float(
                payload.get("review_threshold")
                if payload.get("review_threshold") is not None
                else DEFAULT_OPERATOR_REVIEW_THRESHOLD
            )
            strategy.auto_workload_penalty_multiplier = 1.0
            strategy.category_priority_overrides = "{}"
            strategy.notify_only_high_priority = True
            strategy.max_recommended_candidates = int(
                payload.get("max_recommended_candidates")
                if payload.get("max_recommended_candidates") is not None
                else DEFAULT_MAX_RECOMMENDED_CANDIDATES
            )
            return

        for field_name in text_list_fields:
            if field_name in payload and payload[field_name] is not None:
                setattr(
                    strategy, field_name, join_multi_value_text(payload[field_name])
                )
        for field_name in float_fields:
            if field_name in payload and payload[field_name] is not None:
                setattr(strategy, field_name, float(payload[field_name]))
        if (
            "max_recommended_candidates" in payload
            and payload["max_recommended_candidates"] is not None
        ):
            strategy.max_recommended_candidates = int(
                payload["max_recommended_candidates"]
            )

    # --- serialization ---------------------------------------------------------

    def _snapshot(
        self,
        user: User,
        profile: Optional[CompanyProfile],
        strategy: Optional[OperatorStrategy],
    ) -> dict[str, Any]:
        """Full field snapshot used as the base payload when cloning."""
        snap: dict[str, Any] = {
            "name": user.full_name,
            "company_name": user.company,
        }
        if profile is not None:
            snap.update(
                {
                    "business_type": profile.business_type,
                    "license_codes": split_multi_value_text(profile.license_codes),
                    "region_codes": split_multi_value_text(profile.region_codes),
                    "annual_revenue": float(profile.annual_revenue or 0.0),
                    "capacity_score": float(profile.capacity_score or 0.0),
                }
            )
        if strategy is not None:
            snap.update(
                {
                    "focus_categories": split_multi_value_text(
                        strategy.focus_categories
                    ),
                    "focus_regions": split_multi_value_text(strategy.focus_regions),
                    "exclude_regions": split_multi_value_text(strategy.exclude_regions),
                    "required_keywords": split_multi_value_text(
                        strategy.required_keywords
                    ),
                    "exclude_keywords": split_multi_value_text(
                        strategy.exclude_keywords
                    ),
                    "min_budget_estimate": float(strategy.min_budget_estimate or 0.0),
                    "max_budget_estimate": float(strategy.max_budget_estimate or 0.0),
                    "minimum_match_score": float(strategy.minimum_match_score or 0.0),
                    "minimum_probability_score": float(
                        strategy.minimum_probability_score or 0.0
                    ),
                    "bid_now_threshold": float(strategy.bid_now_threshold or 0.0),
                    "review_threshold": float(strategy.review_threshold or 0.0),
                    "max_recommended_candidates": int(
                        strategy.max_recommended_candidates or 0
                    ),
                }
            )
        return snap

    def serialize(self, user: User) -> dict[str, Any]:
        """Render a custom company as the detailed operator response shape.

        Superset of ``SyntheticOperatorItem`` (adds ``is_custom`` + the full
        strategy field set) so the create/update/clone responses can drive the
        Phase 3 form without an extra fetch.
        """
        profile = self._profile_for(user)
        strategy = self._strategy_for(user)
        public_slug = user.username[len(SYNTHETIC_USERNAME_PREFIX) :]
        return {
            "user_id": int(user.id),
            "username": user.username,
            "slug": public_slug,
            "is_custom": is_custom_slug(public_slug),
            "display_name": user.full_name or public_slug,
            "company": user.company,
            "business_type": profile.business_type if profile else None,
            "annual_revenue": (
                float(profile.annual_revenue or 0.0) if profile else 0.0
            ),
            "capacity_score": (
                float(profile.capacity_score or 0.0) if profile else 0.0
            ),
            "license_codes": (
                split_multi_value_text(profile.license_codes) if profile else []
            ),
            "region_codes": (
                split_multi_value_text(profile.region_codes) if profile else []
            ),
            "focus_categories": (
                split_multi_value_text(strategy.focus_categories) if strategy else []
            ),
            "focus_regions": (
                split_multi_value_text(strategy.focus_regions) if strategy else []
            ),
            "exclude_regions": (
                split_multi_value_text(strategy.exclude_regions) if strategy else []
            ),
            "required_keywords": (
                split_multi_value_text(strategy.required_keywords) if strategy else []
            ),
            "exclude_keywords": (
                split_multi_value_text(strategy.exclude_keywords) if strategy else []
            ),
            "min_budget_estimate": (
                float(strategy.min_budget_estimate or 0.0) if strategy else 0.0
            ),
            "max_budget_estimate": (
                float(strategy.max_budget_estimate or 0.0) if strategy else 0.0
            ),
            "minimum_match_score": (
                float(strategy.minimum_match_score or 0.0) if strategy else 0.0
            ),
            "minimum_probability_score": (
                float(strategy.minimum_probability_score or 0.0) if strategy else 0.0
            ),
            "bid_now_threshold": (
                float(strategy.bid_now_threshold or 0.0) if strategy else 0.0
            ),
            "review_threshold": (
                float(strategy.review_threshold or 0.0) if strategy else 0.0
            ),
            "max_recommended_candidates": (
                int(strategy.max_recommended_candidates or 0) if strategy else 0
            ),
        }
