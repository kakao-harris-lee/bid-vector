#!/usr/bin/env python3
"""설계 래칫의 측정 코어 — 소스 텍스트에서 지표를 세는 AST 스캔.

지표를 담는 데이터 계약과 순수 비교는 ``scripts/_design_ratchet_contracts.py``, CLI ·
baseline 영속화 · 리포트 출력은 ``scripts/design_ratchet.py`` 에 있다. 파일/함수 크기
한도(CLAUDE.md §4.5-4)를 스캐너 자신도 지켜야 하므로 "측정"·"계약"·"표현/영속화"를
책임 단위로 나눴다.

여기에는 baseline 이라는 개념이 없다(파일 I/O 는 소스 읽기뿐). ``scan_source`` 는 순수
함수라 값 테이블로 테스트한다(§4.7-4).
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import ast
import math
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._design_ratchet_contracts import (  # noqa: E402
    FILE_LOC_BAND_LINES,
    FILE_LOC_SOFT_LIMIT,
    FileMetrics,
    RatchetReport,
)

# --- 선언적 구성 (§4.5-1: 임계값·대상·예외는 함수 밖에) ----------------------------
TARGET_DIRS: tuple[str, ...] = ("app", "scripts")

FUNCTION_SOFT_LIMIT_LINES = 50
FUNCTION_HARD_LIMIT_LINES = 100

WEAK_BOUNDARY_ANNOTATIONS = frozenset(
    {"dict", "Dict", "Any", "Mapping", "MutableMapping", "object"}
)
# ``object`` 는 ``Any`` 보다 더 약한 계약이라 같은 부류로 센다(무비용 우회 차단).
# ``dict[str, Any] | None`` / ``Optional[dict]`` 도 dict 경계다 — 래퍼는 벗기고 멤버
# 단위로 판정한다.
UNION_WRAPPER_ANNOTATIONS = frozenset({"Optional", "Union"})
# 컨테이너는 그 자체로 약하지 않지만 **안에 든 것**이 약하면 경계도 약하다.
# ``list[dict[str, Any]]`` 처럼 컨테이너로 한 겹 감싸 지표를 피하는 우회를 막는다.
CONTAINER_ANNOTATIONS = frozenset(
    {
        "list",
        "List",
        "set",
        "Set",
        "frozenset",
        "FrozenSet",
        "tuple",
        "Tuple",
        "Sequence",
        "MutableSequence",
        "Iterable",
        "Iterator",
        "Collection",
    }
)
JSON_MODULE_NAME = "json"
JSON_DIRECT_CALL_ATTRS = frozenset({"loads", "dumps", "load", "dump"})
# json 직접 호출이 정당한 파일. 추가 기준은 docs/operations/design-ratchet.md.
# signing.py 는 서명 대상 바이트 정합성 때문에 직렬화를 직접 통제해야 한다.
JSON_CALL_ALLOWLIST = frozenset({"app/services/ml_release/signing.py"})

ENVIRONMENT_ATTR_NAME = "ENVIRONMENT"
TEST_ENVIRONMENT_LITERAL = "test"
ENV_SNIFF_SCAN_DIR = "app"
# 속성 접근 말고 프로세스 환경을 직접 읽는 형태도 같은 스니핑이다.
ENVIRONMENT_MAPPING_NAMES = frozenset({"os.environ", "environ"})
ENVIRONMENT_LOOKUP_CALL_NAMES = frozenset(
    {"os.getenv", "getenv", "os.environ.get", "environ.get"}
)
# 멤버십 비교는 **리터럴 컨테이너**만 센다. ``NON_DELIVERING_ENVIRONMENTS`` 같은 선언
# 데이터 참조는 이 저장소가 의도적으로 채택한 패턴이라 스니핑이 아니다.
ENVIRONMENT_LITERAL_CONTAINERS = (ast.Tuple, ast.List, ast.Set)

CELERY_TASK_DECORATORS = frozenset({"celery_app.task", "shared_task"})
VALIDATION_PROMOTION_ATTRS = frozenset({"model_validate", "model_validate_json"})
# celery ``bind=True`` 의 self 와 메서드 수신자는 payload 가 아니라서 제외한다.
IMPLICIT_RECEIVER_ARG_NAMES = frozenset({"self", "cls"})
# 중첩 정의 본문은 task 가 실행한다는 보장이 없으므로 승격 탐색에서 제외한다.
DEFERRED_BODY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)

EXCLUDED_PATH_PARTS = frozenset({"__pycache__"})

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class RatchetScanError(RuntimeError):
    """스캔 대상 파일을 해석할 수 없음.

    조용히 건너뛰면 그 파일의 위반이 0 으로 보고되어 "삭제 = 통과" 경로로 사라지므로
    래칫이 샌다. 그래서 침묵하지 않고 크게 실패한다.
    """


# --- AST 해석 헬퍼 ---------------------------------------------------------------
def annotation_name(node: ast.expr | None) -> str | None:
    """어노테이션의 최상위 이름(``dict[str, int]`` → ``dict``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Subscript):
        return annotation_name(node.value)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("[")[0].strip()
    return None


