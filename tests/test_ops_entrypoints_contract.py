"""운영 진입점(Makefile · shell)이 파이썬 계약과 어긋나지 않는지 고정한다.

왜 이 파일이 필요한가 — LSTM predictor 은퇴(2026-08-09) 때 소비자 전수 조사를
``app/``·``scripts/*.py`` 로만 돌려서 **파이썬 밖 소비자 2곳을 놓쳤다**:

* ``Makefile:ml-release-manifest`` 가 삭제된 ``--lstm-artifact-path`` 를 계속 넘겨
  argparse hard-fail(exit 2). ``make help`` 에 광고된 타깃인데 CI 는 초록이었다.
* ``scripts/train_price_predictor_and_verify.sh`` 가 더 이상 생성되지 않는 lstm
  아티팩트를 필수 산출로 요구해 **성공한 훈련마다 검증 실패**.

둘 다 테스트가 Makefile/.sh 를 보지 않아서 통과했다. 여기서 그 두 경계를 계약으로
고정한다 — 개별 플래그 이름을 하드코딩하지 않고, **진입점이 선언한 것**과 **파이썬이
실제로 받아들이는 것/내보내는 것**을 대조한다.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = REPO_ROOT / "Makefile"
TRAIN_VERIFY_SH = REPO_ROOT / "scripts" / "train_price_predictor_and_verify.sh"


def _load_promote_module():
    """``scripts/promote_ml_release.py`` 를 모듈로 적재(패키지가 아니라 경로 적재).

    ``sys.modules`` 등록이 exec 보다 **먼저**여야 한다 — 스크립트 안의 ``@dataclass``
    가 정의 시점에 자기 모듈을 ``sys.modules`` 에서 되찾기 때문이다.
    """
    path = REPO_ROOT / "scripts" / "promote_ml_release.py"
    spec = importlib.util.spec_from_file_location("_promote_ml_release_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _makefile_recipe(target: str) -> str:
    """한 타깃의 레시피 본문(탭으로 시작하는 줄)을 잇는다."""
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith(f"{target}:"):
            inside = True
            continue
        if inside:
            if line.startswith("\t"):
                collected.append(line.strip())
                continue
            if line.strip() == "":
                continue
            break
    assert collected, f"Makefile 타깃을 찾지 못했다: {target}"
    return " ".join(collected)


def _subcommand_option_strings(parser, subcommand: str) -> set[str]:
    subparsers_actions = [
        action
        for action in parser._actions  # noqa: SLF001 - argparse 내성 검사 전용
        if isinstance(action, argparse._SubParsersAction)  # noqa: SLF001
    ]
    for action in subparsers_actions:
        if subcommand in action.choices:
            sub = action.choices[subcommand]
            options: set[str] = set()
            for sub_action in sub._actions:  # noqa: SLF001
                options.update(sub_action.option_strings)
            return options
    raise AssertionError(f"subcommand 없음: {subcommand}")


def test_makefile_release_target_only_passes_flags_the_cli_accepts():
    """``make ml-release-manifest`` 가 넘기는 모든 플래그를 argparse 가 받아야 한다.

    argparse 는 미지 플래그에 hard-fail 하므로, 하나만 어긋나도 타깃 전체가 죽는다.
    """
    recipe = _makefile_recipe("ml-release-manifest")
    passed_flags = set(re.findall(r"(?<!\w)(--[a-z0-9][a-z0-9-]*)", recipe))
    accepted = _subcommand_option_strings(_load_promote_module()._build_parser(), "create-manifest")

    unknown = sorted(passed_flags - accepted)
    assert not unknown, (
        f"Makefile 이 CLI 가 모르는 플래그를 넘긴다 (argparse hard-fail): {unknown}"
    )


def _shell_required_artifact_keys() -> set[str]:
    """검증 스크립트가 '완료된 훈련이 반드시 내놓아야 한다'고 보는 키들."""
    text = TRAIN_VERIFY_SH.read_text(encoding="utf-8")
    # ("<key>", repo_root / ...) 형태의 존재 검사 목록
    tuple_keys = set(re.findall(r'\(\s*"([a-z_]+_path)"\s*,\s*repo_root', text))
    # base_required_keys = [...] / required_keys.append('...') 형태
    quoted_keys = set(re.findall(r"'([a-z_]+_path)'", text))
    return tuple_keys | quoted_keys


def test_train_verify_script_requires_no_retired_artifact():
    """은퇴한 predictor 산출을 필수로 요구하면 성공한 훈련이 항상 실패한다."""
    required = _shell_required_artifact_keys()

    assert "lstm_artifact_path" not in required
    assert required, "필수 키 파싱이 비었다 — 정규식이 스크립트와 어긋났다"


def test_train_verify_script_requirements_match_training_output(tmp_path):
    """스크립트의 필수 키 ⊆ 훈련 서비스가 실제로 내보내는 키.

    이 대조가 없으면 서비스 payload 를 줄일 때 shell 쪽이 조용히 스테일해진다
    (은퇴 라운드에서 실제로 일어난 일).
    """
    from app.services.ml_training.constants import TrainingRunPaths

    required = _shell_required_artifact_keys()
    # manifest_path 는 create_manifest 일 때만 요구되며 릴리스 서비스가 만든다.
    required.discard("manifest_path")

    emitted = set(TrainingRunPaths.__dataclass_fields__)
    # 결과 payload 는 경로 필드를 그대로 키로 싣는다(_completed_training_result).
    missing = sorted(key for key in required if key not in emitted)
    assert not missing, (
        f"검증 스크립트가 훈련이 만들지 않는 산출을 요구한다: {missing}"
    )


@pytest.mark.parametrize(
    "path",
    [MAKEFILE, TRAIN_VERIFY_SH],
    ids=["Makefile", "train_price_predictor_and_verify.sh"],
)
def test_ops_entrypoints_do_not_reference_the_retired_predictor(path: Path):
    """파이썬 밖 진입점에 은퇴 predictor 참조가 남지 않는다(주석 제외)."""
    offending = [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if "lstm" in line.lower() and not line.strip().startswith("#")
    ]

    assert not offending, f"{path.name} 에 은퇴 predictor 참조가 남아 있다: {offending}"
