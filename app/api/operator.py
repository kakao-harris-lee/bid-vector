"""Single-operator profile and overview routes.

Router shell (#295 pattern): every ``@router`` entry — its path, HTTP method,
``response_model``, signature and docstring — stays here so the generated
OpenAPI schema is unchanged. Each handler resolves the operator scope and
delegates its body to a per-domain impl module (``operator_profile``,
``operator_strategy``, ``operator_dashboard``, ``operator_accounts``,
``operator_feedback``); shared helpers live in ``operator_common``.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.operator_accounts import (
    list_operator_accounts_impl,
    list_operator_notification_channels_impl,
    list_operator_notifications_impl,
    mark_operator_notification_read_impl,
)
from app.api.operator_dashboard import (
    get_operator_dashboard_impl,
    get_operator_overview_impl,
)
from app.api.operator_feedback import submit_eligibility_feedback_impl
from app.api.operator_profile import (
    get_operator_profile_impl,
    update_operator_profile_impl,
)
from app.api.operator_strategy import (
    get_monitor_run_detail_impl,
    get_monitor_status_impl,
    get_operator_strategy_impl,
    list_monitor_runs_impl,
    list_strategy_candidates_impl,
    run_monitor_async_impl,
    run_strategy_monitor_impl,
    update_operator_strategy_impl,
)
from app.core.database import get_db
from app.core.security import get_current_operator_optional
from app.core.single_user import (
    resolve_read_operator,
    resolve_write_operator,
)
from app.models.models import User
from app.schemas.eligibility_feedback import (
    EligibilityFeedbackRequest,
    EligibilityFeedbackResponse,
)
from app.schemas.schemas import (
    NotificationResponse,
    OperatorAccountListResponse,
    OperatorDashboardResponse,
    OperatorNotificationChannelListResponse,
    OperatorOverviewResponse,
    OperatorProfileResponse,
    OperatorProfileUpdate,
    OperatorStrategyCandidatesResponse,
    OperatorStrategyMonitorRequest,
    OperatorStrategyMonitorResponse,
    OperatorStrategyMonitorTaskResponse,
    OperatorStrategyMonitorTaskStatusResponse,
    OperatorStrategyRunDetailResponse,
    OperatorStrategyRunListResponse,
    OperatorStrategyResponse,
    OperatorStrategyUpdate,
)

router = APIRouter()


# Read-scoped operator resolution is shared across analytics/operator/decision
# endpoints; see app.core.single_user.resolve_read_operator for the security
# policy (canonical fallback / 403 / 404). The returned operator also populates
# the ``current_operator_*`` envelope fields (convention from PR #70). Kept as a
# thin alias so existing call sites and their names remain stable.
_resolve_operator_for_read = resolve_read_operator


# Write-scoped operator resolution (self-only; 403 on cross-operator writes) is
# shared by PUT /operator/profile, PUT /operator/strategy, the eligibility
# feedback route, and the onboarding apply endpoint. See
# app.core.single_user.resolve_write_operator for the canonical/synthetic write
# isolation policy. Kept as a thin alias so existing call sites and their names
# remain stable.
_resolve_operator_for_write = resolve_write_operator


@router.get("/profile", response_model=OperatorProfileResponse)
def get_operator_profile_endpoint(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return the operator account and company profile for the active context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_operator_profile_impl(target, db)


@router.put("/profile", response_model=OperatorProfileResponse)
def update_operator_profile(
    request: OperatorProfileUpdate,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Update the active operator's account and fit profile settings.

    Edits are restricted to the bearer-token owner. Supplying ``?operator_id``
    that differs from the actor's id returns 403; synthetic-company profile
    edits must go through ``/synthetic/custom-operators/{slug}`` instead.
    """
    actor = _resolve_operator_for_write(db, current_operator, operator_id)
    return update_operator_profile_impl(request, actor, db)