def _subscript_arguments(node: ast.expr | None) -> Iterator[ast.expr]:
    if not isinstance(node, ast.Subscript):
        return
    sliced = node.slice
    yield from sliced.elts if isinstance(sliced, ast.Tuple) else (sliced,)


def is_weak_annotation(node: ast.expr | None) -> bool:
    """검증되지 않는 dict/Any 경계인가.

    - union/``Optional`` 래퍼는 벗겨서 멤버 단위로 판정한다.
    - bare ``dict``/``Dict``/``Any``/``Mapping``/``MutableMapping``/``object`` 는
      약한 경계다.
    - 첨자가 붙은 매핑은 **value 가 약할 때만** 약하다: ``dict[str, Any]``·
      ``dict[str, object]``·``dict[str, dict[str, Any]]`` 는 약하고,
      ``dict[str, ConcreteModel]``·``dict[str, str]`` 처럼 value 가 구체 모델이나
      스칼라면 검증되는 계약이라 면제한다.
    - 컨테이너(``list``/``tuple``/``Sequence`` …)는 그 자체로는 약하지 않지만 **안에
      든 것**이 약하면 약하다: ``list[dict[str, Any]]`` 는 약한 경계로 센다.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return is_weak_annotation(node.left) or is_weak_annotation(node.right)
    name = annotation_name(node)
    if name in UNION_WRAPPER_ANNOTATIONS or name in CONTAINER_ANNOTATIONS:
        return any(
            is_weak_annotation(argument) for argument in _subscript_arguments(node)
        )
    if name not in WEAK_BOUNDARY_ANNOTATIONS:
        return False
    if not isinstance(node, ast.Subscript):
        return True
    arguments = list(_subscript_arguments(node))
    return bool(arguments) and is_weak_annotation(arguments[-1])


def _all_arguments(node: _FunctionNode) -> list[ast.arg]:
    """``*args``/``**kwargs`` 까지 포함한 전체 인자(우회 경로 차단)."""
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    arguments.extend(
        extra for extra in (node.args.vararg, node.args.kwarg) if extra is not None
    )
    return arguments


def _has_weak_boundary(node: _FunctionNode) -> bool:
    if is_weak_annotation(node.returns):
        return True
    return any(
        is_weak_annotation(argument.annotation) for argument in _all_arguments(node)
    )


def _function_line_span(node: _FunctionNode) -> int:
    return (node.end_lineno or node.lineno) - node.lineno + 1


def _callable_name(node: ast.expr) -> str | None:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
        return f"{target.value.id}.{target.attr}"
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _is_celery_task(node: _FunctionNode) -> bool:
    names = {_callable_name(decorator) for decorator in node.decorator_list}
    return bool(names & CELERY_TASK_DECORATORS)


def _promotion_targets(node: ast.Call) -> list[ast.expr]:
    """승격 호출이 "승격하는 값" 들(승격 형태가 아니면 빈 목록).

    ``Model.model_validate(x)`` 은 **위치 인자**가, ``Model(**x)`` 은 splat 대상이 그
    값이다. keyword 인자는 대상이 아니다 — ``Other.model_validate(CONST, context=payload)``
    처럼 payload 를 부수 인자로만 스치고 면제받는 세탁을 막는다.
    ``schemas.Req(**payload)`` 같은 모듈 경로 호출도 마지막 세그먼트로 판정한다.
    """
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in VALIDATION_PROMOTION_ATTRS:
        return list(node.args)
    splatted = [keyword.value for keyword in node.keywords if keyword.arg is None]
    constructed = (_callable_name(node) or "").rpartition(".")[2]
    return splatted if splatted and constructed[:1].isupper() else []


def _is_validation_promotion(node: ast.Call, weak_names: frozenset[str]) -> bool:
    """이 task 의 **weak 파라미터를 대상으로 한** 승격인가.

    대상 이름을 보지 않으면 payload 와 무관한 ``Other.model_validate(CONST)`` 나
    ``Thread(**options)`` 만으로 면제되어 지표가 무비용으로 우회된다.
    """
    referenced = {
        child.id
        for target in _promotion_targets(node)
        for child in ast.walk(target)
        if isinstance(child, ast.Name)
    }
    return bool(referenced & weak_names)


def _weak_parameter_names(node: _FunctionNode) -> frozenset[str]:
    """task 인자가 dict/Any 이거나 **어노테이션이 없으면** 검증되지 않는 입력이다."""
    return frozenset(
        argument.arg
        for argument in _all_arguments(node)
        if argument.arg not in IMPLICIT_RECEIVER_ARG_NAMES
        and (argument.annotation is None or is_weak_annotation(argument.annotation))
    )


def _iter_executed_nodes(node: _FunctionNode) -> Iterator[ast.AST]:
    """중첩 정의(함수·lambda) 본문을 제외한, 이 함수가 실행하는 노드들.

    호출되는지 알 수 없는 중첩 정의 안의 승격은 payload 검증 증거가 못 된다.
    """
    stack: list[ast.AST] = list(node.body)
    while stack:
        current = stack.pop()
        yield current
        if not isinstance(current, DEFERRED_BODY_NODES):
            stack.extend(ast.iter_child_nodes(current))


def _is_unvalidated_dict_task(node: _FunctionNode) -> bool:
    if not _is_celery_task(node):
        return False
    weak_names = _weak_parameter_names(node)
    if not weak_names:
        return False
    return not any(
        isinstance(child, ast.Call) and _is_validation_promotion(child, weak_names)
        for child in _iter_executed_nodes(node)
    )


def _is_json_direct_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == JSON_MODULE_NAME
        and func.attr in JSON_DIRECT_CALL_ATTRS
    )


def _dotted_name(node: ast.expr) -> str | None:
    """``os.environ.get`` 처럼 점으로 이어진 이름 전체(중간이 이름이 아니면 None)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


