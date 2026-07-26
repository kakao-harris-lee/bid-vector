"""Account switcher + notification domain impls for the single-operator API.

Pure move (#295 pattern) from ``app/api/operator.py``: the ``@router`` entries
stay in operator.py (thin), forwarding the resolved operator plus the raw
request/query values here. No behaviour change.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.operator_common import (
    _SYNTHETIC_USERNAME_PREFIX,
    _is_profile_configured,
    _operator_context_fields,
)
from app.core.security import (
    CANONICAL_OPERATOR_USERNAME,
    is_privileged_operator,
)
from app.core.single_user import (
    ensure_operator_account,
    split_multi_value_text,
)
from app.models.models import (
    CompanyProfile,
    Notification,
    OperatorNotificationChannel,
    User,
)
from app.schemas.schemas import (
    OperatorAccountItem,
    OperatorAccountListResponse,
    OperatorNotificationChannelItem,
    OperatorNotificationChannelListResponse,
)
from app.services.notifications.manager import (
    OperatorNotificationService,
    mask_notification_route_key,
    mask_notification_target,
)


def _serialize_operator_account(
    user: User,
    *,
    profile: CompanyProfile | None,
) -> OperatorAccountItem:
    """Convert a ``User`` into the compact account row used by the switcher."""
    business_type = profile.business_type if profile is not None else None
    return OperatorAccountItem(
        operator_id=int(user.id),
        username=str(user.username or ""),
        full_name=user.full_name,
        company=user.company,
        business_type=business_type,
        is_canonical=user.username == CANONICAL_OPERATOR_USERNAME,
        is_synthetic=bool(
            user.username and user.username.startswith(_SYNTHETIC_USERNAME_PREFIX)
        ),
        is_active=bool(user.is_active),
        profile_configured=profile is not None
        and _is_profile_configured(
            license_codes=split_multi_value_text(profile.license_codes),
            region_codes=split_multi_value_text(profile.region_codes),
            annual_revenue=float(profile.annual_revenue or 0.0),
            capacity_score=float(profile.capacity_score or 0.0),
            total_awards=int(profile.total_awards or 0),
            construction_capacity_amount=float(
                profile.construction_capacity_amount or 0.0
            ),
            awarded_contract_limit=float(profile.awarded_contract_limit or 0.0),
        ),
    )


def _serialize_notification_channel(
    channel: OperatorNotificationChannel,
) -> OperatorNotificationChannelItem:
    return OperatorNotificationChannelItem(
        channel_id=int(channel.id),
        operator_id=int(channel.operator_id),
        channel_type=str(channel.channel_type),
        route_key=mask_notification_route_key(channel.route_key),
        target_label=mask_notification_target(channel.target_label),
        is_active=bool(channel.is_active),
        dry_run_only=bool(channel.dry_run_only),
        source="operator_notification_channels",
        verified_at=channel.verified_at,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _notification_channel_item_from_plan(
    plan: dict[str, object],
) -> OperatorNotificationChannelItem:
    channel_id = plan.get("channel_id")
    return OperatorNotificationChannelItem(
        channel_id=int(channel_id) if channel_id is not None else None,
        operator_id=int(plan["operator_id"]),
        channel_type=str(plan.get("channel_type") or ""),
        route_key=str(plan.get("route_key") or ""),
        target_label=(
            str(plan["target_label"]) if plan.get("target_label") is not None else None
        ),
        is_active=bool(plan.get("channel_active")),
        dry_run_only=bool(plan.get("dry_run_only")),
        source=str(plan.get("channel_source") or ""),
    )


def list_operator_accounts_impl(current_operator: User | None, db: Session) -> OperatorAccountListResponse:
    actor = current_operator or ensure_operator_account(db)
    privileged = is_privileged_operator(actor)

    if privileged:
        users = (
            db.query(User)
            .filter(
                (User.username == CANONICAL_OPERATOR_USERNAME)
                | (User.username.like(f"{_SYNTHETIC_USERNAME_PREFIX}%"))
                | (User.id == actor.id)
            )
            .order_by(User.id.asc())
            .all()
        )
    else:
        users = [actor]

    if not users:
        users = [actor]

    user_ids = [int(user.id) for user in users]
    profile_rows = (
        db.query(CompanyProfile).filter(CompanyProfile.user_id.in_(user_ids)).all()
        if user_ids
        else []
    )
    profile_by_user = {int(row.user_id): row for row in profile_rows}

    operators: list[OperatorAccountItem] = []
    seen_ids: set[int] = set()
    for user in users:
        if int(user.id) in seen_ids:
            continue
        seen_ids.add(int(user.id))
        operators.append(
            _serialize_operator_account(user, profile=profile_by_user.get(int(user.id)))
        )

    return OperatorAccountListResponse(
        current_operator_id=int(actor.id),
        current_operator_username=str(actor.username or ""),
        is_privileged=privileged,
        operator_count=len(operators),
        operators=operators,
    )


def list_operator_notification_channels_impl(target: User, db: Session) -> OperatorNotificationChannelListResponse:
    rows = (
        db.query(OperatorNotificationChannel)
        .filter(OperatorNotificationChannel.operator_id == int(target.id))
        .order_by(
            OperatorNotificationChannel.channel_type.asc(),
            OperatorNotificationChannel.is_active.desc(),
            OperatorNotificationChannel.id.asc(),
        )
        .all()
    )
    channels = [_serialize_notification_channel(row) for row in rows]

    has_telegram_row = any(row.channel_type == "telegram" for row in rows)
    if not has_telegram_row:
        plan = OperatorNotificationService().build_telegram_delivery_plan(
            db, operator_id=int(target.id)
        )
        if plan.get("channel_source") == "legacy_settings":
            channels.append(_notification_channel_item_from_plan(plan))

    return OperatorNotificationChannelListResponse(
        operator_id=int(target.id),
        **_operator_context_fields(target),
        channel_count=len(channels),
        channels=channels,
    )


def list_operator_notifications_impl(
    db: Session,
    limit: int,
    unread_only: bool,
    notification_type: str | None,
):
    operator = ensure_operator_account(db)
    query = db.query(Notification).filter(Notification.user_id == operator.id)

    if unread_only:
        query = query.filter(Notification.is_read.is_(False))

    if notification_type:
        query = query.filter(Notification.type == notification_type)

    return query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()


def mark_operator_notification_read_impl(notification_id: int, db: Session):
    operator = ensure_operator_account(db)
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == operator.id)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")

    return OperatorNotificationService().mark_as_read(db, notification)
