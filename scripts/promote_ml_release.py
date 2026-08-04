"""CLI helpers for creating and applying ML release manifests."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydantic import ValidationError  # noqa: E402 - follows the sys.path bootstrap

from app.ai.predictors.artifact_contracts import (  # noqa: E402
    StrictArtifactSummaryDocument,
    artifact_contract_error_kind,
    artifact_contract_error_location,
)
from app.services.ml_release import (  # noqa: E402
    MLReleasePromotionRequest,
    MLReleasePromotionService,
)


@dataclass
class PreflightResult:
    ok: bool
    reason: str | None = None


def evaluate_preflight_gate(manifest_path: "Path | str") -> PreflightResult:
    """manifest.summary.group_calibration의 각 그룹 sample_count를 확인해
    settings.GROUP_CALIBRATION_MIN_SAMPLES 미달 시 거부.

    읽기는 predictor 아티팩트와 같은 계약을 쓰지만 **엄격 변형**
    (``StrictArtifactSummaryDocument``)을 쓴다: 게이트에서 추론 경로의 관용을 쓰면 손상된
    ``summary``/표가 "블록 없음"으로 접혀 거부가 통과로 바뀐다. 게이트는 거부가 안전한
    방향이므로 위반을 접지 않고 fail-closed 로 떨어뜨리고, 사유에 **위반 위치**를 담는다
    (블록 자체가 없는 아티팩트는 종전처럼 통과).
    """
    from app.core.config import settings

    path = Path(manifest_path)
    try:
        document = StrictArtifactSummaryDocument.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValidationError as exc:
        if artifact_contract_error_kind(exc) == "json":
            return PreflightResult(
                ok=False, reason=f"manifest 읽기 실패: {exc.errors()[0].get('msg')}"
            )
        location = artifact_contract_error_location(exc)
        if not location:
            return PreflightResult(ok=False, reason="manifest가 JSON 객체가 아님")
        return PreflightResult(
            ok=False,
            reason=f"manifest 계약 위반({location}): {exc.errors()[0].get('msg')}",
        )
    except Exception as exc:
        return PreflightResult(ok=False, reason=f"manifest 읽기 실패: {exc}")

    calibration = document.summary.group_calibration if document.summary else {}
    if not calibration:
        # 그룹 캘리브레이션 블록이 없으면 gate를 통과시킴 (선택적 블록)
        return PreflightResult(ok=True)

    threshold = int(settings.GROUP_CALIBRATION_MIN_SAMPLES or 0)
    failing = [
        (group, int((stats or {}).get("sample_count") or 0))
        for group, stats in calibration.items()
        if int((stats or {}).get("sample_count") or 0) < threshold
    ]
    if failing:
        detail = ", ".join(f"{g}={n}" for g, n in failing)
        return PreflightResult(
            ok=False,
            reason=f"group_calibration sample_count<{threshold}: {detail}",
        )
    return PreflightResult(ok=True)


def _clean_optional(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _build_parser() -> argparse.ArgumentParser:
    from app.core.config import settings

    parser = argparse.ArgumentParser(
        description="Create or apply manifest-backed ML artifact promotions.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create-manifest",
        help="Validate artifacts and write a release manifest under models/manifests/.",
    )
    create_parser.add_argument(
        "--release-tag",
        required=True,
        help="Release tag used for the manifest filename.",
    )
    create_parser.add_argument(
        "--embedding-model-path",
        default="",
        help="Local embedding model snapshot directory.",
    )
    create_parser.add_argument(
        "--lstm-artifact-path", default="", help="Persisted LSTM artifact JSON path."
    )
    create_parser.add_argument(
        "--ensemble-artifact-path",
        default="",
        help="Persisted ensemble artifact JSON path.",
    )
    create_parser.add_argument(
        "--predictor-backtest-report",
        default="",
        help="Optional predictor backtest report JSON used for release promotion gating.",
    )
    create_parser.add_argument(
        "--git-sha", default="", help="Git SHA recorded in the manifest."
    )
    create_parser.add_argument(
        "--notes", default="", help="Optional operator note stored in the manifest."
    )
    create_parser.add_argument(
        "--rebuild-limit",
        type=int,
        default=100,
        help="Suggested default rebuild batch size.",
    )
    create_parser.add_argument(
        "--rebuild-offset",
        type=int,
        default=0,
        help="Suggested default rebuild offset.",
    )
    create_parser.add_argument(
        "--category", default="", help="Suggested default project category filter."
    )
    create_parser.add_argument(
        "--project-status", default="", help="Suggested default project status filter."
    )
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

    preflight_parser = subparsers.add_parser(
        "preflight-rollout",
        help="Check manifest signature/artifacts and object-storage IAM before rollout.",
    )
    preflight_parser.add_argument(
        "--manifest",
        required=True,
        help="Release tag or direct path to the manifest JSON file.",
    )
    preflight_parser.add_argument(
        "--require-signature",
        action="store_true",
        help="Validate as if ML_RELEASE_MANIFEST_REQUIRE_SIGNATURE=true.",
    )
    preflight_parser.add_argument(
        "--no-write-probe",
        action="store_true",
        help="Skip the object-storage write/delete probe.",
    )
    preflight_parser.add_argument(
        "--production",
        action="store_true",
        help="Require a passing checksummed predictor backtest for production rollout.",
    )
    preflight_parser.add_argument(
        "--expected-git-sha",
        default="",
        help="Deployment git SHA that the signed manifest must match.",
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
        default=f"{settings.ML_RELEASE_REMOTE_TRIGGER_BASE_URL}/health",
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
        default=settings.ML_RELEASE_REMOTE_TRIGGER_BASE_URL,
        help="Base URL for the running API when using --rebuild-embeddings-via-api.",
    )
    apply_parser.add_argument(
        "--publish-remote",
        action="store_true",
        help="Publish the signed manifest and artifacts to ML_RELEASE_OBJECT_STORAGE_URL.",
    )
    apply_parser.add_argument(
        "--skip-promotion-gate",
        action="store_true",
        help="Bypass predictor promotion-gate failure checks when applying the manifest.",
    )
    apply_parser.add_argument(
        "--limit", type=int, default=None, help="Override rebuild batch size."
    )
    apply_parser.add_argument(
        "--offset", type=int, default=None, help="Override rebuild offset."
    )
    apply_parser.add_argument(
        "--category", default=None, help="Override rebuild category filter."
    )
    apply_parser.add_argument(
        "--project-status", default=None, help="Override rebuild status filter."
    )
    force_group = apply_parser.add_mutually_exclusive_group()
    force_group.add_argument(
        "--force", action="store_true", help="Force an embedding rebuild."
    )
    force_group.add_argument(
        "--no-force", action="store_true", help="Reuse cached embeddings when possible."
    )

    return parser


def _serialize_payload(payload: dict[str, Any]) -> str:
    # CLI 표준출력 포맷팅은 ``json.dumps`` 를 유지한다: payload 는 서비스가 조립한 자유형식
    # 결과(manifest·preflight·remote 결과의 합집합)이고 ``default=str`` 폴백에 의존한다.
    # pydantic 직렬화로 바꾸면 직렬화 불가 값에서 폴백 대신 경고/변형이 발생한다.
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def _remote_storage_failed(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("status") in {
        "failed",
        "not_configured",
    }


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
                predictor_backtest_report_path=_clean_optional(
                    args.predictor_backtest_report
                ),
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
        return 2 if _remote_storage_failed(payload.get("remote_storage")) else 0

    if args.command == "preflight-rollout":
        payload = service.preflight_release_rollout(
            args.manifest,
            require_signature=True if args.require_signature else None,
            probe_write=not bool(args.no_write_probe),
            production=bool(args.production),
            expected_git_sha=_clean_optional(args.expected_git_sha),
        )
        print(_serialize_payload(payload))
        return 0 if payload.get("passed") else 2

    force_override = None
    if args.force:
        force_override = True
    elif args.no_force:
        force_override = False

    if not args.rebuild_embeddings:
        remote_storage_failed = False
        payload = service.apply_release_manifest(
            None,
            manifest_ref=args.manifest,
            rebuild_embeddings=False,
            skip_promotion_gate=bool(args.skip_promotion_gate),
        )
        if args.write_env_file:
            payload["env_file_update"] = service.write_manifest_env_file(
                args.manifest,
                env_file_path=args.write_env_file,
            )
        if args.publish_remote:
            payload["remote_storage"] = service.publish_release_manifest(args.manifest)
            remote_storage_failed = _remote_storage_failed(payload["remote_storage"])
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
        return 2 if remote_storage_failed else 0

    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        remote_storage_failed = False
        payload = service.apply_release_manifest(
            db,
            manifest_ref=args.manifest,
            rebuild_embeddings=True,
            limit=args.limit,
            offset=args.offset,
            category=_clean_optional(args.category),
            project_status=_clean_optional(args.project_status),
            force=force_override,
            skip_promotion_gate=bool(args.skip_promotion_gate),
        )
        if args.write_env_file:
            payload["env_file_update"] = service.write_manifest_env_file(
                args.manifest,
                env_file_path=args.write_env_file,
            )
        if args.publish_remote:
            payload["remote_storage"] = service.publish_release_manifest(args.manifest)
            remote_storage_failed = _remote_storage_failed(payload["remote_storage"])
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
        return 2 if remote_storage_failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