def _is_environment_key(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value == ENVIRONMENT_ATTR_NAME


def _reads_environment(node: ast.expr) -> bool:
    """``settings.ENVIRONMENT`` · ``os.environ["ENVIRONMENT"]`` · ``os.getenv("…")``."""
    if isinstance(node, ast.Attribute):
        return node.attr == ENVIRONMENT_ATTR_NAME
    if isinstance(node, ast.Subscript) and _is_environment_key(node.slice):
        return _dotted_name(node.value) in ENVIRONMENT_MAPPING_NAMES
    if isinstance(node, ast.Call):
        # ``os.getenv(key="ENVIRONMENT")`` 처럼 keyword 로 넘겨도 같은 읽기다.
        arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
        return _dotted_name(node.func) in ENVIRONMENT_LOOKUP_CALL_NAMES and any(
            _is_environment_key(argument) for argument in arguments
        )
    return False


def _mentions_test_literal(node: ast.expr) -> bool:
    """``"test"`` 리터럴이거나 ``("test", "ci")`` 같은 리터럴 컨테이너의 원소인가."""
    if isinstance(node, ast.Constant):
        return node.value == TEST_ENVIRONMENT_LITERAL
    if isinstance(node, ENVIRONMENT_LITERAL_CONTAINERS):
        return any(_mentions_test_literal(element) for element in node.elts)
    return False


def _is_env_test_compare(node: ast.Compare) -> bool:
    operands = [node.left, *node.comparators]
    return any(_reads_environment(operand) for operand in operands) and any(
        _mentions_test_literal(operand) for operand in operands
    )


# --- 스캔 ------------------------------------------------------------------------
def _top_level_dir(relative_path: str) -> str:
    parts = PurePosixPath(relative_path).parts
    return parts[0] if parts else ""


def _count_functions_over(functions: Sequence[_FunctionNode], limit: int) -> int:
    return sum(1 for node in functions if _function_line_span(node) > limit)


def _count_json_direct_calls(tree: ast.Module, relative_path: str) -> int:
    if relative_path in JSON_CALL_ALLOWLIST:
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_json_direct_call(node)
    )


