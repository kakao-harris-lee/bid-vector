"""Profile domain impls for the single-operator API.

Pure move (#295 pattern) from ``app/api/operator.py``: the ``@router`` entries
stay in operator.py (thin), forwarding the resolved operator plus the raw
request/query values here. No behaviour change.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.operator_common import _is_profile_configured
from app.core.single_user import (
    ensure_operator_profile_for,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import User
from app.schemas.schemas import (
    OperatorProfileResponse,
    OperatorProfileUpdate,
)


def _build_operator_profile_response(
    operator: User,
    license_codes: list[str],
    region_codes: list[str],
    business_type: str,
    annual_revenue: float,
    capacity_score: float,
    total_awards: int,
    construction_capacity_amount: float = 0.0,
    awarded_contract_limit: float = 0.0,
    association_memberships: list[str] | None = None,
    tech_fields: list[str] | None = None,
) -> OperatorProfileResponse:
    return OperatorProfileResponse(
        operator_id=operator.id,
        username=operator.username,
        email=operator.email,
        full_name=operator.full_name,
        company=operator.company,
        is_active=operator.is_active,
        created_at=operator.created_at,
        business_type=business_type,
        license_codes=license_codes,
        region_codes=region_codes,
        association_memberships=association_memberships or [],
        tech_fields=tech_fields or [],
        annual_revenue=annual_revenue,
        capacity_score=capacity_score,
        construction_capacity_amount=construction_capacity_amount,
        awarded_contract_limit=awarded_contract_limit,
        total_awards=total_awards,
        profile_configured=_is_profile_configured(
            license_codes=license_codes,
            region_codes=region_codes,
            annual_revenue=annual_revenue,
            capacity_score=capacity_score,
            total_awards=total_awards,
            construction_capacity_amount=construction_capacity_amount,
            awarded_contract_limit=awarded_contract_limit,
        ),
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
    )


def get_operator_profile_impl(target: User, db: Session) -> OperatorProfileResponse:
    profile = ensure_operator_profile_for(db, target)
    license_codes = split_multi_value_text(profile.license_codes)
    region_codes = split_multi_value_text(profile.region_codes)
    return _build_operator_profile_response(
        operator=target,
        license_codes=license_codes,
        region_codes=region_codes,
        business_type=profile.business_type,
        annual_revenue=profile.annual_revenue,
        capacity_score=profile.capacity_score,
        total_awards=profile.total_awards,
        construction_capacity_amount=float(
            profile.construction_capacity_amount or 0.0
        ),
        awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        association_memberships=split_multi_value_text(
            profile.association_memberships
        ),
        tech_fields=split_multi_value_text(profile.tech_fields),
    )


def update_operator_profile_impl(
    request: OperatorProfileUpdate,
    actor: User,
    db: Session,
) -> OperatorProfileResponse:
    operator = actor
    profile = ensure_operator_profile_for(db, actor)

    if request.username is not None and request.username != operator.username:
        existing_username = db.query(User).filter(User.username == request.username, User.id != operator.id).first()
        if existing_username:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Username already exists")
        operator.username = request.username

    if request.email is not None and request.email != operator.email:
        existing_email = db.query(User).filter(User.email == request.email, User.id != operator.id).first()
        if existing_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
        operator.email = request.email

    if request.full_name is not None:
        operator.full_name = request.full_name
    if request.company is not None:
        operator.company = request.company
    if request.business_type is not None:
        profile.business_type = request.business_type
    if request.license_codes is not None:
        profile.license_codes = join_multi_value_text(request.license_codes)
    if request.region_codes is not None:
        profile.region_codes = join_multi_value_text(request.region_codes)
    if request.association_memberships is not None:
        profile.association_memberships = join_multi_value_text(
            request.association_memberships
        )
    if request.tech_fields is not None:
        profile.tech_fields = join_multi_value_text(request.tech_fields)
    if request.annual_revenue is not None:
        profile.annual_revenue = request.annual_revenue
    if request.capacity_score is not None:
        profile.capacity_score = request.capacity_score
    if request.construction_capacity_amount is not None:
        profile.construction_capacity_amount = request.construction_capacity_amount
    if request.awarded_contract_limit is not None:
        profile.awarded_contract_limit = request.awarded_contract_limit
    if request.total_awards is not None:
        profile.total_awards = request.total_awards

    db.commit()
    db.refresh(operator)
    db.refresh(profile)

    license_codes = split_multi_value_text(profile.license_codes)
    region_codes = split_multi_value_text(profile.region_codes)
    return _build_operator_profile_response(
        operator=operator,
        license_codes=license_codes,
        region_codes=region_codes,
        business_type=profile.business_type,
        annual_revenue=profile.annual_revenue,
        capacity_score=profile.capacity_score,
        total_awards=profile.total_awards,
        construction_capacity_amount=float(
            profile.construction_capacity_amount or 0.0
        ),
        awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        association_memberships=split_multi_value_text(
            profile.association_memberships
        ),
        tech_fields=split_multi_value_text(profile.tech_fields),
    )
