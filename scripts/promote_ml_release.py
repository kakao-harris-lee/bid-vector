"""CLI helpers for creating and applying ML release manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.ml_release import MLReleasePromotionRequest, MLReleasePromotionService


def _clean_optional(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create or apply manifest-backed ML artifact promotions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-manifest",
        help="Validate artifacts and write a release manifest under models/manifests/.",
    )
    create_parser.add_argument("--release-tag", required=True, help="Release tag used for the manifest filename.")
    create_parser.add_argument("--embedding-model-path", default="", help="Local embedding model snapshot directory.")
    create_parser.add_argument("--lstm-artifact-path", default="", help="Persisted LSTM artifact JSON path.")
    create_parser.add_argument("--ensemble-artifact-path", default="", help="Persisted ensemble artifact JSON path.")
    create_parser.add_argument("--git-sha", default="", help="Git SHA recorded in the manifest.")
    create_parser.add_argument("--notes", default="", help="Optional operator note stored in the manifest.")
    create_parser.add_argument("--rebuild-limit", type=int, default=100, help="Suggested default rebuild batch size.")
    create_parser.add_argument("--rebuild-offset", type=int, default=0, help="Suggested default rebuild offset.")
    create_parser.add_argument("--category", default="", help="Suggested default project category filter.")
    create_parser.add_argument("--project-status", default="", help="Suggested default project status filter.")
    create_parser.add_argument(
        "--no-force-rebuild",
        action="store_true",
        help="Record force=false as the default rebuild recommendation.",
    )
    create_parser.add_argument(
        "--publish-remote",
        action="store_true",
        help="Publish the signed manifest and artifacts to ML_RELEASE_OBJECT_STORAGE_URL.",
    )

    apply_parser = subparsers.add_parser(
        "apply-manifest",
        help="Load a release manifest and optionally rebuild project embeddings from it.",
    )
    apply_parser.add_argument(
        "--manifest",
        required=True,
        help="Release tag or direct path to the manifest JSON file.",
    )
    rebuild_group = apply_parser.add_mutually_exclusive_group()
    rebuild_group.add_argument(
        "--rebuild-embeddings",
        action="store_true",
        help="Execute a local in-process embedding rebuild using the manifest's embedding model path.",
    )
    rebuild_group.add_argument(
        "--rebuild-embeddings-via-api",
        action="store_true",
        help="Call the running API's embedding rebuild endpoint after rollout.",
    )
    apply_parser.add_argument(
        "--write-env-file",
        default=None,
        help="Optional dotenv file to update with the manifest's recommended env values.",
    )
    apply_parser.add_argument(
        "--restart-compose",
        action="store_true",
        help="Run `docker compose up -d --build` for selected services after applying the manifest.",
    )
    apply_parser.add_argument(
        "--compose-service",
        action="append",
        default=None,
        help="Compose service to restart (repeatable). Defaults to `api` when --restart-compose is used.",
    )
    apply_parser.add_argument(
        "--no-compose-build",
        action="store_true",
        help="Restart compose services without `--build`.",
    )
    apply_parser.add_argument(
        "--wait-for-health-url",
        default="http://localhost:8000/health",
        help="HTTP endpoint polled after compose restart and before remote rebuild.",
    )
    apply_parser.add_argument(
        "--health-timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum seconds to wait for the health URL to respond successfully.",
    )
    apply_parser.add_argument(
        "--api-base-url",
        default="http://localhost:8000",
        help="Base URL for the running API when using --rebuild-embeddings-via-api.",
    )
    apply_parser.add_argument(
        "--publish-remote",
        action="store_true",
        help="Publish the signed manifest and artifacts to ML_RELEASE_OBJECT_STORAGE_URL.",
    )
    apply_parser.add_argument("--limit", type=int, default=None, help="Override rebuild batch size.")
    apply_parser.add_argument("--offset", type=int, default=None, help="Override rebuild offset.")
    apply_parser.add_argument("--category", default=None, help="Override rebuild category filter.")
    apply_parser.add_argument("--project-status", default=None, help="Override rebuild status filter.")
    force_group = apply_parser.add_mutually_exclusive_group()
    force_group.add_argument("--force", action="store_true", help="Force an embedding rebuild.")
    force_group.add_argument("--no-force", action="store_true", help="Reuse cached embeddings when possible.")

    return parser


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    service = MLReleasePromotionService()

    if args.command == "create-manifest":
        payload = service.create_release_manifest(
            MLReleasePromotionRequest(
                release_tag=args.release_tag,
                embedding_model_path=_clean_optional(args.embedding_model_path),
                lstm_artifact_path=_clean_optional(args.lstm_artifact_path),
                ensemble_artifact_path=_clean_optional(args.ensemble_artifact_path),
                git_sha=_clean_optional(args.git_sha),
                notes=_clean_optional(args.notes),
                rebuild_limit=int(args.rebuild_limit),
                rebuild_offset=int(args.rebuild_offset),
                category=_clean_optional(args.category),
                project_status=_clean_optional(args.project_status),
                force_rebuild=not bool(args.no_force_rebuild),
                publish_remote=bool(args.publish_remote),
            )
        )
        print(_serialize_payload(payload))
        return 0

    force_override = None
    if args.force:
        force_override = True
    elif args.no_force:
        force_override = False

    if not args.rebuild_embeddings:
        payload = service.apply_release_manifest(
            None,
            manifest_ref=args.manifest,
            rebuild_embeddings=False,
        )
        if args.write_env_file:
            payload["env_file_update"] = service.write_manifest_env_file(
                args.manifest,
                env_file_path=args.write_env_file,
            )
        if args.publish_remote:
            payload["remote_storage"] = service.publish_release_manifest(args.manifest)
        if args.restart_compose:
            payload["compose_restart"] = service.restart_compose_services(
                services=args.compose_service or ["api"],
                build=not bool(args.no_compose_build),
            )
            if args.wait_for_health_url:
                payload["health_check"] = service.wait_for_http_ready(
                    url=args.wait_for_health_url,
                    timeout_seconds=float(args.health_timeout_seconds),
                )
        elif args.rebuild_embeddings_via_api and args.wait_for_health_url:
            payload["health_check"] = service.wait_for_http_ready(
                url=args.wait_for_health_url,
                timeout_seconds=float(args.health_timeout_seconds),
            )

        if args.rebuild_embeddings_via_api:
            payload["remote_rebuild"] = service.trigger_remote_embedding_rebuild(
                args.manifest,
                base_url=args.api_base_url,
                limit=args.limit,
                offset=args.offset,
                category=_clean_optional(args.category),
                project_status=_clean_optional(args.project_status),
                force=force_override,
                timeout_seconds=float(args.health_timeout_seconds),
            )
        print(_serialize_payload(payload))
        return 0

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        payload = service.apply_release_manifest(
            db,
            manifest_ref=args.manifest,
            rebuild_embeddings=True,
            limit=args.limit,
            offset=args.offset,
            category=_clean_optional(args.category),
            project_status=_clean_optional(args.project_status),
            force=force_override,
        )
        if args.write_env_file:
            payload["env_file_update"] = service.write_manifest_env_file(
                args.manifest,
                env_file_path=args.write_env_file,
            )
        if args.publish_remote:
            payload["remote_storage"] = service.publish_release_manifest(args.manifest)
        if args.restart_compose:
            payload["compose_restart"] = service.restart_compose_services(
                services=args.compose_service or ["api"],
                build=not bool(args.no_compose_build),
            )
            if args.wait_for_health_url:
                payload["health_check"] = service.wait_for_http_ready(
                    url=args.wait_for_health_url,
                    timeout_seconds=float(args.health_timeout_seconds),
                )
        print(_serialize_payload(payload))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
