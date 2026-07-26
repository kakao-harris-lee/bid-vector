"""Declarative verdict / lifecycle / status transition tables.

The ordered, first-match rule machines consumed by ``DecisionExperimentService``:
``_build_evaluation`` reads ``_VERDICT_RULES``, ``evaluate_run`` reads
``_EVALUATION_LIFECYCLE_RULES``, and ``update_run`` reads
``_UPDATE_STATUS_EFFECTS``. These dataclasses and tables are the original
module-level definitions, relocated verbatim, so behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.models.models import DecisionExperimentRun


@dataclass(frozen=True)
class _VerdictContext:
    """Pre-computed inputs consumed by the verdict rule predicates and summaries."""

    run: DecisionExperimentRun
    sample_size: int
    minimum_decision_sample: int
    minimum_sample_reached: bool
    guardrail_broken: bool
    metric_improved: bool
    period_elapsed: bool


@dataclass(frozen=True)
class _VerdictRule:
    """One first-match verdict rule: a predicate plus its declarative outcome."""

    predicate: Callable[[_VerdictContext], bool]
    outcome: str
    recommended_action: str
    summary_builder: Callable[[_VerdictContext], str]


# Ordered, first-match verdict machine. The improved branch is split into an
# "improved + period elapsed" success rule and an "improved" watch rule so every
# rule keeps a static outcome/action and a verbatim summary template.
_VERDICT_RULES: tuple[_VerdictRule, ...] = (
    _VerdictRule(
        predicate=lambda ctx: not ctx.minimum_sample_reached,
        outcome="insufficient_data",
        recommended_action="collect_more_data",
        summary_builder=lambda ctx: (
            f"현재 표본 {ctx.sample_size}건으로는 실험 판단이 이릅니다. "
            f"최소 {ctx.minimum_decision_sample}건이 쌓일 때까지 더 수집하세요."
        ),
    ),
    _VerdictRule(
        predicate=lambda ctx: ctx.guardrail_broken,
        outcome="rollback",
        recommended_action="rollback",
        summary_builder=lambda ctx: (
            f"가드레일 지표 `{ctx.run.guardrail_metric}`가 기준 대비 악화되었습니다. "
            f"현재 변경안을 롤백하고 원인을 점검하는 편이 안전합니다."
        ),
    ),
    _VerdictRule(
        predicate=lambda ctx: ctx.metric_improved and ctx.period_elapsed,
        outcome="success",
        recommended_action="complete",
        summary_builder=lambda ctx: (
            f"목표 지표 `{ctx.run.target_metric}`가 기준 대비 개선되었습니다. "
            f"현재 추세를 유지하며 실험을 종료하세요."
        ),
    ),
    _VerdictRule(
        predicate=lambda ctx: ctx.metric_improved,
        outcome="watch",
        recommended_action="continue",
        summary_builder=lambda ctx: (
            f"목표 지표 `{ctx.run.target_metric}`가 기준 대비 개선되었습니다. "
            f"현재 추세를 유지하며 추가 표본을 수집하세요."
        ),
    ),
    _VerdictRule(
        predicate=lambda ctx: ctx.period_elapsed,
        outcome="inconclusive",
        recommended_action="complete",
        summary_builder=lambda ctx: (
            f"예정된 실험 기간은 종료되었지만 `{ctx.run.target_metric}` 개선이 충분하지 않았습니다. "
            f"결과를 기록하고 다음 가설로 넘어가는 편이 좋습니다."
        ),
    ),
    _VerdictRule(
        predicate=lambda ctx: True,
        outcome="watch",
        recommended_action="continue",
        summary_builder=lambda ctx: (
            f"아직 목표 지표 `{ctx.run.target_metric}` 개선 폭이 충분하지 않습니다. "
            f"기간 종료 전까지 추이를 더 관찰하세요."
        ),
    ),
)


@dataclass(frozen=True)
class _LifecycleContext:
    """Inputs consumed by the ``evaluate_run`` lifecycle transition rules."""

    recommended_action: str
    now: datetime
    run_started_at: datetime
    scheduled_end: datetime


@dataclass(frozen=True)
class _LifecycleRule:
    """One first-match lifecycle rule mapping a verdict+timing to a run status."""

    predicate: Callable[[_LifecycleContext], bool]
    status: str
    # ``None`` leaves ``ended_at`` untouched; otherwise it resolves the new value.
    ended_at: Callable[[_LifecycleContext], datetime] | None


# Ordered, first-match lifecycle machine for auto-evaluation. Only the rollback
# and completed rules touch ``ended_at``; running/planned leave it unchanged.
_EVALUATION_LIFECYCLE_RULES: tuple[_LifecycleRule, ...] = (
    _LifecycleRule(
        predicate=lambda ctx: ctx.recommended_action == "rollback",
        status="rolled_back",
        ended_at=lambda ctx: ctx.now,
    ),
    _LifecycleRule(
        predicate=lambda ctx: ctx.now >= ctx.scheduled_end,
        status="completed",
        ended_at=lambda ctx: ctx.scheduled_end,
    ),
    _LifecycleRule(
        predicate=lambda ctx: ctx.run_started_at <= ctx.now,
        status="running",
        ended_at=None,
    ),
    _LifecycleRule(
        predicate=lambda ctx: True,
        status="planned",
        ended_at=None,
    ),
)


@dataclass(frozen=True)
class _StatusEffect:
    """Side-effects applied by ``update_run`` for one manual target status."""

    # ``None`` defers to the request outcome; otherwise the outcome is forced.
    forced_outcome: str | None
    # One of ``"set"`` (request.ended_at or now), ``"clear"`` (None), ``"keep"``.
    ended_at: str


# Manual status → side-effect table. Statuses are constrained by the request
# schema to {planned, running, completed, rolled_back}; the default keeps the
# prior ``ended_at`` for defensive parity with the original branch fallthrough.
_UPDATE_STATUS_EFFECTS: dict[str, _StatusEffect] = {
    "rolled_back": _StatusEffect(forced_outcome="rollback", ended_at="set"),
    "completed": _StatusEffect(forced_outcome=None, ended_at="set"),
    "planned": _StatusEffect(forced_outcome=None, ended_at="clear"),
    "running": _StatusEffect(forced_outcome=None, ended_at="clear"),
}
_DEFAULT_STATUS_EFFECT = _StatusEffect(forced_outcome=None, ended_at="keep")
