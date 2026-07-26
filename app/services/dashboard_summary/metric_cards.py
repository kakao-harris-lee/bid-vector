"""Operational status + paper backtest metric card serializers."""

from __future__ import annotations

from app.models.models import OperatorStrategyRun, PaperBidRun


def _serialize_operational_status(latest_run: OperatorStrategyRun | None) -> dict:
    if latest_run is None:
        return {
            "key": "operator_strategy",
            "label": "운영 상태",
            "value": "no_run",
            "unit": "state",
            "status": "info",
            "detail": "아직 전략 모니터링 실행 기록이 없습니다.",
        }

    run_status = str(latest_run.status or "queued")
    if run_status == "completed":
        status_value = "healthy"
        detail = "최근 전략 모니터링이 정상 완료되었습니다."
    elif run_status in {"queued", "running"}:
        status_value = "watch"
        detail = "전략 모니터링 작업이 진행 중입니다."
    elif run_status == "failed":
        status_value = "critical"
        detail = latest_run.error_message or "최근 전략 모니터링이 실패했습니다."
    else:
        status_value = "info"
        detail = f"최근 전략 모니터링 상태: {run_status}"

    return {
        "key": "operator_strategy",
        "label": "운영 상태",
        "value": run_status,
        "unit": "state",
        "status": status_value,
        "detail": detail,
    }


def _serialize_paper_backtest_metric(latest_run: PaperBidRun | None) -> dict:
    if latest_run is None:
        return {
            "key": "paper_backtest",
            "label": "페이퍼 검증",
            "value": 0,
            "unit": "count",
            "status": "info",
            "detail": "아직 저장된 paper bidding 실행이 없습니다.",
        }

    run_status = str(latest_run.status or "unknown")
    if run_status == "completed":
        status_value = "healthy" if int(latest_run.paper_bid_count or 0) > 0 else "info"
    elif run_status == "failed":
        status_value = "critical"
    else:
        status_value = "watch"

    detail = (
        f"{latest_run.mode} 실행에서 후보 {int(latest_run.candidate_count or 0)}건, "
        f"가상 투찰 {int(latest_run.paper_bid_count or 0)}건, 정산 {int(latest_run.settled_count or 0)}건을 기록했습니다."
    )
    if latest_run.error_message:
        detail = latest_run.error_message
    return {
        "key": "paper_backtest",
        "label": "페이퍼 검증",
        "value": int(latest_run.paper_bid_count or 0),
        "unit": "count",
        "status": status_value,
        "detail": detail,
    }