def _count_env_test_sniff(tree: ast.Module, relative_path: str) -> int:
    if _top_level_dir(relative_path) != ENV_SNIFF_SCAN_DIR:
        return 0
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare) and _is_env_test_compare(node)
    )


def file_loc_band(source: str) -> int:
    """500줄 초과 파일의 25줄 밴드(이하면 0). 501~525줄 → 21, 526~550줄 → 22."""
    loc = len(source.splitlines())
    if loc <= FILE_LOC_SOFT_LIMIT:
        return 0
    return math.ceil(loc / FILE_LOC_BAND_LINES)


def _parse_source(relative_path: str, source: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise RatchetScanError(f"{relative_path} 파싱 실패: {exc}") from exc


def scan_source(relative_path: str, source: str) -> FileMetrics:
    """한 파일의 소스 텍스트에서 지표를 센다(파일 I/O 없는 순수 함수)."""
    tree = _parse_source(relative_path, source)
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    return FileMetrics(
        functions_over_soft_limit=_count_functions_over(
            functions, FUNCTION_SOFT_LIMIT_LINES
        ),
        functions_over_hard_limit=_count_functions_over(
            functions, FUNCTION_HARD_LIMIT_LINES
        ),
        file_loc_band=file_loc_band(source),
        json_direct_calls=_count_json_direct_calls(tree, relative_path),
        dict_boundary_functions=sum(
            1 for node in functions if _has_weak_boundary(node)
        ),
        env_test_sniff=_count_env_test_sniff(tree, relative_path),
        unvalidated_dict_tasks=sum(
            1 for node in functions if _is_unvalidated_dict_task(node)
        ),
    )


def iter_target_files(root: Path) -> Iterator[Path]:
    for directory in TARGET_DIRS:
        for path in sorted((root / directory).rglob("*.py")):
            if EXCLUDED_PATH_PARTS.isdisjoint(path.parts):
                yield path


def _read_source(path: Path, relative_path: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RatchetScanError(f"{relative_path} 디코딩 실패: {exc}") from exc


def scan_repo(root: Path) -> RatchetReport:
    """대상 디렉터리를 스캔한다. 위반 0 인 파일은 리포트에 넣지 않는다."""
    report = RatchetReport()
    for path in iter_target_files(root):
        relative_path = path.relative_to(root).as_posix()
        metrics = scan_source(relative_path, _read_source(path, relative_path))
        if not metrics.is_clean():
            report.files[relative_path] = metrics
    return report
