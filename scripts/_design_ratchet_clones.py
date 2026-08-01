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

크기 판정과 지문은 **같은 정규화 트리**에서 낸다. 기준이 어긋나면(원본에서 크기를 재고
정규화 후 지문을 내면) annotation·docstring 이 두꺼운 얇은 델리게이터가 임계값을 넘어
오탐이 된다. 같은 이유로 순수 파라미터 위임(``return callee(a, b, key=c)`` 한 줄)은
후보에서 아예 뺀다 — 그것이 §4.5-8 처방의 산출물이기 때문이다.
"""
# ruff: noqa: E402 - imports follow the sys.path bootstrap below.
from __future__ import annotations

import ast
import hashlib
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.schemas._base import FrozenStrictModel  # noqa: E402

# --- 선언적 구성 (§4.5-1·3: 튜닝 표면을 함수 밖 데이터로) --------------------------
# 노드 수는 **정규화 후** 형태에서 잰다(annotation·docstring·데코레이터 제거 후). 원본
# 에서 재면 타입힌트와 docstring 이 두꺼운 얇은 델리게이터가 임계값을 넘어 오탐이 되고,
# 그러면 §4.5-8 이 처방하는 "파라미터화 + 얇은 명명 래퍼" 리팩터가 지표를 줄이지 못한다
# (게이트가 자기 법을 처벌한다).
#
# 14 는 그 정규화 기준에서의 하한이다: `_average` 류 위임(정규화 12노드)과 유일한 우연
# 일치 그룹(`parse_license_limit_groups`|`scan_repo`, 12노드)을 배제하면서
# `_mapping_or_empty`(15) · CLONE_ALLOWLIST 두 항목(16) · `guidance_for`(18) 같은 실제
# 중복은 살린다. 임계값 스윕 실측은 설계 문서 §2.2 에 있다.
CLONE_MIN_AST_NODES = 14

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

# 구조는 같지만 통합하면 안 되는 그룹. 키는 **멤버 신원**("파일:함수" 정렬 후 "|" 결합)
# 이지 콘텐츠 해시가 아니다 — 해시로 잡으면 면제된 함수를 사소하게 고칠 때마다 키가
# 바뀌어 빌드가 깨진다. 멤버 신원이면 본문 수정은 통과하고, 복사본이 하나 더 생기면
# 멤버 집합이 달라져 면제가 풀린다. 근거는
# docs/superpowers/specs/2026-08-01-code-duplication-consolidation-design.md §5.3.
CLONE_ALLOWLIST: dict[str, str] = {
    "app/services/award_verification.py:_rate_to_fraction"
    "|app/services/base_amount_basis.py:normalize_winning_rate": (
        "금액 basis 도메인 — 두 함수의 basis 의미가 동일함을 증명하기 전에 합치면"
        " 예정가/기초금액 혼동을 코드에 굳힌다."
    ),
    "app/ai/predictors/registry.py:build_default_predictor_registry"
    "|scripts/backtest_price_predictors.py:build_registry": (
        "CLAUDE.md §4.7-2 팩토리/레지스트리 — 스크립트가 축소 레지스트리를 주입하는"
        " 테스트 격리 seam 이라 통합하면 격리가 사라진다."
    ),
}

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


def _is_pure_parameter_delegation(body: Sequence[ast.stmt]) -> bool:
    """본문이 ``return callee(a, b, key=c)`` **한 줄뿐인** 얇은 명명 델리게이터인가.

    §4.5-8 의 처방("파라미터화된 해석기 + 얇은 명명 래퍼")을 적용한 결과물이 바로 이
    형태다. 처방의 산출물을 중복으로 계수하면 게이트가 자기 법을 처벌하므로 배제한다.

    정의는 **좁게** 잡는다. "본문이 ``return <Call>`` 하나"까지 넓히면 한 줄로 쓴 진짜
    복붙까지 죽는다. 그래서 네 조건을 **전부** 만족할 때만 배제한다.

    1. 호출 대상이 bare ``ast.Name`` (``"".join(...)`` 같은 ``Attribute`` 는 해당 없음)
    2. 모든 위치 인자가 ``ast.Name`` 또는 ``ast.Constant``
    3. 모든 키워드 인자의 **값**이 ``ast.Name`` 또는 ``ast.Constant``
    4. ``*``/``**`` 언패킹이 없음 (``**`` 는 ``keyword.arg is None``, ``*`` 는
       ``ast.Starred`` 라 2번의 타입 검사에서 이미 걸린다)

    즉 인자가 중첩 호출(``title=title_line(t)``)이거나 comprehension
    (``tuple(x for x in ...)``)이면 위임이 아니라 로직이므로 후보로 남는다.
    """
    if len(body) != 1:
        return False
    statement = body[0]
    if not isinstance(statement, ast.Return):
        return False
    call = statement.value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return False
    if any(keyword.arg is None for keyword in call.keywords):
        return False
    operands = [*call.args, *(keyword.value for keyword in call.keywords)]
    return all(isinstance(operand, (ast.Name, ast.Constant)) for operand in operands)


def _normalized(node: _FunctionNode) -> tuple[str, int] | None:
    """정규화 본문의 ``(지문, 노드 수)``. 비교 대상이 아니면 ``None``.

    지문과 노드 수를 **같은 트리**에서 함께 낸다. 원본에서 노드 수를 재면 annotation ·
    docstring 이 두꺼운 얇은 델리게이터가 임계값을 넘어 오탐이 된다.

    ``None`` 인 경우는 둘이다 — 본문이 docstring 뿐이거나, 순수 파라미터 위임이다.
    """
    clone = ast.parse(ast.unparse(node)).body[0]
    if not isinstance(clone, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    clone.name = NORMALIZED_FUNCTION_NAME
    clone.decorator_list = []
    clone.returns = None
    body = clone.body[1:] if clone.body and _is_docstring(clone.body[0]) else clone.body
    if not body or _is_pure_parameter_delegation(body):
        return None
    clone.body = body
    normalized = _NameNormalizer().visit(clone)
    ast.fix_missing_locations(normalized)
    encoded = ast.unparse(normalized).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:DIGEST_LENGTH]
    return digest, sum(1 for _ in ast.walk(normalized))


def collect_clone_signatures(
    relative_path: str, tree: ast.Module
) -> list[CloneSignature]:
    """한 파일의 메커니컬 헬퍼 지문 목록(파일 I/O 없는 순수 함수)."""
    signatures: list[CloneSignature] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not is_mechanical(node):
            continue
        normalized = _normalized(node)
        if normalized is None:
            continue
        digest, node_count = normalized
        if node_count < CLONE_MIN_AST_NODES:
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


# --- 그룹핑 + allowlist (§4.5-2: 카운트/면제를 데이터 흐름으로) -------------------
class CloneGroup(FrozenStrictModel):
    """같은 지문을 공유하는 함수들. ``key`` 는 allowlist 조회에 쓰는 멤버 신원이다."""

    members: tuple[str, ...]
    files: tuple[str, ...]
    node_count: int

    @property
    def key(self) -> str:
        return "|".join(self.members)


def _buckets_by_digest(
    signatures: Iterable[CloneSignature],
) -> dict[str, list[CloneSignature]]:
    buckets: dict[str, list[CloneSignature]] = {}
    for signature in signatures:
        buckets.setdefault(signature.digest, []).append(signature)
    return buckets


def _build_group(members: Sequence[CloneSignature]) -> CloneGroup:
    return CloneGroup(
        members=tuple(sorted(member.member for member in members)),
        files=tuple(sorted({member.file for member in members})),
        node_count=max(member.node_count for member in members),
    )


def _cross_file_buckets(
    signatures: Iterable[CloneSignature],
) -> Iterator[list[CloneSignature]]:
    for members in _buckets_by_digest(signatures).values():
        if len({member.file for member in members}) > 1:
            yield members


def cross_file_clone_groups(
    signatures: Sequence[CloneSignature],
) -> list[CloneGroup]:
    """소속 파일이 2개 이상인 그룹. allowlist 등재분은 제외한다."""
    groups = [
        group
        for members in _cross_file_buckets(signatures)
        if (group := _build_group(members)).key not in CLONE_ALLOWLIST
    ]
    return sorted(groups, key=lambda group: group.key)


def count_cross_file_clones(signatures: Sequence[CloneSignature]) -> dict[str, int]:
    """파일별 교차 파일 클론 멤버 수."""
    counts: Counter[str] = Counter()
    for group in cross_file_clone_groups(signatures):
        counts.update(member.rsplit(":", 1)[0] for member in group.members)
    return dict(counts)


def count_local_clones(signatures: Sequence[CloneSignature]) -> int:
    """한 파일 안에서 서로 클론인 헬퍼 수(같은 파일의 지문만 넘길 것)."""
    return sum(
        len(members)
        for members in _buckets_by_digest(signatures).values()
        if len(members) > 1
    )


def unused_allowlist_keys(signatures: Sequence[CloneSignature]) -> list[str]:
    """현재 스캔에 더 이상 존재하지 않는 allowlist 항목.

    비워 두면 면제 범위가 조용히 넓어지므로 호출부가 실패시킨다.
    """
    live = {_build_group(members).key for members in _cross_file_buckets(signatures)}
    return sorted(set(CLONE_ALLOWLIST) - live)
