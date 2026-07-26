"""Composed :class:`PricePredictionTrainingService` (training pipeline mixins)."""

from __future__ import annotations

from pathlib import Path

from .artifacts import ArtifactBuilderMixin
from .calibration import CalibrationMixin
from .comparison import ComparisonMixin
from .dataset_quality import DatasetQualityMixin
from .helpers import HelpersMixin
from .orchestration import OrchestrationMixin


class PricePredictionTrainingService(
    OrchestrationMixin,
    CalibrationMixin,
    DatasetQualityMixin,
    ComparisonMixin,
    ArtifactBuilderMixin,
    HelpersMixin,
):
    """Build lightweight predictor artifacts from historical bid-rate data."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        # NOTE: ``parents`` index is one deeper than the pre-decomposition module
        # because this file now lives in ``app/services/ml_training/`` (a package)
        # instead of ``app/services/ml_training.py``. ``parents[3]`` still resolves
        # to the repo root, preserving the default (repo_root=None) behavior used by
        # the production Celery training task (``app.tasks.jobs.train_price_predictor``).
        self.repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[3]
        self.repo_root = self.repo_root.resolve()
