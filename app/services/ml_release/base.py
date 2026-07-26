"""Shared foundation for manifest-backed ML release promotion.

Holds the ``MLReleasePromotionRequest`` value object plus ``_MLReleaseBase``:
the promotion-gate presets, dataset-quality ordering, ``__init__`` and the
stateless leaf helpers (path resolution, sha256, env parsing, archive/probe
builders). Every member is moved verbatim from the original module."""

from __future__ import annotations

import os
import hashlib
import tarfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.core.config import settings


@dataclass(frozen=True, slots=True)
class MLReleasePromotionRequest:
    """Validated inputs for one manifest-backed ML release promotion."""

    release_tag: str
    embedding_model_path: str | None = None
    lstm_artifact_path: str | None = None
    ensemble_artifact_path: str | None = None
    predictor_backtest_report_path: str | None = None
    git_sha: str | None = None
    notes: str | None = None
    rebuild_limit: int = 100
    rebuild_offset: int = 0
    category: str | None = None
    project_status: str | None = None
    force_rebuild: bool = True
    publish_remote: bool = False


class _MLReleaseBase:
    """Class constants, ``__init__`` and stateless leaf helpers shared by
    every ``MLReleasePromotionService`` mixin. Method bodies are the original
    ``MLReleasePromotionService`` members, relocated verbatim."""

    PREDICTOR_GATE_POLICY_PRESETS: dict[str, dict[str, Any]] = {
        "advisory": {
            "label": "Advisory rollout",
            "require_report": False,
            "min_sample_count": 1,
            "max_average_absolute_error_rate": 0.06,
            "max_guardrail_rate": 0.5,
            "max_fallback_rate": 0.5,
            "min_dataset_quality_status": "failed",
            "block_on_missing_dataset_quality": False,
        },
        "canary": {
            "label": "Canary rollout",
            "require_report": True,
            "min_sample_count": 3,
            "max_average_absolute_error_rate": 0.04,
            "max_guardrail_rate": 0.35,
            "max_fallback_rate": 0.35,
            "min_dataset_quality_status": "warning",
            "block_on_missing_dataset_quality": False,
        },
        "standard": {
            "label": "Standard rollout",
            "require_report": None,
            "min_sample_count": None,
            "max_average_absolute_error_rate": None,
            "max_guardrail_rate": None,
            "max_fallback_rate": None,
            "min_dataset_quality_status": "warning",
            "block_on_missing_dataset_quality": False,
        },
        "strict": {
            "label": "Strict rollout",
            "require_report": True,
            "min_sample_count": 10,
            "max_average_absolute_error_rate": 0.02,
            "max_guardrail_rate": 0.15,
            "max_fallback_rate": 0.1,
            "min_dataset_quality_status": "passed",
            "block_on_missing_dataset_quality": True,
        },
    }

    DATASET_QUALITY_ORDER = {
        "failed": 0,
        "warning": 1,
        "passed": 2,
    }

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = (
            Path(repo_root)
            if repo_root is not None
            else Path(__file__).resolve().parents[3]
        )
        self.repo_root = self.repo_root.resolve()

    def _first_int(self, *values: Any) -> int:
        """Return the first value that can be interpreted as a non-negative integer."""
        for value in values:
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    def _first_float(self, *values: Any) -> float | None:
        """Return the first value that can be interpreted as a float."""
        for value in values:
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _iter_manifest_artifact_paths(
        self, manifest: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Return artifact path references from a release manifest."""
        artifacts = manifest.get("artifacts") or {}
        results: list[dict[str, str]] = []
        embedding_model = artifacts.get("embedding_model")
        if isinstance(embedding_model, dict) and embedding_model.get("path"):
            results.append(
                {"key": "embedding_model", "path": str(embedding_model["path"])}
            )

        predictors = artifacts.get("predictors") if isinstance(artifacts, dict) else {}
        if isinstance(predictors, dict):
            for predictor_key in ("lstm", "ensemble"):
                predictor = predictors.get(predictor_key)
                if isinstance(predictor, dict) and predictor.get("path"):
                    results.append(
                        {"key": predictor_key, "path": str(predictor["path"])}
                    )
                if (
                    predictor_key == "ensemble"
                    and isinstance(predictor, dict)
                    and predictor.get("resolved_lstm_artifact_path")
                ):
                    results.append(
                        {
                            "key": "linked_lstm",
                            "path": str(predictor["resolved_lstm_artifact_path"]),
                        }
                    )
        return results

    def _archive_artifact_directory(
        self, path: Path, *, release_tag: str, artifact_key: str
    ) -> Path:
        """Create a tarball for one directory artifact before object-storage upload."""
        bundle_dir = self._manifest_dir() / "bundles"
        bundle_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = bundle_dir / f"{release_tag}-{artifact_key}.tar.gz"
        with tarfile.open(bundle_path, "w:gz") as tar:
            tar.add(path, arcname=path.name)
        return bundle_path

    def _path_integrity_metadata(self, path: Path) -> dict[str, Any]:
        """Return deterministic integrity metadata for a file or directory artifact."""
        if path.is_file():
            return {
                "checksum_algorithm": "sha256",
                "sha256": self._sha256_file(path),
                "bytes": path.stat().st_size,
            }
        if path.is_dir():
            file_paths = sorted(item for item in path.rglob("*") if item.is_file())
            digest = hashlib.sha256()
            total_bytes = 0
            for file_path in file_paths:
                relative_name = file_path.relative_to(path).as_posix()
                digest.update(relative_name.encode("utf-8"))
                digest.update(b"\0")
                file_digest = self._sha256_file(file_path)
                digest.update(file_digest.encode("ascii"))
                total_bytes += file_path.stat().st_size
            return {
                "checksum_algorithm": "sha256-tree",
                "sha256": digest.hexdigest(),
                "bytes": total_bytes,
                "file_count": len(file_paths),
            }
        raise ValueError(f"Artifact path must be a file or directory: {path}")

    def _rollout_check(
        self,
        name: str,
        passed: bool,
        status: str,
        detail: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Build one rollout preflight check payload."""
        return {
            "name": name,
            "passed": bool(passed),
            "status": status,
            "detail": detail,
            **extra,
        }

    def _sha256_file(self, path: Path) -> str:
        """Hash one file without loading it fully into memory."""
        digest = hashlib.sha256()
        with path.open("rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _manifest_dir(self) -> Path:
        """Return the configured manifest directory."""
        configured_path = Path(settings.ML_RELEASE_MANIFEST_DIR)
        if not configured_path.is_absolute():
            configured_path = self.repo_root / configured_path
        return configured_path.resolve()

    def _manifest_archive_dir(self) -> Path:
        """Return the configured manifest archive directory."""
        configured_path = Path(settings.ML_RELEASE_MANIFEST_ARCHIVE_DIR)
        if not configured_path.is_absolute():
            configured_path = self.repo_root / configured_path
        return configured_path.resolve()

    def _stringify_env_value(self, value: Any) -> str:
        """Render manifest values into dotenv-friendly strings."""
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return ""
        return str(value)

    def _parse_env_key(self, line: str) -> str | None:
        """Extract an env key from one dotenv line while ignoring comments and blanks."""
        stripped_line = str(line or "").strip()
        if (
            not stripped_line
            or stripped_line.startswith("#")
            or "=" not in stripped_line
        ):
            return None
        key, _, _ = stripped_line.partition("=")
        normalized_key = key.strip()
        return normalized_key or None

    def _normalize_release_tag(self, value: str) -> str:
        """Normalize and validate one manifest tag for safe filesystem usage."""
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("release_tag is required.")
        if any(token in normalized for token in ("/", "\\", "..")):
            raise ValueError("release_tag must not contain path separators or '..'.")
        return normalized

    def _manifest_path_for_tag(self, release_tag: str) -> Path:
        """Return the canonical manifest file path for one release tag."""
        return self._manifest_dir() / f"{release_tag}.json"

    def _resolve_manifest_path(self, manifest_ref: str | Path) -> Path:
        """Resolve either a direct manifest path or a release tag into a file path."""
        raw_value = str(manifest_ref).strip()
        if not raw_value:
            raise ValueError("manifest_ref is required.")

        candidate = Path(raw_value)
        if candidate.suffix.lower() == ".json" or candidate.exists():
            if not candidate.is_absolute():
                candidate = (self.repo_root / candidate).resolve()
            return candidate
        return self._manifest_path_for_tag(self._normalize_release_tag(raw_value))

    def _resolve_existing_path(self, raw_path: str, *, expect_directory: bool) -> Path:
        """Resolve one repo-relative or absolute path and ensure it exists."""
        path = self._resolve_portable_path(raw_path)
        if not path.exists():
            raise ValueError(f"Artifact path was not found: {path}")
        if expect_directory and not path.is_dir():
            raise ValueError(f"Expected a directory but found a file: {path}")
        if not expect_directory and not path.is_file():
            raise ValueError(f"Expected a file but found a directory: {path}")
        return path

    def _resolve_dependency_path(self, raw_path: str, *, base_path: Path) -> Path:
        """Resolve one nested artifact path relative to its parent file or the repository root."""
        candidate = Path(str(raw_path).strip())
        if candidate.is_absolute():
            if not candidate.exists():
                raise ValueError(f"Dependent artifact path was not found: {candidate}")
            return candidate.resolve()

        relative_to_parent = (base_path / candidate).resolve()
        if relative_to_parent.exists():
            return relative_to_parent

        relative_to_repo = (self.repo_root / candidate).resolve()
        if relative_to_repo.exists():
            return relative_to_repo

        raise ValueError(
            f"Dependent artifact path was not found relative to '{base_path}' or repo root: {raw_path}"
        )

    def _resolve_portable_path(self, raw_path: str) -> Path:
        """Resolve one manifest-friendly path string against the repository root."""
        candidate = Path(str(raw_path).strip())
        if not candidate.is_absolute():
            candidate = self.repo_root / candidate
        return candidate.resolve()

    def _to_portable_path(self, path: Path) -> str:
        """Prefer repo-relative paths inside manifests when possible."""
        resolved_path = path.resolve()
        try:
            return resolved_path.relative_to(self.repo_root).as_posix()
        except ValueError:
            return str(resolved_path)

    def _path_kind(self, path: Path) -> str:
        """Describe whether a manifest path is repo-relative or absolute."""
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(self.repo_root)
            return "repo-relative"
        except ValueError:
            return "absolute"

    @contextmanager
    def _temporary_settings(self, overrides: dict[str, Any]) -> Iterator[None]:
        """Temporarily override shared settings while applying one release manifest."""
        previous_values = {key: getattr(settings, key) for key in overrides}
        try:
            for key, value in overrides.items():
                setattr(settings, key, value)
            yield
        finally:
            for key, value in previous_values.items():
                setattr(settings, key, value)

    @contextmanager
    def _temporary_working_directory(self, path: Path) -> Iterator[None]:
        """Temporarily run inside the repository root so relative model paths resolve correctly."""
        previous_cwd = Path.cwd()
        try:
            os.chdir(path)
            yield
        finally:
            os.chdir(previous_cwd)

