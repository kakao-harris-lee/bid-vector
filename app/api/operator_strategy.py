"""Strategy + monitoring domain impls for the single-operator API.

Pure move (#295 pattern) from ``app/api/operator.py``: the ``@router`` entries
stay in operator.py (thin), forwarding the resolved operator plus the raw
request/query values here. No behaviour change.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.api.operator_common import _operator_context_fields
from app.core.single_user import (
    DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
    DEFAULT_OPERATOR_REVIEW_THRESHOLD,
    ensure_operator_strategy_for,
    join_multi_value_text,
    split_multi_value_text,
)
from app.models.models import OperatorStrategyRun, User
from app.schemas.schemas import (
    OperatorStrategyResponse,
    OperatorStrategyUpdate,
)
from app.services.opportunity_monitoring import StrategyMonitoringService
from app.services.preview_snapshot import PreviewSnapshotService
from app.services.operator_strategy_tuning import (
    clamp_auto_workload_penalty_multiplier,
    dump_category_priority_overrides,
    get_strategy_auto_workload_penalty_multiplier,
    get_strategy_category_priority_overrides,
)
from app.tasks.jobs import enqueue_operator_strategy_monitor, get_operator_strategy_monitor_task_status


def _is_strategy_configured(
    focus_categories: list[str],
    focus_regions: list[str],
    exclude_regions: list[str],
    required_keywords: list[str],
    exclude_keywords: list[str],
    min_budget_estimate: float,
    max_budget_estimate: float,
    minimum_match_score: float,
    minimum_probability_score: float,
    bid_now_threshold: float,
    review_threshold: float,
    auto_workload_penalty_multiplier: float,
    category_priority_overrides: dict[str, float],
    notify_only_high_priority: bool,
    max_recommended_candidates: int,
) -> bool:
    return any([
        bool(focus_categories),
        bool(focus_regions),
        bool(exclude_regions),
        bool(required_keywords),
        bool(exclude_keywords),
        min_budget_estimate > 0,
        max_budget_estimate > 0,
        round(minimum_match_score, 4) != 0.6,
        round(minimum_probability_score, 4) != 0.55,
        round(bid_now_threshold, 4) != DEFAULT_OPERATOR_BID_NOW_THRESHOLD,
        round(review_threshold, 4) != DEFAULT_OPERATOR_REVIEW_THRESHOLD,
        round(auto_workload_penalty_multiplier, 4) != 1.0,
        bool(category_priority_overrides),
        notify_only_high_priority is False,
        max_recommended_candidates != 10,
    ])


def _validate_strategy_thresholds(*, bid_now_threshold: float, review_threshold: float) -> None:
    if review_threshold > bid_now_threshold:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="review_threshold cannot be greater than bid_now_threshold",
        )


def _build_operator_strategy_response(
    operator: User,
    focus_categories: list[str],
    focus_regions: list[str],
    exclude_regions: list[str],
    required_keywords: list[str],
    exclude_keywords: list[str],
    min_budget_estimate: float,
    max_budget_estimate: float,
    minimum_match_score: float,
    minimum_probability_score: float,
    bid_now_threshold: float,
    review_threshold: float,
    auto_workload_penalty_multiplier: float,
    category_priority_overrides: dict[str, float],
    notify_only_high_priority: bool,
    max_recommended_candidates: int,
) -> OperatorStrategyResponse:
    _validate_strategy_thresholds(
        bid_now_threshold=bid_now_threshold,
        review_threshold=review_threshold,
    )
    return OperatorStrategyResponse(
        operator_id=operator.id,
        focus_categories=focus_categories,
        focus_regions=focus_regions,
        exclude_regions=exclude_regions,
        required_keywords=required_keywords,
        exclude_keywords=exclude_keywords,
        min_budget_estimate=min_budget_estimate,
        max_budget_estimate=max_budget_estimate,
        minimum_match_score=minimum_match_score,
        minimum_probability_score=minimum_probability_score,
        bid_now_threshold=bid_now_threshold,
        review_threshold=review_threshold,
        auto_workload_penalty_multiplier=auto_workload_penalty_multiplier,
        category_priority_overrides=category_priority_overrides,
        notify_only_high_priority=notify_only_high_priority,
        max_recommended_candidates=max_recommended_candidates,
        strategy_configured=_is_strategy_configured(
            focus_categories=focus_categories,
            focus_regions=focus_regions,
            exclude_regions=exclude_regions,
            required_keywords=required_keywords,
            exclude_keywords=exclude_keywords,
            min_budget_estimate=min_budget_estimate,
            max_budget_estimate=max_budget_estimate,
            minimum_match_score=minimum_match_score,
            minimum_probability_score=minimum_probability_score,
            bid_now_threshold=bid_now_threshold,
            review_threshold=review_threshold,
            auto_workload_penalty_multiplier=auto_workload_penalty_multiplier,
            category_priority_overrides=category_priority_overrides,
            notify_only_high_priority=notify_only_high_priority,
            max_recommended_candidates=max_recommended_candidates,
        ),
        current_operator_id=int(operator.id),
        current_operator_username=str(operator.username or ""),
    )


def get_operator_strategy_impl(target: User, db: Session) -> OperatorStrategyResponse:
    strategy = ensure_operator_strategy_for(db, target)
    return _build_operator_strategy_response(
        operator=target,
        focus_categories=split_multi_value_text(strategy.focus_categories),
        focus_regions=split_multi_value_text(strategy.focus_regions),
        exclude_regions=split_multi_value_text(strategy.exclude_regions),
        required_keywords=split_multi_value_text(strategy.required_keywords),
        exclude_keywords=split_multi_value_text(strategy.exclude_keywords),
        min_budget_estimate=float(strategy.min_budget_estimate or 0.0),
        max_budget_estimate=float(strategy.max_budget_estimate or 0.0),
        minimum_match_score=float(strategy.minimum_match_score or 0.0),
        minimum_probability_score=float(strategy.minimum_probability_score or 0.0),
        bid_now_threshold=float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD),
        review_threshold=float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD),
        auto_workload_penalty_multiplier=get_strategy_auto_workload_penalty_multiplier(strategy),
        category_priority_overrides=get_strategy_category_priority_overrides(strategy),
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


def update_operator_strategy_impl(
    request: OperatorStrategyUpdate,
    actor: User,
    db: Session,
) -> OperatorStrategyResponse:
    operator = actor
    strategy = ensure_operator_strategy_for(db, actor)

    next_bid_now_threshold = float(
        request.bid_now_threshold
        if request.bid_now_threshold is not None
        else strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD
    )
    next_review_threshold = float(
        request.review_threshold
        if request.review_threshold is not None
        else strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD
    )
    _validate_strategy_thresholds(
        bid_now_threshold=next_bid_now_threshold,
        review_threshold=next_review_threshold,
    )

    if request.focus_categories is not None:
        strategy.focus_categories = join_multi_value_text(request.focus_categories)
    if request.focus_regions is not None:
        strategy.focus_regions = join_multi_value_text(request.focus_regions)
    if request.exclude_regions is not None:
        strategy.exclude_regions = join_multi_value_text(request.exclude_regions)
    if request.required_keywords is not None:
        strategy.required_keywords = join_multi_value_text(request.required_keywords)
    if request.exclude_keywords is not None:
        strategy.exclude_keywords = join_multi_value_text(request.exclude_keywords)
    if request.min_budget_estimate is not None:
        strategy.min_budget_estimate = request.min_budget_estimate
    if request.max_budget_estimate is not None:
        strategy.max_budget_estimate = request.max_budget_estimate
    if request.minimum_match_score is not None:
        strategy.minimum_match_score = request.minimum_match_score
    if request.minimum_probability_score is not None:
        strategy.minimum_probability_score = request.minimum_probability_score
    if request.bid_now_threshold is not None:
        strategy.bid_now_threshold = request.bid_now_threshold
    if request.review_threshold is not None:
        strategy.review_threshold = request.review_threshold
    if request.auto_workload_penalty_multiplier is not None:
        strategy.auto_workload_penalty_multiplier = clamp_auto_workload_penalty_multiplier(
            request.auto_workload_penalty_multiplier
        )
    if request.category_priority_overrides is not None:
        strategy.category_priority_overrides = dump_category_priority_overrides(
            request.category_priority_overrides
        )
    if request.notify_only_high_priority is not None:
        strategy.notify_only_high_priority = request.notify_only_high_priority
    if request.max_recommended_candidates is not None:
        strategy.max_recommended_candidates = request.max_recommended_candidates

    db.commit()
    db.refresh(strategy)
    # 전략 저장은 preview 산출을 바꾼다: 사용 중인 스냅샷 키를 단일비행 가드
    # 하에 재계산 디스패치한다 (설계 §6.3 — 구 preview_cache.invalidate 대체).
    PreviewSnapshotService().dispatch_for_strategy_write(db, operator_id=int(operator.id))

    return _build_operator_strategy_response(
        operator=operator,
        focus_categories=split_multi_value_text(strategy.focus_categories),
        focus_regions=split_multi_value_text(strategy.focus_regions),
        exclude_regions=split_multi_value_text(strategy.exclude_regions),
        required_keywords=split_multi_value_text(strategy.required_keywords),
        exclude_keywords=split_multi_value_text(strategy.exclude_keywords),
        min_budget_estimate=float(strategy.min_budget_estimate or 0.0),
        max_budget_estimate=float(strategy.max_budget_estimate or 0.0),
        minimum_match_score=float(strategy.minimum_match_score or 0.0),
        minimum_probability_score=float(strategy.minimum_probability_score or 0.0),
        bid_now_threshold=float(strategy.bid_now_threshold or DEFAULT_OPERATOR_BID_NOW_THRESHOLD),
        review_threshold=float(strategy.review_threshold or DEFAULT_OPERATOR_REVIEW_THRESHOLD),
        auto_workload_penalty_multiplier=get_strategy_auto_workload_penalty_multiplier(strategy),
        category_priority_overrides=get_strategy_category_priority_overrides(strategy),
        notify_only_high_priority=bool(strategy.notify_only_high_priority),
        max_recommended_candidates=int(strategy.max_recommended_candidates or 10),
    )


def list_strategy_candidates_impl(
    target: User,
    db: Session,
    limit: int | None,
    high_priority_only: bool | None,
) -> dict:
    """스냅샷 순수 읽기 (설계 2026-07-30 §6.2) — 요청 경로 인라인 ML 스캔 없음."""
    payload = PreviewSnapshotService().serve(
        db,
        operator=target,
        limit=limit,
        high_priority_only=high_priority_only,
    )
    payload.update(_operator_context_fields(target))
    return payload


def refresh_strategy_candidates_impl(
    target: User,
    db: Session,
    high_priority_only: bool | None,
) -> dict:
    """명시 재계산 디스패치 (202, 설계 §6.2). 단일비행: 이미 running 이면 그
    task 를 재사용한다 — 새로고침 연타가 스캔을 중복 실행하지 못한다."""
    service = PreviewSnapshotService()
    resolved_high_priority_only = service.resolve_high_priority_key(
        db, operator=target, high_priority_only=high_priority_only
    )
    row = service.dispatch_recompute(
        db, operator_id=int(target.id), high_priority_only=resolved_high_priority_only
    )
    already_running = row is None
    if row is None:
        row = service.get_row(
            db, operator_id=int(target.id), high_priority_only=resolved_high_priority_only
        )
    return {
        "task_id": row.task_id if row is not None else None,
        "operator_id": int(target.id),
        **_operator_context_fields(target),
        "high_priority_only": bool(resolved_high_priority_only),
        "snapshot_status": str(row.status) if row is not None else "failed",
        "detail": (
            "이미 실행 중인 재계산을 재사용합니다."
            if already_running
            else "미리보기 재계산을 큐에 등록했습니다."
        ),
        "poll_url": "/api/v1/operator/strategy/candidates",
    }


def list_monitor_runs_impl(
    operator: User,
    db: Session,
    limit: int,
    run_status: str | None,
) -> dict:
    service = StrategyMonitoringService()
    runs = service.list_recent_runs(db, limit=limit, run_status=run_status, operator=operator)
    return {
        "operator_id": operator.id,
        **_operator_context_fields(operator),
        "result_count": len(runs),
        "runs": [service.serialize_run(run) for run in runs],
    }


def get_monitor_run_detail_impl(run_id: int, operator: User, db: Session):
    service = StrategyMonitoringService()
    try:
        return service.get_run_detail(db, run_id=run_id, operator=operator)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def run_monitor_async_impl(request, operator: User, db: Session) -> dict:
    service = StrategyMonitoringService()
    monitor_run = service.create_monitor_run(
        db,
        request=request,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        status="queued",
        operator=operator,
    )
    async_result = enqueue_operator_strategy_monitor(
        request=request,
        monitor_run_id=monitor_run.id,
        trigger_source=StrategyMonitoringService.ASYNC_TRIGGER_SOURCE,
        operator_id=operator.id,
    )
    monitor_run = service.update_monitor_run_task_id(db, run_id=monitor_run.id, task_id=async_result.id) or monitor_run
    status_payload = get_operator_strategy_monitor_task_status(async_result.id)
    return {
        "task_id": async_result.id,
        "monitor_run_id": monitor_run.id,
        "operator_id": operator.id,
        **_operator_context_fields(operator),
        "task_name": status_payload["task_name"],
        "status": status_payload["status"],
        "detail": status_payload["detail"],
        "poll_url": f"/api/v1/operator/strategy/monitor/tasks/{async_result.id}",
    }


def get_monitor_status_impl(task_id: str, operator: User, db: Session) -> dict:
    payload = get_operator_strategy_monitor_task_status(task_id)

    monitor_run = (
        db.query(OperatorStrategyRun)
        .filter(OperatorStrategyRun.task_id == task_id)
        .order_by(OperatorStrategyRun.id.desc())
        .first()
    )
    if monitor_run is None and payload.get("monitor_run_id") is not None:
        monitor_run = (
            db.query(OperatorStrategyRun)
            .filter(OperatorStrategyRun.id == int(payload["monitor_run_id"]))
            .first()
        )

    result = payload.get("result")
    result_operator_id = None
    if isinstance(result, dict) and result.get("operator_id") is not None:
        result_operator_id = int(result["operator_id"])

    resolved_owner_id = int(monitor_run.operator_id) if monitor_run is not None else result_operator_id
    if resolved_owner_id is not None and int(resolved_owner_id) != int(operator.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Monitoring task not found")

    payload["operator_id"] = int(operator.id)
    payload.update(_operator_context_fields(operator))
    return payload