@router.get("/strategy", response_model=OperatorStrategyResponse)
def get_operator_strategy_endpoint(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return the watch strategy for the active operator context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_operator_strategy_impl(target, db)


@router.put("/strategy", response_model=OperatorStrategyResponse)
def update_operator_strategy(
    request: OperatorStrategyUpdate,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Update the active operator's watch rules used for monitoring and prioritization.

    Like :func:`update_operator_profile`, edits are restricted to the
    bearer-token owner — ``?operator_id`` mismatched against the actor returns
    403. Cross-operator strategy edits are not supported.
    """
    actor = _resolve_operator_for_write(db, current_operator, operator_id)
    return update_operator_strategy_impl(request, actor, db)


@router.get("/strategy/candidates", response_model=OperatorStrategyCandidatesResponse)
def list_operator_strategy_candidates(
    limit: int | None = Query(default=None, ge=1, le=100),
    high_priority_only: bool | None = Query(default=None),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Preview currently open projects that match the active operator's watch strategy."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return list_strategy_candidates_impl(target, db, limit, high_priority_only)


@router.post("/strategy/monitor", response_model=OperatorStrategyMonitorResponse)
def run_operator_strategy_monitor(
    request: OperatorStrategyMonitorRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Execute the stored strategy, persist bid decisions, and create operator notifications."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return run_strategy_monitor_impl(request, target, db)


@router.get("/strategy/monitor/runs", response_model=OperatorStrategyRunListResponse)
def list_operator_strategy_monitor_runs(
    limit: int = Query(default=20, ge=1, le=100),
    run_status: str | None = Query(default=None, alias="status"),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return recent strategy monitoring execution history for the resolved operator."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return list_monitor_runs_impl(operator, db, limit, run_status)


@router.get("/strategy/monitor/runs/{run_id}", response_model=OperatorStrategyRunDetailResponse)
def get_operator_strategy_monitor_run_detail(
    run_id: int,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return one strategy monitor run with full payloads and candidate diff details."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_monitor_run_detail_impl(run_id, operator, db)


@router.post("/strategy/monitor/async", response_model=OperatorStrategyMonitorTaskResponse)
def run_operator_strategy_monitor_async(
    request: OperatorStrategyMonitorRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Queue operator strategy monitoring work and return a pollable task id."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return run_monitor_async_impl(request, operator, db)


@router.get("/strategy/monitor/tasks/{task_id}", response_model=OperatorStrategyMonitorTaskStatusResponse)
def get_operator_strategy_monitor_status(
    task_id: str,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Inspect the current status and final result of a queued strategy monitoring task."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_monitor_status_impl(task_id, operator, db)


@router.get("/dashboard", response_model=OperatorDashboardResponse)
def get_operator_dashboard(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(5, ge=1, le=20),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return a card-ready web dashboard payload for analysis, decisions, monitoring, and feedback."""
    operator = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_operator_dashboard_impl(operator, operator_id, days, limit, db)


@router.get("/overview", response_model=OperatorOverviewResponse)
def get_operator_overview(
    days: int = Query(7, ge=1, le=90),
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return a compact dashboard summary for the active operator context."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return get_operator_overview_impl(target, days, db)


@router.get("/accounts", response_model=OperatorAccountListResponse)
def list_operator_accounts(
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """List operator accounts visible to the current bearer-token owner.

    Privileged callers (canonical ``operator`` or ``is_admin``) receive the full
    catalogue (canonical + ``synthetic-*``). Non-privileged callers receive only
    their own row so the dropdown collapses to a single self-pick.
    """
    return list_operator_accounts_impl(current_operator, db)


@router.get("/notification-channels", response_model=OperatorNotificationChannelListResponse)
def list_operator_notification_channels(
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """Return masked notification route metadata for the resolved operator."""
    target = _resolve_operator_for_read(db, current_operator, operator_id)
    return list_operator_notification_channels_impl(target, db)


@router.get("/notifications", response_model=list[NotificationResponse])
def list_operator_notifications(
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    notification_type: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return recent notifications for the singleton operator's web dashboard."""
    return list_operator_notifications_impl(db, limit, unread_only, notification_type)


@router.put("/notifications/{notification_id}/read", response_model=NotificationResponse)
def mark_operator_notification_read(notification_id: int, db: Session = Depends(get_db)):
    """Mark a notification as read from the web dashboard."""
    return mark_operator_notification_read_impl(notification_id, db)


@router.post("/eligibility-feedback", response_model=EligibilityFeedbackResponse)
def submit_eligibility_feedback(
    request: EligibilityFeedbackRequest,
    operator_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_operator: User | None = Depends(get_current_operator_optional),
):
    """운영자 식별 피드백(적합/부적합/보류)을 operator 소스 라벨로 upsert 한다.

    식별 피드백은 투찰 결정(BidDecisionRecord)과 별개 축으로, 어느 공고든
    (eligibility_raw 유무 무관) 저장한다. precision/recall 은 리포트가 rule∩operator
    교집합에서만 산출하므로 rule 라벨이 뒤늦게 backfill 되면 지표에 자동 편입된다.
    정답 세트 오염 방지를 위해 synthetic operator 는 거부한다(canonical 만 허용).
    """
    operator = _resolve_operator_for_write(db, current_operator, operator_id)
    return submit_eligibility_feedback_impl(request, operator, db)
