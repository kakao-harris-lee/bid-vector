#!/usr/bin/env python3
"""설계 래칫의 측정 코어 — AST 스캔 + 데이터 계약 + 순수 비교.

``scripts/design_ratchet.py`` (CLI · baseline 영속화 · 리포트 출력) 에서 쓰는 계측
부분만 분리한 모듈이다. 파일/함수 크기 한도(CLAUDE.md §4.5-4)를 스캐너 자신도 지켜야
하므로 "측정"과 "표현/영속화"를 책임 단위로 나눴다.

여기에는 baseline 이라는 개념이 없다(파일 I/O 는 소스 읽기뿐). 비교·집계는 모두 순수
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

from pydantic import Field  # noqa: E402

from app.schemas._base import StrictModel  # noqa: E402

# --- 선언적 구성 (§4.5-1: 임계값·대상·예외는 함수 밖에) ----------------------------
TARGET_DIRS: tuple[str, ...] = ("app", "scripts")

FUNCTION_SOFT_LIMIT_LINES = 50
FUNCTION_HARD_LIMIT_LINES = 100
FILE_LOC_SOFT_LIMIT = 500
# 500줄 초과 파일은 LOC 자체가 아니라 25줄 밴드로 센다. 한 줄 추가하는 버그픽스가
# baseline 상승을 요구하지 않게 하되(잡음 제거), 파일이 계속 자라면 밴드가 올라간다.
FILE_LOC_BAND_LINES = 25
FILE_LOC_METRIC = "file_loc_band"

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

CELERY_TASK_DECORATORS = frozenset({"celery_app.task", "shared_task"})
VALIDATION_PROMOTION_ATTRS = frozenset({"model_validate", "model_validate_json"})
# celery ``bind=True`` 의 self 와 메서드 수신자는 payload 가 아니라서 제외한다.
IMPLICIT_RECEIVER_ARG_NAMES = frozenset({"self", "cls"})

EXCLUDED_PATH_PARTS = frozenset({"__pycache__"})

# 밴드 숫자만 보면 무슨 단위인지 알 수 없으므로 위반 메시지에 환산을 붙인다.
FILE_LOC_BAND_HINT = (
    f"(밴드≈{FILE_LOC_BAND_LINES}줄, {FILE_LOC_SOFT_LIMIT}줄 초과 진입 시"
    f" {FILE_LOC_SOFT_LIMIT // FILE_LOC_BAND_LINES + 1}부터)"
)
METRIC_DESCRIBE_SUFFIXES = {FILE_LOC_METRIC: FILE_LOC_BAND_HINT}

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class RatchetScanError(RuntimeError):
    """스캔 대상 파일을 해석할 수 없음.

    조용히 건너뛰면 그 파일의 위반이 0 으로 보고되어 "삭제 = 통과" 경로로 사라지므로
    래칫이 샌다. 그래서 침묵하지 않고 크게 실패한다.
    """


# --- 데이터 계약 (§4.7: 원시 dict 대신 검증되는 모델) -----------------------------
class FileMetrics(StrictModel):
    """한 파일의 규율 위반 카운트. 0 은 "해당 위반 없음"을 뜻한다."""

    functions_over_soft_limit: int = 0
    functions_over_hard_limit: int = 0
    file_loc_band: int = 0
    json_direct_calls: int = 0
    dict_boundary_functions: int = 0
    env_test_sniff: int = 0
    unvalidated_dict_tasks: int = 0

    def count(self, metric: str) -> int:
        return int(getattr(self, metric))

    def is_clean(self) -> bool:
        return all(self.count(metric) == 0 for metric in METRIC_NAMES)


METRIC_NAMES: tuple[str, ...] = tuple(FileMetrics.model_fields)


class RatchetReport(StrictModel):
    """파일 경로(posix 상대경로) → 위반 카운트."""

    files: dict[str, FileMetrics] = Field(default_factory=dict)

    def metrics_for(self, path: str) -> FileMetrics:
        return self.files.get(path) or FileMetrics()

    def totals(self) -> FileMetrics:
        summed = {
            metric: sum(metrics.count(metric) for metrics in self.files.values())
            for metric in METRIC_NAMES
        }
        return FileMetrics(**summed)


class RatchetViolation(StrictModel):
    """baseline 대비 악화된 (파일, 지표) 한 건."""

    metric: str
    file: str
    baseline: int
    current: int

    def describe(self) -> str:
        line = f"  {self.metric}: {self.file} {self.baseline} -> {self.current}"
        suffix = METRIC_DESCRIBE_SUFFIXES.get(self.metric, "")
        return f"{line} {suffix}" if suffix else line


# --- 순수 비교 (I/O 없음) ---------------------------------------------------------
def compare_reports(
    baseline: RatchetReport, current: RatchetReport
) -> list[RatchetViolation]:
    """current 가 baseline 을 초과한 (파일, 지표) 목록. 감소·삭제는 통과."""
    violations: list[RatchetViolation] = []
    for path in sorted(current.files):
        before = baseline.metrics_for(path)
        after = current.metrics_for(path)
        violations.extend(_violations_for_file(path, before, after))
    return violations


def _violations_for_file(
    path: str, baseline: FileMetrics, current: FileMetrics
) -> list[RatchetViolation]:
    return [
        RatchetViolation(
            metric=metric,
            file=path,
            baseline=baseline.count(metric),
            current=current.count(metric),
        )
        for metric in METRIC_NAMES
        if current.count(metric) > baseline.count(metric)
    ]


def count_improvements(baseline: RatchetReport, current: RatchetReport) -> FileMetrics:
    """지표별 감소 총량(양수로). 파일 삭제로 사라진 카운트도 포함하는 순수 함수."""
    reduced = dict.fromkeys(METRIC_NAMES, 0)
    for path in set(baseline.files) | set(current.files):
        before = baseline.metrics_for(path)
        after = current.metrics_for(path)
        for metric in METRIC_NAMES:
            delta = before.count(metric) - after.count(metric)
            if delta > 0:
                reduced[metric] += delta
    return FileMetrics(**reduced)


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


def _is_validation_promotion(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in VALIDATION_PROMOTION_ATTRS:
        return True
    if not any(keyword.arg is None for keyword in node.keywords):
        return False
    name = _callable_name(node)
    return bool(name) and name[0].isupper()


def _has_unvalidated_task_input(node: _FunctionNode) -> bool:
    """task 인자가 dict/Any 이거나 **어노테이션이 없으면** 검증되지 않는 입력이다."""
    payload_arguments = [
        argument
        for argument in _all_arguments(node)
        if argument.arg not in IMPLICIT_RECEIVER_ARG_NAMES
    ]
    return any(
        argument.annotation is None or is_weak_annotation(argument.annotation)
        for argument in payload_arguments
    )


def _is_unvalidated_dict_task(node: _FunctionNode) -> bool:
    if not _is_celery_task(node):
        return False
    if not _has_unvalidated_task_input(node):
        return False
    calls = (child for child in ast.walk(node) if isinstance(child, ast.Call))
    return not any(_is_validation_promotion(call) for call in calls)


def _is_json_direct_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == JSON_MODULE_NAME
        and func.attr in JSON_DIRECT_CALL_ATTRS
    )


def _is_env_test_compare(node: ast.Compare) -> bool:
    operands = [node.left, *node.comparators]
    touches_environment = any(
        isinstance(operand, ast.Attribute) and operand.attr == ENVIRONMENT_ATTR_NAME
        for operand in operands
    )
    compares_test = any(
        isinstance(operand, ast.Constant) and operand.value == TEST_ENVIRONMENT_LITERAL
        for operand in operands
    )
    return touches_environment and compares_test


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
