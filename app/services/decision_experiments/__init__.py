"""Persist and evaluate decision-tuning experiments.

Public surface. ``DecisionExperimentService`` keeps its historical import path
(``from app.services.decision_experiments import DecisionExperimentService``).
The service body was decomposed into responsibility mixins (base / evaluation /
lifecycle / serialization / application) plus the declarative verdict, lifecycle,
and status transition tables in ``verdict_machine``; this ``__init__`` composes
them. The split is a pure move — every method body is the original
``DecisionExperimentService`` method, relocated verbatim, so behaviour is
unchanged.
"""

from __future__ import annotations

from app.services.decision_experiments.application import _ApplicationMixin
from app.services.decision_experiments.evaluation import _EvaluationMixin
from app.services.decision_experiments.lifecycle import _LifecycleMixin
from app.services.decision_experiments.serialization import _SerializationMixin

__all__ = ["DecisionExperimentService"]


class DecisionExperimentService(
    _LifecycleMixin,
    _EvaluationMixin,
    _SerializationMixin,
    _ApplicationMixin,
):
    """Manage saved experiment plans and compare their performance against a baseline."""
