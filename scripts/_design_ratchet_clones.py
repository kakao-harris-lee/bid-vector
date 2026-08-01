#!/usr/bin/env python3
"""설계 래칫의 중복 클론 측정 — 메커니컬 헬퍼의 구조 클론 탐지.

스캐너는 이미 measurement(``_design_ratchet_scan.py``) · contracts
(``_design_ratchet_contracts.py``) · CLI(``design_ratchet.py``) 로 나뉘어 있다. 클론
탐지를 네 번째 모듈로 뺀 것도 같은 이유다: 스캐너 자신이 스캔 대상이라 CLAUDE.md
§4.5-4 의 파일 500줄 한도를 지켜야 하는데, 403줄인 scan 모듈에 클론 탐지를 얹으면
한도를 넘겨 ``file_loc_band`` 를 스스로 올린다.

여기의 ``CloneSignature``/``CloneGroup`` 은 baseline JSON 으로 직렬화되지 않는 내부
자료구조라 contracts 모듈이 아니라 이 모듈에 둔다. contracts 의 경계는 "baseline 에
직렬화되는 계약 + 순수 비교"다.

"메커니컬 헬퍼" = 도메인 지식이 없는 함수. 데코레이터가 없고(라우터·celery task 배제),
DB 세션·네트워크·전역 설정에 닿지 않는다. 순수 계산뿐 아니라 얇은 파일 I/O 래퍼도
포함한다 — ``_write_json`` 류가 실제 중복의 최대 밀도 구간이기 때문이다.

정규화는 변수·인자명을 등장 순서 플레이스홀더로 치환해 "이름만 바꾼 복붙"을 같게 보되,
**상수 리터럴은 보존**해 ``round(x, 2)`` 와 ``round(x, 3)`` 을 다르게 본다(보수적 판정).
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.schemas._base import FrozenStrictModel  # noqa: E402

# --- 선언적 구성 (§4.5-1·3: 튜닝 표면을 함수 밖 데이터로) --------------------------
# 임계값 20 은 실측 고원의 하단이다: 20 과 25 에서 결과가 같고, 12 로 낮추면 우연의
# 일치가 섞이며 40 으로 올리면 진짜 중복을 놓친다. 20 에서의 실측 인벤토리는 교차파일
# 31그룹/71함수 · 동일파일 5그룹/10함수다(allowlist 적용 전).
CLONE_MIN_AST_NODES = 20

# 본문에 이 이름이 나타나면 도메인·인프라에 결합된 것으로 보고 후보에서 뺀다.
# ``open``/``Path``/``json``/``datetime`` 은 **일부러 넣지 않았다** — 얇은 메커니컬
# I/O 래퍼는 통합 대상이 맞다.
MECHANICAL_EXCLUDED_NAMES = frozenset(
    {
        "self",
        "cls",
        "db",
        "session",
        "Session",
        "engine",
        "select",
        "func",
        "requests",
        "httpx",
        "urllib",
        "socket",
        "subprocess",
        "sys",
        "shutil",
        "logger",
        "logging",
        "settings",
        "input",
        "print",
    }
)
MECHANICAL_EXCLUDED_ATTRS = frozenset(
    {"query", "commit", "add", "execute", "flush", "refresh", "scalar", "scalars"}
)

DIGEST_LENGTH = 12
PLACEHOLDER_PREFIX = "v"
NORMALIZED_FUNCTION_NAME = "f"

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class CloneSignature(FrozenStrictModel):
    """한 함수의 정규화 지문. 같은 ``digest`` 끼리가 구조 클론이다."""

    file: str
    function: str
    digest: str
    node_count: int

    @property
    def member(self) -> str:
        return f"{self.file}:{self.function}"


class _NameNormalizer(ast.NodeTransformer):
    """변수·인자명을 등장 순서 플레이스홀더로 치환한다."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def _placeholder(self, name: str) -> str:
        if name not in self._seen:
            self._seen[name] = f"{PLACEHOLDER_PREFIX}{len(self._seen)}"
        return self._seen[name]

    def visit_Name(self, node: ast.Name) -> ast.Name:
        renamed = ast.Name(id=self._placeholder(node.id), ctx=node.ctx)
        return ast.copy_location(renamed, node)

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._placeholder(node.arg)
        node.annotation = None
        return node


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def is_mechanical(node: _FunctionNode) -> bool:
    """도메인 지식 없는 헬퍼인가. 데코레이터가 있으면 프레임워크 보일러플레이트다."""
    if node.decorator_list:
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in MECHANICAL_EXCLUDED_NAMES:
            return False
        if isinstance(child, ast.Attribute) and child.attr in MECHANICAL_EXCLUDED_ATTRS:
            return False
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            return False
    return True


def _normalized_digest(node: _FunctionNode) -> str | None:
    """정규화 본문의 지문. 본문이 docstring 뿐이면 비교 대상이 아니라 ``None``."""
    clone = ast.parse(ast.unparse(node)).body[0]
    if not isinstance(clone, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    clone.name = NORMALIZED_FUNCTION_NAME
    clone.decorator_list = []
    clone.returns = None
    body = clone.body[1:] if clone.body and _is_docstring(clone.body[0]) else clone.body
    if not body:
        return None
    clone.body = body
    normalized = _NameNormalizer().visit(clone)
    ast.fix_missing_locations(normalized)
    encoded = ast.unparse(normalized).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:DIGEST_LENGTH]


def collect_clone_signatures(
    relative_path: str, tree: ast.Module
) -> list[CloneSignature]:
    """한 파일의 메커니컬 헬퍼 지문 목록(파일 I/O 없는 순수 함수)."""
    signatures: list[CloneSignature] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        node_count = sum(1 for _ in ast.walk(node))
        if node_count < CLONE_MIN_AST_NODES or not is_mechanical(node):
            continue
        digest = _normalized_digest(node)
        if digest is None:
            continue
        signatures.append(
            CloneSignature(
                file=relative_path,
                function=node.name,
                digest=digest,
                node_count=node_count,
            )
        )
    return signatures
