"""Composed :class:`SyntheticExperimentService` (CRUD + run lifecycle)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .service_crud import ExperimentCrudMixin
from .service_lifecycle import RunLifecycleMixin
from .service_sample_gap import SampleGapPlanningMixin
from .service_sample_gap_candidate import SampleGapCandidateMixin
from .service_serialization import RunSerializationMixin


class SyntheticExperimentService(
    ExperimentCrudMixin,
    SampleGapPlanningMixin,
    SampleGapCandidateMixin,
    RunLifecycleMixin,
    RunSerializationMixin,
):
    """CRUD + run lifecycle for synthetic experiments."""

    def __init__(self, db: Session) -> None:
        self.db = db
