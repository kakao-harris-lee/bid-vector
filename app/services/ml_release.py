"""Helpers for promoting trained ML artifacts into runtime and embedding flows."""

from __future__ import annotations

import json
import os
import hashlib
import hmac
import shutil
import subprocess
import tarfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib import error, parse, request

from sqlalchemy.orm import Session

from app.ai.predictors.ensemble import load_ensemble_artifact
from app.ai.predictors.lstm import load_lstm_artifact
from app.core.config import settings
from app.core.time import utc_now


class RemoteObjectStorageClient:
    """Small object-storage adapter for file:// and optional S3 release storage."""

    def __init__(self, base_url: str | None) -> None:
        self.base_url = str(base_url or "").strip()
        self.parsed = parse.urlparse(self.base_url)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def put_file(self, source_path: str | Path, *, object_name: str) -> dict[str, Any]:
        """Upload one local file under the configured object prefix."""
        if not self.enabled:
            return {"enabled": False, "uri": None, "object_name": object_name}

        source = Path(source_path).resolve()
        if not source.is_file():
            raise ValueError(f"Object storage upload source must be a file: {source}")

        scheme = self.parsed.scheme.lower()
        if scheme in {"", "file"}:
            destination = self._local_destination(object_name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return {
                "enabled": True,
                "provider": "file",
                "object_name": object_name,
                "uri": destination.as_uri(),
                "bytes": destination.stat().st_size,
            }
        if scheme == "s3":
            return self._put_s3(source, object_name=object_name)
        raise ValueError(f"Unsupported ML_RELEASE_OBJECT_STORAGE_URL scheme: {scheme or 'path'}")

    def _local_destination(self, object_name: str) -> Path:
        """Resolve a local object-storage destination."""
        if self.parsed.scheme == "file":
            base_path = Path(parse.unquote(self.parsed.path))
        else:
            base_path = Path(self.base_url)
        return (base_path / object_name).resolve()

    def _put_s3(self, source: Path, *, object_name: str) -> dict[str, Any]:
        """Upload one object to S3 when boto3 is available at runtime."""
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on deployment extras
            raise RuntimeError("boto3 is required when ML_RELEASE_OBJECT_STORAGE_URL uses s3://") from exc

        bucket = self.parsed.netloc
        prefix = self.parsed.path.strip("/")
        key = f"{prefix}/{object_name}".strip("/")
        boto3.client("s3").upload_file(str(source), bucket, key)
        return {
            "enabled": True,
            "provider": "s3",
            "object_name": object_name,
            "uri": f"s3://{bucket}/{key}",
            "bytes": source.stat().st_size,
        }


@dataclass(frozen=True, slots=True)
class MLReleasePromotionRequest:
    """Validated inputs for one manifest-backed ML release promotion."""

    release_tag: str
    embedding_model_path: str | None = None
    lstm_artifact_path: str | None = None
    ensemble_artifact_path: str | None = None
    git_sha: str | None = None
    notes: str | None = None
    rebuild_limit: int = 100
    rebuild_offset: int = 0
    category: str | None = None
    project_status: str | None = None
    force_rebuild: bool = True
    publish_remote: bool = False


class MLReleasePromotionService:
    """Create, validate, and apply manifest-backed ML artifact promotions."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        self.repo_root = self.repo_root.resolve()

    def create_release_manifest(self, request: MLReleasePromotionRequest) -> dict[str, Any]:
        """Validate provided artifacts and persist one release manifest."""
        release_tag = self._normalize_release_tag(request.release_tag)
        embedding_metadata = self._validate_embedding_model_path(request.embedding_model_path)
        lstm_metadata = self._validate_lstm_artifact(request.lstm_artifact_path)
        ensemble_metadata = self._validate_ensemble_artifact(request.ensemble_artifact_path)

        if embedding_metadata is None and lstm_metadata is None and ensemble_metadata is None:
            raise ValueError("At least one embedding or predictor artifact path must be provided.")

        auto_lstm_path = None
        if lstm_metadata is None and ensemble_metadata is not None:
            auto_lstm_path = ensemble_metadata.get("resolved_lstm_artifact_path")

        recommended_env: dict[str, Any] = {}
        if embedding_metadata is not None:
            recommended_env["CLASSIFIER_EMBEDDING_MODEL"] = embedding_metadata["path"]
            recommended_env["CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY"] = True

        effective_lstm_path = lstm_metadata["path"] if lstm_metadata is not None else auto_lstm_path
        if effective_lstm_path or ensemble_metadata is not None:
            recommended_env["PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS"] = True
        if effective_lstm_path:
            recommended_env["PRICE_PREDICTION_LSTM_MODEL_PATH"] = effective_lstm_path
        if ensemble_metadata is not None:
            recommended_env["PRICE_PREDICTION_ENSEMBLE_MODEL_PATH"] = ensemble_metadata["path"]

        manifest = {
            "manifest_schema_version": "2",
            "release_tag": release_tag,
            "git_sha": str(request.git_sha).strip() or None,
            "validated_on": utc_now().isoformat(),
            "notes": str(request.notes).strip() or None,
            "recommended_docker_target": "api-embedding" if embedding_metadata is not None else "api-runtime",
            "artifacts": {
                "embedding_model": embedding_metadata,
                "predictors": {
                    "lstm": lstm_metadata,
                    "ensemble": ensemble_metadata,
                },
            },
            "recommended_env": recommended_env,
            "rebuild": {
                "recommended_force": bool(request.force_rebuild),
                "default_limit": int(request.rebuild_limit),
                "default_offset": int(request.rebuild_offset),
                "default_category": request.category,
                "default_project_status": request.project_status,
                "task_endpoint": "/api/v1/ml/backfills/project-embeddings",
            },
            "storage_policy": self._build_storage_policy(),
        }
        manifest["signature"] = self._sign_manifest(manifest)

        manifest_path = self._manifest_path_for_tag(release_tag)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["manifest_path"] = str(manifest_path)
        manifest["retention"] = self.enforce_manifest_retention(current_manifest_path=manifest_path)
        if request.publish_remote or settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH:
            manifest["remote_storage"] = self.publish_release_manifest(manifest_path)
        return manifest

    def load_release_manifest(self, manifest_ref: str | Path) -> tuple[dict[str, Any], Path]:
        """Load one persisted release manifest by path or release tag."""
        manifest_path = self._resolve_manifest_path(manifest_ref)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"Release manifest was not found: {manifest_path}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"Release manifest is not valid JSON: {manifest_path}") from exc

        if not isinstance(manifest, dict):
            raise ValueError(f"Release manifest must decode to a JSON object: {manifest_path}")
        self.verify_release_manifest(manifest, manifest_path=manifest_path)
        return manifest, manifest_path

    def verify_release_manifest(self, manifest: dict[str, Any], *, manifest_path: Path | None = None) -> dict[str, Any]:
        """Verify a manifest signature when present or required by policy."""
        signature = manifest.get("signature")
        if not isinstance(signature, dict):
            if settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE:
                location = f": {manifest_path}" if manifest_path is not None else ""
                raise ValueError(f"Release manifest is missing a required signature{location}")
            return {"verified": False, "reason": "signature_missing"}

        expected = self._sign_manifest({key: value for key, value in manifest.items() if key != "signature"})
        if not hmac.compare_digest(str(signature.get("digest") or ""), str(expected.get("digest") or "")):
            location = f": {manifest_path}" if manifest_path is not None else ""
            raise ValueError(f"Release manifest signature verification failed{location}")
        return {
            "verified": True,
            "algorithm": signature.get("algorithm"),
            "key_id": signature.get("key_id"),
            "payload_sha256": signature.get("payload_sha256"),
        }

    def apply_release_manifest(
        self,
        db: Session | None,
        *,
        manifest_ref: str | Path,
        rebuild_embeddings: bool = False,
        limit: int | None = None,
        offset: int | None = None,
        category: str | None = None,
        project_status: str | None = None,
        force: bool | None = None,
    ) -> dict[str, Any]:
        """Load a manifest, surface its runtime settings, and optionally rebuild embeddings."""
        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})

        response: dict[str, Any] = {
            "release_tag": manifest.get("release_tag"),
            "manifest_path": str(manifest_path),
            "recommended_docker_target": manifest.get("recommended_docker_target"),
            "recommended_env": recommended_env,
            "rebuild_requested": rebuild_embeddings,
            "rebuild_result": None,
        }

        if not rebuild_embeddings:
            return response
        if db is None:
            raise ValueError("A database session is required when rebuild_embeddings=True.")
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError("The selected manifest does not include a local embedding model path to rebuild from.")

        rebuild_settings = self._resolve_rebuild_settings(
            manifest,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
        )

        temporary_settings = self._manifest_settings_overrides(recommended_env)

        try:
            from app.services.project_similarity import ProjectSimilarityService

            with self._temporary_settings(temporary_settings), self._temporary_working_directory(self.repo_root):
                rebuild_result = ProjectSimilarityService().rebuild_project_embeddings(
                    db,
                    limit=int(rebuild_settings["limit"]),
                    offset=int(rebuild_settings["offset"]),
                    category=rebuild_settings["category"],
                    project_status=rebuild_settings["project_status"],
                    force=bool(rebuild_settings["force"]),
                )
                db.commit()
        except Exception:
            db.rollback()
            raise

        response["rebuild_result"] = rebuild_result
        response["rebuild_settings"] = rebuild_settings
        return response

    def build_manifest_env_updates(
        self,
        manifest_ref: str | Path,
        *,
        include_docker_target: bool = True,
    ) -> dict[str, str]:
        """Build a stringified env mapping from one stored release manifest."""
        manifest, _ = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})

        env_updates = {
            key: self._stringify_env_value(value)
            for key, value in recommended_env.items()
        }
        if include_docker_target:
            docker_target = str(manifest.get("recommended_docker_target") or "").strip()
            if docker_target:
                env_updates["API_DOCKER_TARGET"] = docker_target
        return env_updates

    def write_manifest_env_file(
        self,
        manifest_ref: str | Path,
        *,
        env_file_path: str | Path = ".env",
        include_docker_target: bool = True,
    ) -> dict[str, Any]:
        """Apply one manifest's recommended env values into a dotenv-style file."""
        env_updates = self.build_manifest_env_updates(
            manifest_ref,
            include_docker_target=include_docker_target,
        )
        target_path = Path(env_file_path)
        if not target_path.is_absolute():
            target_path = self.repo_root / target_path
        target_path = target_path.resolve()
        file_previously_existed = target_path.exists()

        existing_lines = target_path.read_text(encoding="utf-8").splitlines() if file_previously_existed else []
        updated_lines: list[str] = []
        applied_keys: list[str] = []

        for line in existing_lines:
            key = self._parse_env_key(line)
            if key is not None and key in env_updates:
                updated_lines.append(f"{key}={env_updates[key]}")
                applied_keys.append(key)
            else:
                updated_lines.append(line)

        missing_keys = [key for key in env_updates if key not in applied_keys]
        if missing_keys and updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        for key in missing_keys:
            updated_lines.append(f"{key}={env_updates[key]}")

        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")

        return {
            "env_file_path": str(target_path),
            "created": not file_previously_existed,
            "updated_keys": list(env_updates.keys()),
            "applied_env": env_updates,
        }

    def publish_release_manifest(self, manifest_ref: str | Path) -> dict[str, Any]:
        """Publish a signed manifest and referenced artifacts to configured object storage."""
        client = RemoteObjectStorageClient(settings.ML_RELEASE_OBJECT_STORAGE_URL)
        if not client.enabled:
            return {
                "enabled": False,
                "detail": "ML_RELEASE_OBJECT_STORAGE_URL is not configured.",
                "objects": [],
            }

        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        release_tag = str(manifest.get("release_tag") or manifest_path.stem)
        objects = [
            client.put_file(manifest_path, object_name=f"manifests/{manifest_path.name}"),
        ]

        for artifact in self._iter_manifest_artifact_paths(manifest):
            artifact_path = self._resolve_portable_path(artifact["path"])
            if artifact_path.is_dir():
                bundle_path = self._archive_artifact_directory(
                    artifact_path,
                    release_tag=release_tag,
                    artifact_key=artifact["key"],
                )
                object_name = f"artifacts/{release_tag}/{artifact['key']}.tar.gz"
                objects.append(client.put_file(bundle_path, object_name=object_name))
            elif artifact_path.is_file():
                object_name = f"artifacts/{release_tag}/{artifact['key']}/{artifact_path.name}"
                objects.append(client.put_file(artifact_path, object_name=object_name))

        return {
            "enabled": True,
            "base_url": settings.ML_RELEASE_OBJECT_STORAGE_URL,
            "release_tag": release_tag,
            "object_count": len(objects),
            "objects": objects,
        }

    def enforce_manifest_retention(self, *, current_manifest_path: Path | None = None) -> dict[str, Any]:
        """Archive older manifests according to the configured local retention limit."""
        retention_limit = int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0)
        manifest_dir = self._manifest_dir()
        archive_dir = self._manifest_archive_dir()
        if retention_limit <= 0 or not manifest_dir.exists():
            return {
                "enabled": retention_limit > 0,
                "retention_limit": retention_limit,
                "archived_count": 0,
                "archived_paths": [],
            }

        current_path = current_manifest_path.resolve() if current_manifest_path is not None else None
        candidates = [
            path
            for path in manifest_dir.glob("*.json")
            if path.is_file() and (current_path is None or path.resolve() != current_path)
        ]
        candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
        archive_candidates = candidates[max(retention_limit - 1, 0):]
        archived_paths: list[str] = []
        if archive_candidates:
            archive_dir.mkdir(parents=True, exist_ok=True)

        for path in archive_candidates:
            destination = archive_dir / path.name
            if destination.exists():
                destination = archive_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
            path.rename(destination)
            archived_paths.append(str(destination))

        return {
            "enabled": True,
            "retention_limit": retention_limit,
            "archived_count": len(archived_paths),
            "archived_paths": archived_paths,
        }

    def restart_compose_services(
        self,
        *,
        services: list[str] | None = None,
        build: bool = True,
    ) -> dict[str, Any]:
        """Restart one or more compose services from the repository root."""
        resolved_services = [service for service in (services or ["api"]) if str(service or "").strip()]
        command = ["docker", "compose", "up", "-d"]
        if build:
            command.append("--build")
        command.extend(resolved_services)

        completed = subprocess.run(
            command,
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "docker compose restart failed with exit code "
                f"{completed.returncode}.\nSTDOUT:\n{completed.stdout.strip()}\nSTDERR:\n{completed.stderr.strip()}"
            )

        return {
            "command": " ".join(command),
            "services": resolved_services,
            "build": build,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    def wait_for_http_ready(
        self,
        *,
        url: str,
        timeout_seconds: float = 120.0,
        interval_seconds: float = 1.0,
    ) -> dict[str, Any]:
        """Poll one HTTP endpoint until it responds successfully or times out."""
        started_at = time.monotonic()
        last_error: str | None = None

        while True:
            try:
                with request.urlopen(url, timeout=max(1.0, min(timeout_seconds, 10.0))) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    return {
                        "url": url,
                        "status_code": int(response.getcode()),
                        "body": body,
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                    }
            except Exception as exc:  # pragma: no cover - exercised via timeout/error branches in runtime use
                last_error = str(exc)

            if time.monotonic() - started_at >= timeout_seconds:
                raise RuntimeError(
                    f"Timed out waiting for HTTP readiness at {url} after {timeout_seconds:.1f}s. "
                    f"Last error: {last_error or 'unknown'}"
                )
            time.sleep(interval_seconds)

    def trigger_remote_embedding_rebuild(
        self,
        manifest_ref: str | Path,
        *,
        base_url: str = "http://localhost:8000",
        limit: int | None = None,
        offset: int | None = None,
        category: str | None = None,
        project_status: str | None = None,
        force: bool | None = None,
        timeout_seconds: float = 120.0,
    ) -> dict[str, Any]:
        """Trigger the API-based embedding rebuild endpoint using one stored manifest's defaults."""
        manifest, _ = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError("The selected manifest does not include an embedding model, so remote rebuild is unavailable.")

        rebuild_settings = self._resolve_rebuild_settings(
            manifest,
            limit=limit,
            offset=offset,
            category=category,
            project_status=project_status,
            force=force,
        )
        query = {
            "limit": int(rebuild_settings["limit"]),
            "offset": int(rebuild_settings["offset"]),
            "force": "true" if rebuild_settings["force"] else "false",
        }
        if rebuild_settings["category"]:
            query["category"] = str(rebuild_settings["category"])
        if rebuild_settings["project_status"]:
            query["project_status"] = str(rebuild_settings["project_status"])

        target_url = f"{str(base_url).rstrip('/')}/api/v1/ml/backfills/project-embeddings?{parse.urlencode(query)}"
        request_object = request.Request(target_url, method="POST")
        try:
            with request.urlopen(request_object, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed_body = json.loads(body) if body.strip() else None
                return {
                    "url": target_url,
                    "status_code": int(response.getcode()),
                    "response": parsed_body,
                    "rebuild_settings": rebuild_settings,
                }
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Remote embedding rebuild failed with HTTP {exc.code} at {target_url}: {error_body}"
            ) from exc

    def _build_storage_policy(self) -> dict[str, Any]:
        """Describe the release manifest storage and retention policy."""
        return {
            "local_manifest_dir": self._to_portable_path(self._manifest_dir()),
            "local_archive_dir": self._to_portable_path(self._manifest_archive_dir()),
            "retention_limit": int(settings.ML_RELEASE_MANIFEST_RETENTION_LIMIT or 0),
            "remote_storage_configured": bool(settings.ML_RELEASE_OBJECT_STORAGE_URL),
            "remote_auto_publish": bool(settings.ML_RELEASE_REMOTE_STORAGE_AUTO_PUBLISH),
        }

    def _sign_manifest(self, manifest_without_signature: dict[str, Any]) -> dict[str, Any]:
        """Build a deterministic HMAC signature for one manifest payload."""
        canonical_payload = self._canonical_manifest_payload(manifest_without_signature)
        signing_key = self._manifest_signing_key()
        digest = hmac.new(signing_key.encode("utf-8"), canonical_payload, hashlib.sha256).hexdigest()
        return {
            "algorithm": "HMAC-SHA256",
            "key_id": settings.ML_RELEASE_MANIFEST_SIGNING_KEY_ID,
            "signed_at": utc_now().isoformat(),
            "payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
            "digest": digest,
        }

    def _canonical_manifest_payload(self, manifest_without_signature: dict[str, Any]) -> bytes:
        """Serialize a manifest without incidental whitespace for signing."""
        return json.dumps(
            manifest_without_signature,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

    def _manifest_signing_key(self) -> str:
        """Resolve the configured manifest signing key with a safe development fallback."""
        configured_key = str(settings.ML_RELEASE_MANIFEST_SIGNING_KEY or "").strip()
        if configured_key:
            return configured_key
        if settings.ENVIRONMENT == "production":
            raise ValueError("ML_RELEASE_MANIFEST_SIGNING_KEY is required in production.")
        return str(settings.JWT_SECRET_KEY or "development-manifest-signing-key")

    def _iter_manifest_artifact_paths(self, manifest: dict[str, Any]) -> list[dict[str, str]]:
        """Return artifact path references from a release manifest."""
        artifacts = manifest.get("artifacts") or {}
        results: list[dict[str, str]] = []
        embedding_model = artifacts.get("embedding_model")
        if isinstance(embedding_model, dict) and embedding_model.get("path"):
            results.append({"key": "embedding_model", "path": str(embedding_model["path"])})

        predictors = artifacts.get("predictors") if isinstance(artifacts, dict) else {}
        if isinstance(predictors, dict):
            for predictor_key in ("lstm", "ensemble"):
                predictor = predictors.get(predictor_key)
                if isinstance(predictor, dict) and predictor.get("path"):
                    results.append({"key": predictor_key, "path": str(predictor["path"])})
                if predictor_key == "ensemble" and isinstance(predictor, dict) and predictor.get("resolved_lstm_artifact_path"):
                    results.append({"key": "linked_lstm", "path": str(predictor["resolved_lstm_artifact_path"])})
        return results

    def _archive_artifact_directory(self, path: Path, *, release_tag: str, artifact_key: str) -> Path:
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

    def _manifest_settings_overrides(self, recommended_env: dict[str, Any]) -> dict[str, Any]:
        """Map one manifest's env recommendation into in-process setting overrides."""
        overrides = {
            "ENABLE_SEMANTIC_CLASSIFICATION": True,
            "CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY": bool(
                recommended_env.get("CLASSIFIER_EMBEDDING_LOCAL_FILES_ONLY", True)
            ),
        }

        if settings.ENVIRONMENT == "test":
            overrides["ENVIRONMENT"] = "development"
        for key in (
            "CLASSIFIER_EMBEDDING_MODEL",
            "PRICE_PREDICTION_ENABLE_EXPERIMENTAL_PREDICTORS",
            "PRICE_PREDICTION_LSTM_MODEL_PATH",
            "PRICE_PREDICTION_ENSEMBLE_MODEL_PATH",
        ):
            if key in recommended_env:
                overrides[key] = recommended_env[key]
        return overrides

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
        if not stripped_line or stripped_line.startswith("#") or "=" not in stripped_line:
            return None
        key, _, _ = stripped_line.partition("=")
        normalized_key = key.strip()
        return normalized_key or None

    def _resolve_rebuild_settings(
        self,
        manifest: dict[str, Any],
        *,
        limit: int | None,
        offset: int | None,
        category: str | None,
        project_status: str | None,
        force: bool | None,
    ) -> dict[str, Any]:
        """Resolve rebuild settings by combining manifest defaults with explicit overrides."""
        rebuild_defaults = manifest.get("rebuild") or {}
        return {
            "limit": int(limit if limit is not None else rebuild_defaults.get("default_limit", 100)),
            "offset": int(offset if offset is not None else rebuild_defaults.get("default_offset", 0)),
            "category": category if category is not None else rebuild_defaults.get("default_category"),
            "project_status": (
                project_status if project_status is not None else rebuild_defaults.get("default_project_status")
            ),
            "force": bool(force if force is not None else rebuild_defaults.get("recommended_force", True)),
        }

    def _validate_embedding_model_path(self, raw_path: str | None) -> dict[str, Any] | None:
        """Validate one local sentence-transformer snapshot directory."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=True)
        top_level_entries = list(path.iterdir())
        if not top_level_entries:
            raise ValueError(f"Embedding model directory is empty: {path}")

        config_candidates = sorted(
            entry.name
            for entry in top_level_entries
            if entry.is_file() and entry.suffix.lower() == ".json"
        )
        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "directory_name": path.name,
            "entry_count": len(top_level_entries),
            "config_files": config_candidates,
            "integrity": self._path_integrity_metadata(path),
        }

    def _validate_lstm_artifact(self, raw_path: str | None) -> dict[str, Any] | None:
        """Validate one persisted LSTM JSON artifact."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        artifact = load_lstm_artifact(path)
        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "artifact_version": artifact["artifact_version"],
            "model_version": artifact["model_version"],
            "sequence_length": int(artifact["sequence_length"]),
            "integrity": self._path_integrity_metadata(path),
        }

    def _validate_ensemble_artifact(self, raw_path: str | None) -> dict[str, Any] | None:
        """Validate one persisted ensemble JSON artifact and its optional LSTM linkage."""
        if not raw_path:
            return None
        path = self._resolve_existing_path(raw_path, expect_directory=False)
        artifact = load_ensemble_artifact(path)
        resolved_lstm_artifact_path = None
        raw_lstm_artifact_path = str(artifact.get("lstm_artifact_path") or "").strip() or None

        if raw_lstm_artifact_path:
            dependent_path = self._resolve_dependency_path(raw_lstm_artifact_path, base_path=path.parent)
            load_lstm_artifact(dependent_path)
            resolved_lstm_artifact_path = self._to_portable_path(dependent_path)

        return {
            "path": self._to_portable_path(path),
            "path_kind": self._path_kind(path),
            "artifact_version": artifact["artifact_version"],
            "model_version": artifact["model_version"],
            "sequence_length": int(artifact["sequence_length"]),
            "momentum_window": int(artifact["momentum_window"]),
            "has_embedded_lstm": isinstance(artifact.get("lstm_artifact"), dict),
            "resolved_lstm_artifact_path": resolved_lstm_artifact_path,
            "integrity": self._path_integrity_metadata(path),
        }

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
