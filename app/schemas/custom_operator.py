"""Custom synthetic-operator create / update / clone schemas."""

from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CustomOperatorBase(BaseModel):
    """Shared strategy/profile fields for a custom synthetic company.

    All fields optional here; ``CustomOperatorCreate`` re-declares ``name`` as
    required. Multi-value text fields (categories/regions/keywords/licenses) are
    accepted as string lists and stored as the repo's comma-joined text format.
    """

    model_config = ConfigDict(from_attributes=True)

    company_name: Optional[str] = Field(default=None, max_length=100)
    business_type: Optional[str] = Field(default=None, max_length=50)
    license_codes: Optional[List[str]] = None
    region_codes: Optional[List[str]] = None
    annual_revenue: Optional[float] = Field(default=None, ge=0.0)
    capacity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    focus_categories: Optional[List[str]] = None
    focus_regions: Optional[List[str]] = None
    exclude_regions: Optional[List[str]] = None
    required_keywords: Optional[List[str]] = None
    exclude_keywords: Optional[List[str]] = None
    min_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    max_budget_estimate: Optional[float] = Field(default=None, ge=0.0)
    minimum_match_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    minimum_probability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    bid_now_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    review_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    max_recommended_candidates: Optional[int] = Field(default=None, ge=1, le=100)


class CustomOperatorCreate(CustomOperatorBase):
    """Create a new custom synthetic company (``synthetic-custom-<slug>``)."""

    name: str = Field(min_length=1, max_length=100)
    slug: Optional[str] = Field(
        default=None,
        max_length=80,
        description="Optional explicit slug; normalized to [a-z0-9-]. Derived from name when omitted.",
    )


class CustomOperatorUpdate(CustomOperatorBase):
    """Partial-update a custom synthetic company (all fields optional)."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)


class CustomOperatorCloneRequest(CustomOperatorBase):
    """Clone a preset/custom company into a new custom one with overrides."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    slug: Optional[str] = Field(default=None, max_length=80)


class CustomOperatorDetail(BaseModel):
    """Detailed custom-company shape returned by create/update/clone.

    Superset of ``SyntheticOperatorItem`` (the list shape) plus the full strategy
    field set so the Phase 3 form can render without a second fetch.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: int
    username: str
    slug: str
    is_custom: bool = True
    display_name: str
    company: Optional[str] = None
    business_type: Optional[str] = None
    annual_revenue: float = 0.0
    capacity_score: float = 0.0
    license_codes: List[str] = Field(default_factory=list)
    region_codes: List[str] = Field(default_factory=list)
    focus_categories: List[str] = Field(default_factory=list)
    focus_regions: List[str] = Field(default_factory=list)
    exclude_regions: List[str] = Field(default_factory=list)
    required_keywords: List[str] = Field(default_factory=list)
    exclude_keywords: List[str] = Field(default_factory=list)
    min_budget_estimate: float = 0.0
    max_budget_estimate: float = 0.0
    minimum_match_score: float = 0.0
    minimum_probability_score: float = 0.0
    bid_now_threshold: float = 0.0
    review_threshold: float = 0.0
    max_recommended_candidates: int = 0


class CustomOperatorDeleteResponse(BaseModel):
    """Confirmation payload for a custom-company delete."""

    model_config = ConfigDict(from_attributes=True)

    deleted: bool = True
    slug: str
    username: str
