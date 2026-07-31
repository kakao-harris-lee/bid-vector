"""Rollout execution: apply/publish/retention, compose restart, remote rebuild."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from pydantic import JsonValue, ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.ml_release.base import _MLReleaseBase
from app.services.ml_release.contracts import (
    MLReleaseJsonValueDocument,
    json_document_error_detail,
)
from app.services.ml_release.storage import RemoteObjectStorageClient


class _RolloutMixin(_MLReleaseBase):
    """Apply and publish manifests and drive compose/embedding rollout side effects."""

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
        skip_promotion_gate: bool = False,
    ) -> dict[str, Any]:
        """Load a manifest, surface its runtime settings, and optionally rebuild embeddings."""
        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})
        promotion_gate = self._resolve_manifest_promotion_gate(manifest)
        if not skip_promotion_gate and not bool(promotion_gate.get("passed")):
            reasons = "; ".join(
                str(reason) for reason in promotion_gate.get("reasons", [])
            )
            raise ValueError(
                f"Release manifest failed predictor promotion gate: {reasons or 'unknown failure'}"
            )

        response: dict[str, Any] = {
            "release_tag": manifest.get("release_tag"),
            "manifest_path": str(manifest_path),
            "recommended_docker_target": manifest.get("recommended_docker_target"),
            "recommended_env": recommended_env,
            "promotion_gate": promotion_gate,
            "rebuild_requested": rebuild_embeddings,
            "rebuild_result": None,
        }

        if not rebuild_embeddings:
            return response
        if db is None:
            raise ValueError(
                "A database session is required when rebuild_embeddings=True."
            )
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError(
                "The selected manifest does not include a local embedding model path to rebuild from."
            )

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

            with self._temporary_settings(
                temporary_settings
            ), self._temporary_working_directory(self.repo_root):
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

    def publish_release_manifest(self, manifest_ref: str | Path) -> dict[str, Any]:
        """Publish a signed manifest and referenced artifacts to configured object storage."""
        client = RemoteObjectStorageClient(settings.ML_RELEASE_OBJECT_STORAGE_URL)
        if not client.enabled:
            return {
                "enabled": False,
                "status": "not_configured",
                "detail": "ML_RELEASE_OBJECT_STORAGE_URL is not configured.",
                "objects": [],
            }

        preflight = self.preflight_release_rollout(
            manifest_ref,
            require_signature=settings.ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE,
            probe_write=True,
        )
        if not preflight["passed"]:
            reasons = "; ".join(
                str(reason) for reason in preflight.get("failure_reasons", []) if reason
            )
            return {
                "enabled": True,
                "status": "failed",
                "base_url": client.describe().get("base_url"),
                "release_tag": preflight.get("manifest", {}).get("release_tag"),
                "preflight": preflight,
                "object_count": 0,
                "objects": [],
                "failure_reasons": list(preflight.get("failure_reasons", [])),
                "detail": f"Release rollout preflight failed: {reasons or 'unknown failure'}",
            }

        manifest, manifest_path = self.load_release_manifest(manifest_ref)
        release_tag = str(manifest.get("release_tag") or manifest_path.stem)
        objects: list[dict[str, Any]] = []

        try:
            objects.append(
                client.put_file(
                    manifest_path, object_name=f"manifests/{manifest_path.name}"
                )
            )
            for artifact in self._iter_manifest_artifact_paths(manifest):
                artifact_path = self._resolve_portable_path(artifact["path"])
                if artifact_path.is_dir():
                    bundle_path = self._archive_artifact_directory(
                        artifact_path,
                        release_tag=release_tag,
                        artifact_key=artifact["key"],
                    )
                    object_name = f"artifacts/{release_tag}/{artifact['key']}.tar.gz"
                    objects.append(
                        client.put_file(bundle_path, object_name=object_name)
                    )
                elif artifact_path.is_file():
                    object_name = f"artifacts/{release_tag}/{artifact['key']}/{artifact_path.name}"
                    objects.append(
                        client.put_file(artifact_path, object_name=object_name)
                    )
        except Exception as exc:
            return {
                "enabled": True,
                "status": "failed",
                "base_url": settings.ML_RELEASE_OBJECT_STORAGE_URL,
                "release_tag": release_tag,
                "preflight": preflight,
                "object_count": len(objects),
                "objects": objects,
                "failure_reasons": [str(exc)],
                "detail": f"Release object upload failed: {exc}",
            }

        return {
            "enabled": True,
            "status": "passed",
            "base_url": settings.ML_RELEASE_OBJECT_STORAGE_URL,
            "release_tag": release_tag,
            "preflight": preflight,
            "object_count": len(objects),
            "objects": objects,
        }

    def enforce_manifest_retention(
        self, *, current_manifest_path: Path | None = None
    ) -> dict[str, Any]:
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

        current_path = (
            current_manifest_path.resolve()
            if current_manifest_path is not None
            else None
        )
        candidates = [
            path
            for path in manifest_dir.glob("*.json")
            if path.is_file()
            and (current_path is None or path.resolve() != current_path)
        ]
        candidates.sort(
            key=lambda path: (path.stat().st_mtime, path.name), reverse=True
        )
        archive_candidates = candidates[max(retention_limit - 1, 0) :]
        archived_paths: list[str] = []
        if archive_candidates:
            archive_dir.mkdir(parents=True, exist_ok=True)

        for path in archive_candidates:
            destination = archive_dir / path.name
            if destination.exists():
                destination = (
                    archive_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
                )
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
        resolved_services = [
            service for service in (services or ["api"]) if str(service or "").strip()
        ]
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
                with request.urlopen(
                    url,
                    timeout=max(
                        1.0,
                        min(
                            timeout_seconds,
                            settings.ML_RELEASE_HTTP_READY_PER_REQUEST_CAP_SECONDS,
                        ),
                    ),
                ) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    return {
                        "url": url,
                        "status_code": int(response.getcode()),
                        "body": body,
                        "elapsed_seconds": round(time.monotonic() - started_at, 2),
                    }
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised via timeout/error branches in runtime use
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
        base_url: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        category: str | None = None,
        project_status: str | None = None,
        force: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Trigger the API-based embedding rebuild endpoint using one stored manifest's defaults."""
        if base_url is None:
            base_url = settings.ML_RELEASE_REMOTE_TRIGGER_BASE_URL
        if timeout_seconds is None:
            timeout_seconds = settings.ML_RELEASE_REMOTE_TRIGGER_TIMEOUT_SECONDS
        manifest, _ = self.load_release_manifest(manifest_ref)
        recommended_env = dict(manifest.get("recommended_env") or {})
        if not recommended_env.get("CLASSIFIER_EMBEDDING_MODEL"):
            raise ValueError(
                "The selected manifest does not include an embedding model, so remote rebuild is unavailable."
            )

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
                parsed_body = self._parse_remote_trigger_body(body, url=target_url)
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

    @staticmethod
    def _parse_remote_trigger_body(body: str, *, url: str) -> JsonValue:
        """Decode the remote rebuild response body, preserving it verbatim.

        Reached **after** the remote rebuild has already been triggered, which sets
        the failure policy: a decodable body is echoed back as ``response`` exactly
        as before — including a JSON value that is not an object — because raising
        here would destroy the only record that the rebuild was kicked off. Only a
        body that cannot be decoded at all is rejected, and then with the URL
        attached instead of a bare ``JSONDecodeError``. An empty body stays ``None``
        (the endpoint may legitimately answer with no content).
        """
        if not body.strip():
            return None
        try:
            return MLReleaseJsonValueDocument.model_validate_json(body).root
        except ValidationError as exc:
            raise RuntimeError(
                "Remote embedding rebuild returned a body that is not valid JSON "
                f"at {url}: {json_document_error_detail(exc)}"
            ) from exc

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
            "limit": int(
                limit
                if limit is not None
                else rebuild_defaults.get("default_limit", 100)
            ),
            "offset": int(
                offset
                if offset is not None
                else rebuild_defaults.get("default_offset", 0)
            ),
            "category": (
                category
                if category is not None
                else rebuild_defaults.get("default_category")
            ),
            "project_status": (
                project_status
                if project_status is not None
                else rebuild_defaults.get("default_project_status")
            ),
            "force": bool(
                force
                if force is not None
                else rebuild_defaults.get("recommended_force", True)
            ),
        }

